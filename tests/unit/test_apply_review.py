"""Stage 4 — applying a review and regenerating from it."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from osm_scenario.acquisition import acquire_osm
from osm_scenario.apply_review import (
    ApplyReviewError,
    _comparison,
    _question_key,
    _turn_lanes_tag,
    apply_review,
)
from osm_scenario.config import ConverterConfig
from osm_scenario.generation import generate_lane_model
from osm_scenario.lane_model import (
    GenerationMetadata,
    LaneFeature,
    Point2D,
    PreliminaryLaneModel,
    ReviewFinding,
)
from osm_scenario.normalization import normalize_workspace

JUNCTION = Path(__file__).parents[1] / "fixtures" / "osm" / "junction.osm"
# The same T junction, with one arm made a two-lane one-way tagged for a turn the
# junction does not offer. Kept separate so the plain fixture stays free of the conflict.
TURN_LANES_CONFLICT = (
    Path(__file__).parents[1] / "fixtures" / "osm" / "turn-lanes-conflict.osm"
)
SIGNALS = Path(__file__).parents[1] / "fixtures" / "osm" / "signals.osm"
CONFIG = ConverterConfig(config_version=1)


def _workspace(tmp_path: Path) -> Path:
    """A generated workspace over the T junction fixture, ready to review."""
    workspace = tmp_path / "workspace"
    acquire_osm(workspace=workspace, driving_side="left", osm_file=JUNCTION)
    normalize_workspace(workspace=workspace, config=CONFIG)
    generate_lane_model(workspace=workspace, config=CONFIG)
    return workspace


def _model(workspace: Path) -> PreliminaryLaneModel:
    return PreliminaryLaneModel.model_validate_json(
        (workspace / "lane-model" / "preliminary.json").read_bytes()
    )


def _submission(
    workspace: Path,
    *,
    connector_verdicts: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A ready review: blockers decided, warnings ignored.

    `connector_verdicts` maps a connector id to "accepted" or "removed"; anything not
    named is removed, which is the common case and keeps each test to its own subject.
    """
    model = _model(workspace)
    verdicts = connector_verdicts or {}
    decisions = []
    for finding in model.findings:
        decision: dict[str, Any] = {
            "finding_id": finding.identifier,
            "rule": finding.rule,
            "evidence_checksum": finding.evidence_checksum,
            "decided_at": "2026-08-10T00:00:00.000Z",
            "source_type": finding.source_type,
            "source_ids": list(finding.source_ids),
        }
        if finding.rule == "ambiguous_connector":
            connector = finding.affected_feature_ids[0]
            if verdicts.get(connector, "removed") == "accepted":
                decision["status"] = "accepted"
            else:
                decision["status"] = "overridden"
                decision["value"] = {"accepted": False}
        elif finding.severity == "blocker":
            decision["status"] = "accepted"
        else:
            decision["status"] = "ignored"
        decision.update((overrides or {}).get(finding.identifier, {}))
        decisions.append(decision)

    blockers = [f for f in model.findings if f.severity == "blocker"]
    return {
        "submission_version": 3,
        "exported_at": "2026-08-10T00:00:00.000Z",
        "identity": {
            "workspace": workspace.name,
            "source_checksum": model.metadata.source_checksum,
            "generation_fingerprint": model.metadata.generation_fingerprint,
            "generator_version": model.metadata.generator_version,
            "lane_model_schema_version": model.metadata.lane_model_schema_version,
            "configuration_checksum": model.metadata.configuration_checksum,
            "generated_at": "2026-08-10T00:00:00+00:00",
        },
        "decisions": decisions,
        "readiness": {
            "total": len(model.findings),
            "resolved": len(blockers),
            "ignored": len(model.findings) - len(blockers),
            "blockers_total": len(blockers),
            "blockers_unresolved": 0,
            "ready": True,
        },
    }


def _write(workspace: Path, submission: dict[str, Any]) -> Path:
    path = workspace / "review.json"
    path.write_text(json.dumps(submission, indent=2) + "\n", encoding="utf-8")
    return path


def _apply(workspace: Path, submission: dict[str, Any]) -> Path:
    return apply_review(
        workspace=workspace, submission=_write(workspace, submission), config=CONFIG
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _way_tags(osm: Path, way_id: str) -> dict[str, str]:
    root = ET.parse(osm).getroot()
    way = next(w for w in root.findall("way") if w.get("id") == way_id)
    return {t.get("k") or "": t.get("v") or "" for t in way.findall("tag")}


def _reviewed(workspace: Path) -> PreliminaryLaneModel:
    return PreliminaryLaneModel.model_validate_json(
        (workspace / "lane-model" / "reviewed.json").read_bytes()
    )


def _connector_ids(workspace: Path) -> list[str]:
    return [
        finding.affected_feature_ids[0]
        for finding in _model(workspace).findings
        if finding.rule == "ambiguous_connector"
    ]


def _lane(identifier: str, *, exits: list[str]) -> LaneFeature:
    """The smallest lane the comparison looks at: an identity and a way out."""
    return LaneFeature(
        identifier=identifier,
        source_way_ids=["200"],
        source_edge=["1", "2", "0"],
        lane_index=0,
        lane_count=1,
        direction="forward",
        road_class="residential",
        width_m=3.5,
        speed_limit_kph=50.0,
        centerline=[Point2D(x=0.0, y=0.0), Point2D(x=1.0, y=0.0)],
        polygon=[Point2D(x=0.0, y=0.0)],
        boundaries=[],
        exit_lanes=exits,
    )


def _lane_model(lanes: list[LaneFeature]) -> PreliminaryLaneModel:
    return PreliminaryLaneModel(
        metadata=GenerationMetadata(
            generator_version="test",
            lane_model_schema_version=1,
            source_checksum="source",
            projected_graph_checksum="graph",
            configuration_checksum="config",
            generation_fingerprint="fingerprint",
            coordinate_system_wkt="",
        ),
        lanes=lanes,
    )


def _compare(before: PreliminaryLaneModel, after: PreliminaryLaneModel) -> dict[str, Any]:
    return _comparison(
        preliminary=before,
        reviewed=after,
        applied={
            "forbidden_connector_ids": [],
            "activated_connector_ids": [],
            "left_open_as_not_applicable": [],
        },
    )


# --- what the review actually does -------------------------------------------------


def test_a_removed_movement_is_forbidden_and_a_kept_one_is_active(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    keep, *remove = _connector_ids(workspace)
    _apply(workspace, _submission(workspace, connector_verdicts={keep: "accepted"}))

    by_id = {c.identifier: c for c in _reviewed(workspace).connectors}
    assert by_id[keep].status == "active"
    assert [by_id[c].status for c in remove] == ["forbidden"] * len(remove)
    # Every question the reviewer answered stops being asked.
    assert not [c for c in by_id.values() if c.status == "review_required"]
    assert not [f for f in _reviewed(workspace).findings if f.rule == "ambiguous_connector"]


def test_an_activated_movement_is_wired_into_the_lane_graph(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    keep = _connector_ids(workspace)[0]
    _apply(workspace, _submission(workspace, connector_verdicts={keep: "accepted"}))

    reviewed = _reviewed(workspace)
    connector = next(c for c in reviewed.connectors if c.identifier == keep)
    lanes = {lane.identifier: lane for lane in reviewed.lanes}
    # Promoting to active is not just a label: the movement has to appear on both lanes,
    # or nothing downstream can route through it.
    assert keep in lanes[connector.from_lane_id].exit_lanes
    assert keep in lanes[connector.to_lane_id].entry_lanes


def test_a_forbidden_movement_leaves_the_lane_graph(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    removed = _connector_ids(workspace)[0]
    _apply(workspace, _submission(workspace))

    reviewed = _reviewed(workspace)
    assert not any(removed in lane.exit_lanes for lane in reviewed.lanes)
    assert not any(removed in lane.entry_lanes for lane in reviewed.lanes)


def test_the_reviewed_model_is_regenerated_not_patched(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    before = (workspace / "lane-model" / "preliminary.json").read_bytes()
    _apply(workspace, _submission(workspace))

    assert (workspace / "lane-model" / "preliminary.json").read_bytes() == before
    reviewed = _reviewed(workspace)
    preliminary = _model(workspace)
    # Nothing in this review changes a tag, so the geometry must come back unchanged —
    # same lanes, same connectors, differing only in what the reviewer decided.
    assert {lane.identifier for lane in reviewed.lanes} == {
        lane.identifier for lane in preliminary.lanes
    }
    assert {c.identifier for c in reviewed.connectors} == {
        c.identifier for c in preliminary.connectors
    }


# --- the source is evidence ---------------------------------------------------------


def test_the_reviewed_model_applies_the_same_road_selection(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _apply(workspace, _submission(workspace))

    # Stage 1A filters through public-driving-v1 before handing its graph on. Rebuilding
    # from OSM without repeating that readmits every excluded way — on junction-1 it turned
    # 196 edges into 561 and produced a reviewed model of a network nobody had reviewed.
    ways = {way for lane in _reviewed(workspace).lanes for way in lane.source_way_ids}
    assert ways == {"200", "201", "202"}
    assert not ways & {"300", "301"}, "an excluded way came back into the reviewed model"
    selection = json.loads((workspace / "source" / "manifest.json").read_text())["stage_4"][
        "road_selection"
    ]
    assert selection["policy_id"] == "public-driving-v1"
    assert selection["excluded_by_reason"] == {"highway=footway": 1, "access=private": 1}


def test_the_source_osm_is_never_written(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = workspace / "source" / "map.osm"
    before = _sha(source)
    _apply(workspace, _submission(workspace))
    assert _sha(source) == before


def test_the_reviewed_osm_is_a_faithful_copy_when_no_override_writes_a_tag(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _apply(workspace, _submission(workspace))
    assert (workspace / "review" / "reviewed.osm").read_bytes() == (
        workspace / "source" / "map.osm"
    ).read_bytes()
    applied = json.loads((workspace / "review" / "applied-decisions.json").read_text())
    assert applied["materialised_osm_tags"] == {}


def _lane_count_override(
    workspace: Path, *, count: int, direction: str = "forward"
) -> tuple[dict[str, Any], str]:
    """A review that sets one way's lane count, and the way it lands on."""
    finding = next(
        f
        for f in _model(workspace).findings
        if f.rule == "lane_count_inference"
        and isinstance(f.proposed_value, dict)
        and f.proposed_value.get("direction") == direction
    )
    submission = _submission(
        workspace,
        overrides={
            finding.identifier: {"status": "overridden", "value": {"lane_count": count}}
        },
    )
    return submission, finding.source_ids[0]


def test_a_lane_count_override_reaches_the_regenerated_model(tmp_path: Path) -> None:
    # The point of the whole path: not that the tag appears in the file, but that the
    # generator reads it back and the reviewed model holds the count the reviewer asked
    # for. A tag written but not read would be a review that looks applied and is not.
    workspace = _workspace(tmp_path)
    submission, way_id = _lane_count_override(workspace, count=3)

    def forward_lanes(model: PreliminaryLaneModel) -> set[int]:
        return {
            lane.lane_count
            for lane in model.lanes
            if way_id in lane.source_way_ids and lane.direction == "forward"
        }

    assert forward_lanes(_model(workspace)) != {3}
    _apply(workspace, submission)
    assert forward_lanes(_reviewed(workspace)) == {3}

    # The tag is whichever of the two the way's own oneway-ness calls for, and both are
    # branches `_directional_lane_count` reads as explicit rather than inferring from.
    written = json.loads((workspace / "review" / "applied-decisions.json").read_text())[
        "materialised_osm_tags"
    ][way_id]
    assert written in ({"lanes:forward": "3"}, {"lanes": "3"})
    assert _way_tags(workspace / "review" / "reviewed.osm", way_id) | written == _way_tags(
        workspace / "review" / "reviewed.osm", way_id
    )
    comparison = json.loads((workspace / "reports" / "reviewed-comparison.json").read_text())
    assert comparison["ways_retagged"][way_id] == written


def test_an_overridden_lane_count_stops_being_inferred(tmp_path: Path) -> None:
    # An explicit count is evidence, so the finding that asked for one is answered rather
    # than asked again. The other direction's finding is untouched.
    workspace = _workspace(tmp_path)
    submission, way_id = _lane_count_override(workspace, count=2)
    _apply(workspace, submission)

    def directions(model: PreliminaryLaneModel) -> set[str]:
        return {
            str(f.proposed_value["direction"])
            for f in model.findings
            if f.rule == "lane_count_inference"
            and way_id in f.source_ids
            and isinstance(f.proposed_value, dict)
        }

    assert "forward" in directions(_model(workspace))
    assert "forward" not in directions(_reviewed(workspace))


def test_a_decision_still_answers_its_finding_after_regeneration_renames_it(
    tmp_path: Path,
) -> None:
    """The test that matters for Stage 5, and the one a stable-identifier fixture cannot see.

    A finding's identifier covers its `affected_feature_ids`, so re-laning a way renames
    every finding about it. A decision names the *preliminary* identifier. Join those on the
    identifier and a fully reviewed map reports unresolved blockers the moment anyone
    overrides a lane count - which is exactly what Stage 5 must not do.
    """
    workspace = _workspace(tmp_path)
    before = {_question_key(f): f for f in _model(workspace).findings}
    submission, way_id = _lane_count_override(workspace, count=3)
    _apply(workspace, submission)

    after = {_question_key(f): f for f in _reviewed(workspace).findings}
    # The way's `speed_default` and `lane_width_default` cover its whole lane list, so
    # tripling the forward lanes renames both while the question each asks is untouched.
    renamed = {
        after[key].identifier: before[key].identifier
        for key in before.keys() & after.keys()
        if before[key].identifier != after[key].identifier
    }
    assert renamed, "nothing was renamed, so this fixture cannot prove anything about the join"
    assert all(way_id in f.source_ids for f in after.values() if f.identifier in renamed)

    decisions = json.loads((workspace / "reports" / "reviewed-comparison.json").read_text())[
        "finding_decisions"
    ]
    for new_id, old_id in renamed.items():
        assert decisions[new_id]["state"] == "decided"
        # The join followed the question, not the name. An identifier join would have
        # missed exactly these and called a finished review unresolved.
        assert decisions[new_id]["answers_finding_id"] == old_id

    # Nothing the reviewer saw is left looking unreviewed. What is undecided is exactly
    # what regeneration asked for the first time - those are honestly new questions, and
    # reporting them as decided would be the more dangerous error.
    undecided = {i for i, entry in decisions.items() if entry["state"] == "undecided"}
    assert undecided == {after[key].identifier for key in after.keys() - before.keys()}


def test_the_source_osm_is_untouched_even_when_a_tag_is_written(tmp_path: Path) -> None:
    # The whole reason the tag goes to a derived file: correcting a lane count by editing
    # source/map.osm would move its checksum and invalidate every decision in the review.
    workspace = _workspace(tmp_path)
    submission, _ = _lane_count_override(workspace, count=2)
    before = _sha(workspace / "source" / "map.osm")
    _apply(workspace, submission)
    assert _sha(workspace / "source" / "map.osm") == before


def test_a_lane_count_override_in_the_old_three_field_shape_is_refused(
    tmp_path: Path,
) -> None:
    # Stage 3 offered `lanes` / `lanes:forward` / `lanes:backward` before this rule was
    # implemented. Which of the three the reviewer meant is not recoverable, so a file
    # carrying that shape stops the run rather than having one of them guessed at.
    workspace = _workspace(tmp_path)
    finding = next(f for f in _model(workspace).findings if f.rule == "lane_count_inference")
    submission = _submission(
        workspace,
        overrides={
            finding.identifier: {
                "status": "overridden",
                "value": {"lanes": 2, "lanes_forward": 2},
            }
        },
    )
    with pytest.raises(ApplyReviewError, match="needs"):
        _apply(workspace, submission)


def test_a_lane_count_override_outside_the_permitted_range_is_refused(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    submission, _ = _lane_count_override(workspace, count=99)
    with pytest.raises(ApplyReviewError, match="permitted range"):
        _apply(workspace, submission)


def _conflict_workspace(tmp_path: Path) -> Path:
    """A workspace whose approach is tagged for a turn its junction does not offer.

    Way `200` is a two-lane one-way arm of the T tagged `turn:lanes=left|left`, and the
    junction offers only a through and a right. Both its lanes therefore lose every
    movement to the tag, and both get one back from `_stranded_permission_fallback` - the
    shape `turn_permission_geometry_conflict` exists to report.
    """
    workspace = tmp_path / "conflict"
    acquire_osm(workspace=workspace, driving_side="left", osm_file=TURN_LANES_CONFLICT)
    normalize_workspace(workspace=workspace, config=CONFIG)
    generate_lane_model(workspace=workspace, config=CONFIG)
    return workspace


def _conflict_finding(workspace: Path, *, lane_index: int) -> ReviewFinding:
    """The conflict finding whose *approach* lane sits at `lane_index`."""
    model = _model(workspace)
    lanes = {lane.identifier: lane for lane in model.lanes}
    return next(
        f
        for f in model.findings
        if f.rule == "turn_permission_geometry_conflict"
        and lanes[f.affected_feature_ids[0]].lane_index == lane_index
    )


def _turn_permissions(model: PreliminaryLaneModel, way_id: str) -> dict[int, list[str]]:
    return {
        lane.lane_index: list(lane.turn_permissions)
        for lane in model.lanes
        if way_id in lane.source_way_ids and lane.direction == "forward"
    }


def test_a_turn_lanes_override_reaches_the_regenerated_model(tmp_path: Path) -> None:
    # The same bar the lane count had to clear: not that the tag appears in the file, but
    # that the generator reads it back, the movement is reclassified, and the conflict the
    # finding reported is gone from the reviewed model.
    workspace = _conflict_workspace(tmp_path)
    # Each lane is given the movement it can actually make: the offside lane turns right,
    # the nearside one carries straight on.
    submission = _submission(
        workspace,
        overrides={
            _conflict_finding(workspace, lane_index=0).identifier: {
                "status": "overridden",
                "value": {"movement": "right"},
            },
            _conflict_finding(workspace, lane_index=1).identifier: {
                "status": "overridden",
                "value": {"movement": "through"},
            },
        },
    )
    assert _turn_permissions(_model(workspace), "200") == {0: ["left"], 1: ["left"]}

    _apply(workspace, submission)

    assert _way_tags(workspace / "review" / "reviewed.osm", "200")["turn:lanes"] == (
        "through|right"
    )
    reviewed = _reviewed(workspace)
    assert _turn_permissions(reviewed, "200") == {0: ["right"], 1: ["through"]}
    assert not [f for f in reviewed.findings if f.rule == "turn_permission_geometry_conflict"]
    comparison = json.loads((workspace / "reports" / "reviewed-comparison.json").read_text())
    assert comparison["findings_still_open"] == []
    # Nothing lost its way out: the point of the fallback is that the lane keeps an exit,
    # and correcting the tag must not take it back.
    assert comparison["lanes_left_without_an_exit"] == []


def test_a_turn_lanes_override_writes_the_slot_its_own_lane_occupies(
    tmp_path: Path,
) -> None:
    """The kerbside-first inversion, which is the whole difficulty of this tag.

    Under left-hand traffic `turn:lanes` is written kerbside first, so the *offside* lane
    `idx0` is the **last** slot. Overriding it must move the right-hand end of the value
    and leave the nearside lane's slot exactly as the source had it - a naive
    `slots[lane_index]` would silently retag the other lane instead, and both lanes
    reading back a plausible value is what makes that kind of slip survive review.
    """
    workspace = _conflict_workspace(tmp_path)
    submission = _submission(
        workspace,
        overrides={
            _conflict_finding(workspace, lane_index=0).identifier: {
                "status": "overridden",
                "value": {"movement": "right"},
            }
        },
    )
    _apply(workspace, submission)

    assert _way_tags(workspace / "review" / "reviewed.osm", "200")["turn:lanes"] == "left|right"
    assert _turn_permissions(_reviewed(workspace), "200") == {0: ["right"], 1: ["left"]}


def test_an_override_that_restates_the_tag_is_refused(tmp_path: Path) -> None:
    # Answering "which one is right?" with the value already in the file writes nothing,
    # regenerates an identical model and brings the same blocker back. That reads as an
    # override that was applied and did nothing, so it stops the run instead.
    workspace = _conflict_workspace(tmp_path)
    submission = _submission(
        workspace,
        overrides={
            _conflict_finding(workspace, lane_index=0).identifier: {
                "status": "overridden",
                "value": {"movement": "left"},
            }
        },
    )
    with pytest.raises(ApplyReviewError, match="already says"):
        _apply(workspace, submission)


def test_an_override_naming_a_movement_stage_3_cannot_offer_is_refused(
    tmp_path: Path,
) -> None:
    # `none` is valid OSM and is exactly the value that would strand the lane again:
    # `movement_matches` is false against it for every movement.
    workspace = _conflict_workspace(tmp_path)
    submission = _submission(
        workspace,
        overrides={
            _conflict_finding(workspace, lane_index=0).identifier: {
                "status": "overridden",
                "value": {"movement": "none"},
            }
        },
    )
    with pytest.raises(ApplyReviewError, match="needs"):
        _apply(workspace, submission)


def test_an_override_that_does_not_resolve_the_conflict_stays_open(tmp_path: Path) -> None:
    """`satisfied` is about the map, not the reviewer, and this is where the two part.

    The nearside lane can only carry straight on. Told it turns right, the reviewed model
    rejects that movement too and asks the same question again. A decision exists, so
    nothing here is undecided - but the map does not agree with it, and Stage 5 must still
    see the blocker.
    """
    workspace = _conflict_workspace(tmp_path)
    finding = _conflict_finding(workspace, lane_index=1)
    submission = _submission(
        workspace,
        overrides={
            finding.identifier: {"status": "overridden", "value": {"movement": "right"}}
        },
    )
    _apply(workspace, submission)

    comparison = json.loads((workspace / "reports" / "reviewed-comparison.json").read_text())
    still_open = comparison["findings_still_open"]
    assert still_open, "an override the map does not reflect must not read as resolved"
    decided = comparison["finding_decisions"][still_open[0]]
    assert decided["state"] == "decided"
    assert decided["satisfied"] is False


def test_an_accepted_conflict_reappears_without_being_open(tmp_path: Path) -> None:
    # Accepting means "keep the restored movement", which leaves the OSM alone, so the
    # same question comes back on every regeneration. That is not unreviewed work.
    workspace = _conflict_workspace(tmp_path)
    _apply(workspace, _submission(workspace))

    reviewed = _reviewed(workspace)
    assert [f for f in reviewed.findings if f.rule == "turn_permission_geometry_conflict"]
    comparison = json.loads((workspace / "reports" / "reviewed-comparison.json").read_text())
    assert comparison["findings_still_open"] == []


def test_the_turn_lanes_key_written_is_the_one_generation_reads() -> None:
    """`_turn_permissions` reads `turn:lanes:<direction>` before the bare `turn:lanes`.

    Writing the bare key while a directional one exists would leave the override in the
    file and unread - the silent misapplication this stage refuses everywhere else.
    """
    wanted = {"lane_count": 2, "slots": {"1": "right"}}
    oneway = {"oneway": "yes", "turn:lanes": "left|left"}
    assert _turn_lanes_tag("200", oneway, "forward", wanted) == ("turn:lanes", "left|right")

    directional = {"turn:lanes:forward": "left|left", "turn:lanes": "through|through"}
    assert _turn_lanes_tag("200", directional, "forward", wanted) == (
        "turn:lanes:forward",
        "left|right",
    )


def test_a_bare_turn_lanes_on_a_two_way_way_is_refused() -> None:
    # A bare value on a bidirectional way describes lanes in node order, covering both
    # directions at once. Writing the directional key beside it leaves two tags
    # disagreeing about the other direction; rewriting the bare one answers for a
    # direction nobody reviewed.
    tags = {"oneway": "no", "turn:lanes": "left|left"}
    with pytest.raises(ApplyReviewError, match="whole carriageway"):
        _turn_lanes_tag("200", tags, "forward", {"lane_count": 2, "slots": {"1": "right"}})


def test_a_turn_lanes_value_that_does_not_describe_the_lanes_is_refused() -> None:
    # Three slots for two lanes: which lane the reviewer's movement belongs to is not
    # recoverable, so the run stops rather than placing it by guess.
    tags = {"oneway": "yes", "turn:lanes": "left|left|left"}
    with pytest.raises(ApplyReviewError, match="slot"):
        _turn_lanes_tag("200", tags, "forward", {"lane_count": 2, "slots": {"1": "right"}})


def test_applied_decisions_do_not_collide_with_the_stage_3_export(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _apply(workspace, _submission(workspace))
    # The spec names this output `review/review.json`, one path segment from the
    # hand-made export that is its input. It must not be written under that name.
    assert (workspace / "review" / "applied-decisions.json").is_file()
    assert not (workspace / "review" / "review.json").exists()


# --- reproducibility ----------------------------------------------------------------


def test_the_reviewed_model_is_keyed_on_the_reviewed_osm(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _apply(workspace, _submission(workspace))
    manifest = json.loads((workspace / "source" / "manifest.json").read_text())
    graphml = workspace / manifest["stage_1b"]["artifacts"]["projected_graphml"]["path"]

    # Asserted as a property rather than by running twice and diffing: osmnx stamps a
    # build timestamp into GraphML, so a checksum taken from a rebuilt graph file drifts
    # on every run — but two runs in the same second would agree and the test would pass.
    assert _reviewed(workspace).metadata.projected_graph_checksum == _sha(
        workspace / "review" / "reviewed.osm"
    )
    assert _reviewed(workspace).metadata.projected_graph_checksum != _sha(graphml)


def test_applying_the_same_review_twice_changes_nothing(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    submission = _submission(workspace)
    _apply(workspace, submission)
    first = {
        name: (workspace / name).read_bytes()
        for name in (
            "lane-model/reviewed.json",
            "review/reviewed.osm",
            "reports/reviewed-comparison.json",
            "reports/reviewed-comparison.md",
        )
    }
    _apply(workspace, submission)
    for name, contents in first.items():
        assert (workspace / name).read_bytes() == contents, name


# --- the comparison -----------------------------------------------------------------


def test_the_comparison_reports_what_the_review_resolved(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _apply(workspace, _submission(workspace))
    comparison = json.loads((workspace / "reports" / "reviewed-comparison.json").read_text())

    assert comparison["connector_status"]["review_required"]["reviewed"] == 0
    assert comparison["findings_the_review_resolved"]["by_rule"]["ambiguous_connector"] == {
        "blocker": 3,
        "warning": 0,
    }
    assert comparison["findings_the_review_created"]["total"] == 0


def test_a_lane_keeping_a_continuation_is_not_reported_as_stranded() -> None:
    # `exit_lanes` holds continuations as well as connectors, and a lane can leave by
    # running straight on into the next lane of the same road. Reading the connector list
    # instead reports such a lane as stranded the moment its last connector is forbidden,
    # which is wrong and was the first version of this metric.
    #
    # Built directly rather than through a workspace: `exit_lanes` only ever receives
    # *active* connectors, so a review that forbids review_required movements cannot
    # produce this shape. The metric earns its place once lane counts are writable, when
    # re-laning really can take a lane's last exit.
    kept = _lane("keeps-going", exits=["the-next-lane"])
    lost = _lane("dead-end", exits=[])
    before = _lane_model([kept, _lane("dead-end", exits=["a-connector"])])
    after = _lane_model([kept, lost])

    comparison = _compare(before, after)
    assert comparison["lanes_left_without_an_exit"] == ["dead-end"]
    assert "keeps-going" not in comparison["lanes_left_without_an_exit"]


def test_a_lane_that_never_had_an_exit_is_not_blamed_on_the_review() -> None:
    # Twenty-odd lanes in junction-1 run off the edge of the extract and have never had
    # an exit. Reporting those as the review's doing would bury a real regression.
    boundary = _lane("runs-off-the-extract", exits=[])
    comparison = _compare(_lane_model([boundary]), _lane_model([boundary]))

    assert comparison["lanes_left_without_an_exit"] == []
    assert comparison["lanes_without_an_exit_either_way"] == 1


def test_a_re_laned_way_keeps_its_question(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    speed = next(f for f in _model(workspace).findings if f.rule == "speed_default")
    # Re-laning a way renames its findings, because `identifier` covers the lane list.
    # The question — what speed does this way carry — is unchanged, and the comparison
    # must not report the same question as both removed and created.
    relaned = speed.model_copy(
        update={
            "identifier": "a-different-id",
            "affected_feature_ids": [*speed.affected_feature_ids, "an-extra-lane"],
        }
    )
    assert _question_key(relaned) == _question_key(speed)


def test_two_lane_count_questions_on_one_way_stay_distinct() -> None:
    def finding(direction: str) -> ReviewFinding:
        return ReviewFinding(
            identifier=f"id-{direction}",
            rule="lane_count_inference",
            severity="blocker",
            source_type="way",
            source_ids=["200"],
            affected_feature_ids=["lane-a"],
            proposed_value={"direction": direction, "lanes": 1},
            confidence="low",
            reason="default_single_lane",
            evidence_checksum="checksum",
        )

    # One way asks this twice, once per direction. Without the discriminator both
    # questions collapse into one and a real change would go unreported.
    assert _question_key(finding("forward")) != _question_key(finding("backward"))


# --- the gates ----------------------------------------------------------------------


def test_a_review_from_another_workspace_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    submission = _submission(workspace)
    submission["identity"]["workspace"] = "somewhere-else"
    with pytest.raises(ApplyReviewError, match="workspace 'somewhere-else'"):
        _apply(workspace, submission)


def test_a_stale_generation_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    submission = _submission(workspace)
    submission["identity"]["generation_fingerprint"] = "0" * 64
    with pytest.raises(ApplyReviewError, match="Re-open Stage 3"):
        _apply(workspace, submission)


def test_a_decision_naming_a_finding_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    submission = _submission(workspace)
    submission["decisions"][0]["finding_id"] = "no-such-finding"
    with pytest.raises(ApplyReviewError, match="not in the model"):
        _apply(workspace, submission)


def test_a_decision_made_against_changed_evidence_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    submission = _submission(workspace)
    submission["decisions"][0]["evidence_checksum"] = "stale"
    with pytest.raises(ApplyReviewError, match="evidence that has since changed"):
        _apply(workspace, submission)


def test_an_unresolved_blocker_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    submission = _submission(workspace)
    blocker = next(
        d
        for d in submission["decisions"]
        if d["rule"] == "ambiguous_connector"
    )
    blocker["status"] = "unresolved"
    blocker.pop("value", None)
    with pytest.raises(ApplyReviewError, match="unresolved or ignored"):
        _apply(workspace, submission)


def test_a_blocker_the_file_claims_was_ignored_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    submission = _submission(workspace)
    # Readiness is recomputed from the model's severities. A hand-edited file that sets
    # a blocker aside and declares itself ready must not get past this.
    blocker = next(d for d in submission["decisions"] if d["rule"] == "ambiguous_connector")
    blocker["status"] = "ignored"
    blocker.pop("value", None)
    with pytest.raises(ApplyReviewError, match="unresolved or ignored"):
        _apply(workspace, submission)


def test_an_override_that_would_write_an_osm_tag_is_refused_by_name(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    speed = next(f for f in _model(workspace).findings if f.rule == "speed_default")
    submission = _submission(
        workspace,
        overrides={speed.identifier: {"status": "overridden", "value": {"maxspeed_kph": 30}}},
    )
    with pytest.raises(ApplyReviewError, match="speed_default would write maxspeed"):
        _apply(workspace, submission)


def test_a_malformed_connector_override_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    connector_finding = next(
        f for f in _model(workspace).findings if f.rule == "ambiguous_connector"
    )
    submission = _submission(
        workspace,
        overrides={connector_finding.identifier: {"status": "overridden", "value": {}}},
    )
    with pytest.raises(ApplyReviewError, match="needs"):
        _apply(workspace, submission)


def test_stage_4_refuses_a_workspace_that_has_not_been_generated(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    acquire_osm(workspace=workspace, driving_side="left", osm_file=JUNCTION)
    normalize_workspace(workspace=workspace, config=CONFIG)
    submission = workspace / "review.json"
    submission.write_text("{}", encoding="utf-8")
    with pytest.raises(ApplyReviewError, match="Stage 2 has not run"):
        apply_review(workspace=workspace, submission=submission, config=CONFIG)


def test_a_tampered_source_osm_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    submission = _write(workspace, _submission(workspace))
    (workspace / "source" / "map.osm").write_text("<osm/>", encoding="utf-8")
    with pytest.raises(ApplyReviewError, match="checksum does not match"):
        apply_review(workspace=workspace, submission=submission, config=CONFIG)


# --- not applicable, for any rule -----------------------------------------------------


def test_not_applicable_is_recorded_whatever_rule_it_answers(tmp_path: Path) -> None:
    """The state used to be scoped to `ambiguous_connector`, so a signal or lane-count
    finding marked not applicable matched no branch: nothing applied, nothing recorded,
    and the blocker came back looking untouched."""
    workspace = _workspace(tmp_path)
    model = _model(workspace)
    target = next(f for f in model.findings if f.rule != "ambiguous_connector")
    report = _apply(
        workspace,
        _submission(
            workspace,
            overrides={
                target.identifier: {"status": "not_applicable", "reason": "edge of the extract"}
            },
        ),
    )

    applied = json.loads((workspace / "review" / "applied-decisions.json").read_text())
    assert target.identifier in applied["non_osm_overrides"]["left_open_as_not_applicable"]
    assert applied["non_osm_overrides"]["left_open_reasons"][target.identifier] == (
        "edge of the extract"
    )
    assert "edge of the extract" in report.read_text(encoding="utf-8")


def test_not_applicable_on_a_connector_still_leaves_it_review_required(tmp_path: Path) -> None:
    """Hoisting the check above the rule dispatch must not change what it already did."""
    workspace = _workspace(tmp_path)
    model = _model(workspace)
    target = next(f for f in model.findings if f.rule == "ambiguous_connector")
    connector = target.affected_feature_ids[0]
    _apply(
        workspace,
        _submission(workspace, overrides={target.identifier: {"status": "not_applicable"}}),
    )

    comparison = json.loads((workspace / "reports" / "reviewed-comparison.json").read_text())
    assert target.identifier in comparison["left_open_as_not_applicable"]
    assert connector not in comparison["connectors"]["forbidden_by_review"]
    assert connector not in comparison["connectors"]["activated_by_review"]


def test_a_finding_left_open_does_not_count_as_still_open(tmp_path: Path) -> None:
    """What Stage 5 gates on. Not applicable is an answer, so it closes the blocker -
    reported under its own heading rather than folded into the open count."""
    workspace = _workspace(tmp_path)
    model = _model(workspace)
    target = next(f for f in model.findings if f.severity == "blocker")
    _apply(
        workspace,
        _submission(workspace, overrides={target.identifier: {"status": "not_applicable"}}),
    )

    comparison = json.loads((workspace / "reports" / "reviewed-comparison.json").read_text())
    assert target.identifier not in comparison["findings_still_open"]
    assert not [
        item for item in comparison["finding_decisions"].values() if item["state"] == "undecided"
    ]


def test_a_signal_association_override_reaches_the_regenerated_model(
    tmp_path: Path,
) -> None:
    """`Choose lanes` used to be recorded and then dropped on the floor.

    `_overrides_from` collected the association into the audit record, but
    `ReviewOverrides` carried only connector ids, so regeneration rebuilt the association
    from the graph and the reviewer's answer changed nothing. Because
    `_decision_is_satisfied` asks the *model* whether the signal is mapped, the blocker
    then stayed open whatever was answered - a review that appeared applied and was not.
    """
    workspace = tmp_path / "signals"
    acquire_osm(workspace=workspace, driving_side="left", osm_file=SIGNALS)
    normalize_workspace(workspace=workspace, config=CONFIG)
    generate_lane_model(workspace=workspace, config=CONFIG)

    model = _model(workspace)
    blocker = next(
        f
        for f in model.findings
        if f.rule == "signal_lane_association" and f.severity == "blocker"
    )
    chosen = sorted(lane.identifier for lane in model.lanes)[:1]
    submission = _submission(
        workspace,
        overrides={
            blocker.identifier: {"status": "overridden", "value": {"lane_ids": chosen}}
        },
    )
    _apply(workspace, submission)

    reviewed = _reviewed(workspace)
    signal = next(s for s in reviewed.signals if s.source_node_id == blocker.source_ids[0])
    assert signal.lane_ids == chosen
    assert signal.status == "mapped"
    # And the decision now counts as met, which is the whole point: before this, no answer
    # except `not_applicable` could close the finding.
    comparison = json.loads((workspace / "reports" / "reviewed-comparison.json").read_text())
    assert comparison["findings_still_open"] == []


def test_a_signal_override_naming_an_absent_lane_is_refused(tmp_path: Path) -> None:
    # Gate 6 catches this from the submission, so it never reaches generation. The check
    # inside the generator is the backstop for a caller that builds overrides directly.
    workspace = tmp_path / "signals"
    acquire_osm(workspace=workspace, driving_side="left", osm_file=SIGNALS)
    normalize_workspace(workspace=workspace, config=CONFIG)
    generate_lane_model(workspace=workspace, config=CONFIG)

    blocker = next(
        f
        for f in _model(workspace).findings
        if f.rule == "signal_lane_association" and f.severity == "blocker"
    )
    submission = _submission(
        workspace,
        overrides={
            blocker.identifier: {
                "status": "overridden",
                "value": {"lane_ids": ["not-a-lane"]},
            }
        },
    )
    with pytest.raises(ApplyReviewError, match="not in the model"):
        _apply(workspace, submission)


def test_a_signal_override_is_keyed_on_the_node_not_the_finding(tmp_path: Path) -> None:
    """Why `ReviewOverrides.signal_lane_associations` is a node map.

    A finding's identifier covers its `affected_feature_ids`, so the moment an association
    changes the question is renamed. A verdict filed under the old id would then match
    nothing on the regenerated model and apply silently to no one.
    """
    workspace = tmp_path / "signals"
    acquire_osm(workspace=workspace, driving_side="left", osm_file=SIGNALS)
    normalize_workspace(workspace=workspace, config=CONFIG)
    generate_lane_model(workspace=workspace, config=CONFIG)

    model = _model(workspace)
    blocker = next(
        f
        for f in model.findings
        if f.rule == "signal_lane_association" and f.severity == "blocker"
    )
    chosen = sorted(lane.identifier for lane in model.lanes)[:1]
    submission = _submission(
        workspace,
        overrides={
            blocker.identifier: {"status": "overridden", "value": {"lane_ids": chosen}}
        },
    )
    _apply(workspace, submission)

    recorded = json.loads((workspace / "review" / "applied-decisions.json").read_text())
    associations = recorded["non_osm_overrides"]["signal_lane_associations"]
    assert associations == {blocker.source_ids[0]: chosen}
    assert blocker.identifier not in associations
