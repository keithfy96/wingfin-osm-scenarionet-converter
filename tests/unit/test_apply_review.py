"""Stage 4 — applying a review and regenerating from it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from osm_scenario.acquisition import acquire_osm
from osm_scenario.apply_review import (
    ApplyReviewError,
    _comparison,
    _question_key,
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


def test_the_reviewed_osm_is_a_faithful_copy_while_no_rule_writes_tags(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _apply(workspace, _submission(workspace))
    assert (workspace / "review" / "reviewed.osm").read_bytes() == (
        workspace / "source" / "map.osm"
    ).read_bytes()


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
