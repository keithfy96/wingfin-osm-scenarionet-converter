"""Stage 4 - apply reviewed decisions and regenerate the lane model.

Stage 3 exports what a human concluded. This stage turns those conclusions into a second
lane model, built the same way Stage 2 built the first one, and reports what changed.

Two properties hold whatever the review says, and both are enforced rather than asserted:

* `source/map.osm` is never written. It is acquisition evidence, and Stage 4 re-checksums
  it after the run and fails if it moved. The reviewed OSM is a separate file.
* The reviewed model is *regenerated*, never patched. A decision that changes lane counts
  changes lane ids, connector ids and findings downstream of them, and only running the
  generator again gets all of that right.

Scope, deliberately: a decision that would become an OSM tag is applied only where the tag
write has been built and proved. `lane_count_inference` is; the rest are refused by name
rather than half applied. See `_OSM_NATIVE_RULES`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import osmnx as ox
from pyproj import CRS, Transformer

# Private helpers imported across modules on purpose: these are the exact routines Stage 1
# and Stage 2 use, and a Stage 4 copy of any of them would be a second implementation to
# keep in step. `_project_graph` in particular must match Stage 1B exactly or the reviewed
# model lands in a different coordinate frame from the preliminary one.
from osm_scenario.acquisition import _configure_preserved_tags
from osm_scenario.comparison_view import render_comparison_html
from osm_scenario.config import ConverterConfig
from osm_scenario.generation import (
    GENERATOR_VERSION,
    LANE_MODEL_SCHEMA_VERSION,
    OPPOSITE_DIRECTION,
    ReviewOverrides,
    _canonical_checksum,
    _carries_whole_carriageway,
    build_lane_model,
)
from osm_scenario.lane_model import PreliminaryLaneModel, ReviewFinding
from osm_scenario.normalization import _project_graph
from osm_scenario.osm_source import read_osm_snapshot, select_public_driving_graph

APPLIED_DECISIONS_VERSION = 1

# Rules whose override writes a tag into the reviewed OSM and which are *not* implemented.
# An override on one is refused by name rather than silently dropped - a review that
# appears to have been applied but was not is worse than a run that stops.
#
# `lane_count_inference` was the first to land and is no longer listed: see
# `_lane_count_tag`. It set the standard the rest have to meet - the tag written must
# invert what generation.py reads, and the override must be proved by regenerating and
# reading the lane back, not merely by the tag appearing in the file.
#
# When the rest are built: `width` must invert generation.py's
# `width_total / (lanes tag or directional count)`, and `turn:lanes` must be pinned against
# `_turn_permissions`, which already encodes the kerbside-first ordering under left-hand
# traffic.
_OSM_NATIVE_RULES = {
    "speed_default": "maxspeed",
    "lane_width_default": "width",
    "lane_transition_count_mismatch": "lanes on the destination way",
    "turn_permission_geometry_conflict": "turn:lanes",
    "restriction_effect_review": "the turn restriction relation",
}

# The largest lane count an override may claim. Matches the Stage 3 control's `max`, so a
# value the browser would not let a reviewer type cannot arrive by a hand-edited file.
_MAX_OVERRIDE_LANES = 12

# How to identify the *question* a finding asks, as opposed to the answer it proposes.
#
# A finding's `identifier` covers its `affected_feature_ids`, which for a way-level rule
# is that way's lane list. Re-laning a road therefore renames every question about it, and
# a comparison keyed on the identifier reports the same question as both removed and
# created. Way-level rules are keyed on the way instead, with a discriminator where one
# way legitimately asks the same rule twice.
_WAY_LEVEL_QUESTIONS: dict[str, str | None] = {
    "speed_default": None,
    "lane_width_default": None,
    "lane_count_inference": "direction",
}


class ApplyReviewError(RuntimeError):
    """Raised when a review cannot be applied to a workspace."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise ApplyReviewError(f"{label} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ApplyReviewError(f"{label} is not valid JSON: {error}") from error


def _question_key(finding: ReviewFinding) -> tuple[Any, ...]:
    """What this finding asks, stable across a regeneration that moves its features."""
    if finding.rule not in _WAY_LEVEL_QUESTIONS:
        return (
            finding.rule,
            finding.source_type,
            tuple(finding.source_ids),
            tuple(finding.affected_feature_ids),
        )
    key: list[Any] = [finding.rule, tuple(finding.source_ids)]
    discriminator = _WAY_LEVEL_QUESTIONS[finding.rule]
    if discriminator is not None:
        proposed = finding.proposed_value
        if not isinstance(proposed, dict) or discriminator not in proposed:
            raise ApplyReviewError(
                f"{finding.rule} finding {finding.identifier} has no {discriminator!r} in "
                "its proposed value, so its question cannot be identified"
            )
        key.append(proposed[discriminator])
    return tuple(key)


def _check_stage_1_and_2(workspace: Path) -> tuple[dict[str, Any], Path]:
    """Gate 1 - the workspace is a passed Stage 1B whose artifacts match their checksums."""
    manifest_path = workspace / "source" / "manifest.json"
    manifest = _read_json(manifest_path, "Stage 1 manifest")
    if manifest.get("stage_1b", {}).get("status") != "passed":
        raise ApplyReviewError("Stage 1B has not passed; run fetch first")
    if manifest.get("stage_2", {}).get("status") != "passed":
        raise ApplyReviewError("Stage 2 has not run; run generate-map first")

    source_path = workspace / manifest["source"]["path"]
    graph_artifact = manifest["stage_1b"]["artifacts"]["projected_graphml"]
    for label, path, expected in (
        ("source OSM", source_path, manifest["source"]["sha256"]),
        ("projected GraphML", workspace / graph_artifact["path"], graph_artifact["sha256"]),
    ):
        if not path.is_file() or _sha256(path) != expected:
            raise ApplyReviewError(f"{label} checksum does not match the Stage 1 manifest")
    return manifest, source_path


def _check_submission(
    submission: dict[str, Any],
    model: PreliminaryLaneModel,
    report: dict[str, Any],
    workspace_name: str,
) -> list[dict[str, Any]]:
    """Gates 2 to 6. Returns the decisions, once every one of them is applicable."""
    identity = submission.get("identity")
    if not isinstance(identity, dict):
        raise ApplyReviewError("submission has no identity")
    decisions = submission.get("decisions")
    if not isinstance(decisions, list):
        raise ApplyReviewError("submission has no decisions array")

    # Gate 2 - the review is about this map, at this generation. A stale review is
    # refused rather than migrated: migration belongs in Stage 3, where a human can see
    # which of their decisions survived and judge the ones that did not.
    if identity.get("workspace") != workspace_name:
        raise ApplyReviewError(
            f"review belongs to workspace {identity.get('workspace')!r}, not {workspace_name!r}"
        )
    fingerprint = identity.get("generation_fingerprint")
    for label, expected in (
        ("the generation report", report.get("generation_fingerprint")),
        ("the preliminary model", model.metadata.generation_fingerprint),
    ):
        if fingerprint != expected:
            raise ApplyReviewError(
                f"review was made against generation {fingerprint} but {label} says "
                f"{expected}. Re-open Stage 3 over the current model and re-export."
            )

    findings = {finding.identifier: finding for finding in model.findings}

    # Gate 3 - every decision names a finding that exists, and answers the evidence that
    # exists now. A checksum mismatch means the question moved under the answer.
    unknown = [d["finding_id"] for d in decisions if d.get("finding_id") not in findings]
    if unknown:
        raise ApplyReviewError(
            f"{len(unknown)} decision(s) name findings that are not in the model, "
            f"starting with {unknown[0]}"
        )
    stale = [
        d["finding_id"]
        for d in decisions
        if findings[d["finding_id"]].evidence_checksum != d.get("evidence_checksum")
    ]
    if stale:
        raise ApplyReviewError(
            f"{len(stale)} decision(s) were made against evidence that has since changed, "
            f"starting with {stale[0]}. Re-open Stage 3 and confirm them."
        )

    # Gate 4 - readiness is recomputed from the model's own severities. The file states
    # its readiness, but a hand-edited file states whatever it likes.
    by_id = {d["finding_id"]: d for d in decisions}
    unresolved = [
        finding.identifier
        for finding in model.findings
        if finding.severity == "blocker"
        and by_id.get(finding.identifier, {}).get("status") in (None, "unresolved", "ignored")
    ]
    if unresolved:
        raise ApplyReviewError(
            f"{len(unresolved)} blocking finding(s) are unresolved or ignored, starting "
            f"with {unresolved[0]}. Stage 4 will not promote a map with open blockers."
        )

    # Gate 5 - an override on a rule whose effect is an OSM tag. Refused by name.
    blocked: dict[str, list[str]] = {}
    for decision in decisions:
        if decision.get("status") != "overridden":
            continue
        rule = findings[decision["finding_id"]].rule
        if rule in _OSM_NATIVE_RULES:
            blocked.setdefault(rule, []).extend(findings[decision["finding_id"]].source_ids)
    if blocked:
        detail = "; ".join(
            f"{rule} would write {_OSM_NATIVE_RULES[rule]} onto "
            f"{', '.join(sorted(set(ids))[:5])}"
            + (" and others" if len(set(ids)) > 5 else "")
            for rule, ids in sorted(blocked.items())
        )
        raise ApplyReviewError(
            "this Stage 4 does not write OSM tags yet, so these overrides cannot be "
            f"applied: {detail}. Accept or ignore them, or wait for tag writing."
        )

    # Gate 6 - the shape of every override Stage 4 does apply.
    lane_ids = {lane.identifier for lane in model.lanes}
    for decision in decisions:
        if decision.get("status") != "overridden":
            continue
        finding = findings[decision["finding_id"]]
        _check_override_value(finding, decision.get("value"), lane_ids)
    return decisions


def _override_lane_count(finding: ReviewFinding, value: Any) -> int:
    """The lane count a `lane_count_inference` override asks for, or a refusal by name.

    One key, `lane_count`, for the one direction the finding is about. Stage 3 offered
    `lanes` / `lanes:forward` / `lanes:backward` before this rule was implemented, which
    could state the number three ways and contradict itself two of them; a file carrying
    that shape is refused rather than guessed at, because guessing which of three fields
    the reviewer meant is exactly the silent misapplication Gate 5 exists to prevent.
    """
    count = value.get("lane_count") if isinstance(value, dict) else None
    # bool is an int in Python, and `{"lane_count": true}` is not a lane count.
    if isinstance(count, bool) or not isinstance(count, int):
        raise ApplyReviewError(
            f"lane_count_inference override on {finding.identifier} needs "
            '{"lane_count": <whole number of lanes in this finding\'s direction>}'
        )
    if not 1 <= count <= _MAX_OVERRIDE_LANES:
        raise ApplyReviewError(
            f"lane_count_inference override on {finding.identifier} asks for {count} "
            f"lanes; the permitted range is 1 to {_MAX_OVERRIDE_LANES}"
        )
    return count


def _override_direction(finding: ReviewFinding) -> str:
    """Which direction of the way the finding is about.

    Read from the finding's own proposal, not from the override: the reviewer answers the
    question that was asked, and the question names one direction.
    """
    proposed = finding.proposed_value
    direction = proposed.get("direction") if isinstance(proposed, dict) else None
    if direction not in OPPOSITE_DIRECTION:
        raise ApplyReviewError(
            f"lane_count_inference finding {finding.identifier} proposes no direction, "
            "so an override on it cannot be written to a tag"
        )
    return str(direction)


def _check_override_value(finding: ReviewFinding, value: Any, lane_ids: set[str]) -> None:
    if finding.rule == "lane_count_inference":
        _override_lane_count(finding, value)
        _override_direction(finding)
        if len(finding.source_ids) != 1:
            raise ApplyReviewError(
                f"lane_count_inference override on {finding.identifier} names "
                f"{len(finding.source_ids)} source ways; a lane count is written onto "
                "exactly one way"
            )
        return
    if finding.rule == "ambiguous_connector":
        if not isinstance(value, dict) or not isinstance(value.get("accepted"), bool):
            raise ApplyReviewError(
                f"ambiguous_connector override on {finding.identifier} needs "
                '{"accepted": true|false}'
            )
        return
    if finding.rule == "signal_lane_association":
        ids = value.get("lane_ids") if isinstance(value, dict) else None
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ApplyReviewError(
                f"signal_lane_association override on {finding.identifier} needs a "
                "non-empty lane_ids list"
            )
        missing = [i for i in ids if i not in lane_ids]
        if missing:
            raise ApplyReviewError(
                f"signal_lane_association override on {finding.identifier} names lanes "
                f"that are not in the model: {', '.join(missing[:5])}"
            )
        return
    raise ApplyReviewError(
        f"{finding.rule} offers no override that Stage 4 can apply "
        f"(finding {finding.identifier})"
    )


def _write_reviewed_osm(
    source_path: Path, reviewed_osm: Path, lane_counts: dict[str, dict[str, int]]
) -> dict[str, dict[str, str]]:
    """Write the reviewed OSM and return the tags the review materialised onto it.

    With nothing to write the source is copied byte for byte. That is not merely an
    optimisation: a review that changed no tag must produce a reviewed OSM identical to
    the one it was made against, and byte identity is a property a test can check, which
    round-tripping every run through an XML writer would quietly give up.

    Which tag a lane count becomes is decided per way, from that way's own tags, so that
    what lands is idiomatic OSM and so it inverts `_directional_lane_count` exactly:

    * a way carrying the whole carriageway (`oneway`, or a roundabout) takes `lanes`,
      which that function reads as `explicit_total_oneway`;
    * any other way takes `lanes:<direction>`, read as `explicit_directional`.

    Both are its highest-confidence branches and both short-circuit the inference, so the
    reviewed model reports the reviewer's number rather than re-deriving one.
    """
    if not lane_counts:
        shutil.copy2(source_path, reviewed_osm)
        return {}

    tree = ET.parse(source_path)
    root = tree.getroot()
    materialised: dict[str, dict[str, str]] = {}
    for way in root.findall("way"):
        wanted = lane_counts.get(way.get("id") or "")
        if not wanted:
            continue
        tags = {
            tag.get("k") or "": tag.get("v") or ""
            for tag in way.findall("tag")
            if tag.get("k")
        }
        whole = _carries_whole_carriageway(tags)
        written: dict[str, str] = {}
        for direction, count in sorted(wanted.items()):
            key = "lanes" if whole else f"lanes:{direction}"
            written[key] = str(count)
        # A one-way way has one direction, so two overrides on one cannot both be right;
        # the check above only catches two answers for the same direction.
        if whole and len(wanted) > 1:
            raise ApplyReviewError(
                f"way {way.get('id')} carries the whole carriageway but the review sets a "
                f"lane count for {len(wanted)} directions on it"
            )
        for key, value in written.items():
            existing = next((t for t in way.findall("tag") if t.get("k") == key), None)
            if existing is not None:
                existing.set("v", value)
            else:
                ET.SubElement(way, "tag", {"k": key, "v": value})
        materialised[way.get("id") or ""] = written

    missing = sorted(set(lane_counts) - set(materialised))
    if missing:
        raise ApplyReviewError(
            "the review sets a lane count on ways that are not in the source OSM: "
            f"{', '.join(missing[:5])}"
        )
    tree.write(reviewed_osm, encoding="utf-8", xml_declaration=True)
    return materialised


def _overrides_from(
    decisions: list[dict[str, Any]], model: PreliminaryLaneModel
) -> tuple[ReviewOverrides, dict[str, Any]]:
    """Split the decisions into what changes generation and what is merely recorded."""
    findings = {finding.identifier: finding for finding in model.findings}
    forbidden: set[str] = set()
    active: set[str] = set()
    signal_associations: dict[str, list[str]] = {}
    accepted_stop_lines: list[str] = []
    # {way id: {direction: lane count}}. Which tag each becomes is decided at write time
    # from the way's own `oneway`/`junction` tags, which are in the XML being edited.
    lane_counts: dict[str, dict[str, int]] = {}
    # A finding marked not_applicable says the finding does not describe a defect *here*,
    # not that the thing it names should change. There is no verdict to apply, so the
    # feature keeps whatever status generation gave it and the reviewed model asks the
    # question again - which the comparison reports rather than hiding.
    #
    # Checked before the rule dispatch, so it is available to every rule. A signal at the
    # upstream edge of the extract can never have an approaching lane to associate, and
    # the only honest answer to that finding is that it is not applicable; scoping this to
    # connectors left such a decision matching no branch at all, applying nothing and
    # recording nothing.
    # {finding id: the reviewer's reason}. The reason is the whole value of this state -
    # "not applicable" without one is indistinguishable from a finding nobody read.
    left_open: dict[str, str] = {}

    for decision in decisions:
        finding = findings[decision["finding_id"]]
        status = decision.get("status")
        value = decision.get("value")
        if status == "not_applicable":
            left_open[finding.identifier] = str(decision.get("reason") or "")
            continue
        if finding.rule == "ambiguous_connector":
            if status == "accepted" or (
                status == "overridden" and isinstance(value, dict) and value.get("accepted")
            ):
                active.update(finding.affected_feature_ids)
            elif status == "overridden":
                forbidden.update(finding.affected_feature_ids)
        elif finding.rule == "signal_lane_association" and status == "overridden":
            signal_associations[finding.identifier] = list(value["lane_ids"])
        elif finding.rule == "inferred_stop_line" and status == "accepted":
            accepted_stop_lines.extend(finding.affected_feature_ids)
        elif finding.rule == "lane_count_inference" and status == "overridden":
            # Accepting changes nothing by design: the inferred count already stands, and
            # the decision records which number that was. Only an override moves a tag.
            way_id = finding.source_ids[0]
            direction = _override_direction(finding)
            count = _override_lane_count(finding, value)
            existing = lane_counts.setdefault(way_id, {}).get(direction)
            if existing is not None and existing != count:
                raise ApplyReviewError(
                    f"two overrides disagree about the {direction} lane count on way "
                    f"{way_id}: {existing} and {count}. Re-open Stage 3 and settle it; "
                    "applying either one silently would discard the other."
                )
            lane_counts[way_id][direction] = count

    applied = {
        "forbidden_connector_ids": sorted(forbidden),
        "activated_connector_ids": sorted(active),
        "signal_lane_associations": {k: v for k, v in sorted(signal_associations.items())},
        "accepted_stop_line_ids": sorted(accepted_stop_lines),
        "left_open_as_not_applicable": sorted(left_open),
        "left_open_reasons": dict(sorted(left_open.items())),
        "lane_count_overrides": {
            way_id: dict(sorted(by_direction.items()))
            for way_id, by_direction in sorted(lane_counts.items())
        },
    }
    overrides = ReviewOverrides(
        forbidden_connector_ids=frozenset(forbidden),
        active_connector_ids=frozenset(active),
    )
    return overrides, applied


def _regenerate(
    *,
    reviewed_osm: Path,
    manifest: dict[str, Any],
    config: ConverterConfig,
    overrides: ReviewOverrides,
) -> tuple[PreliminaryLaneModel, dict[str, int], str, dict[str, object]]:
    """Rebuild the lane model over the reviewed OSM, in Stage 1B's coordinate frame.

    The graph is built here rather than by re-running Stage 1 into a scratch workspace.
    That avoids two failures the workspace route causes: `_prepare_local_source` refuses
    to overwrite an existing source, so a second run would fail; and osmnx stamps a build
    timestamp into GraphML, so a byte-identical model would get a new fingerprint on every
    run. No GraphML is written here, and the model is keyed on the reviewed OSM itself.

    The projection is read from the manifest, not re-derived. Deriving it again would take
    the centroid of whatever network the review left behind, quietly moving the origin and
    putting the two models in different frames.
    """
    _configure_preserved_tags()
    graph = ox.graph_from_xml(reviewed_osm, simplify=False, retain_all=True)
    snapshot = read_osm_snapshot(reviewed_osm)
    # Stage 1A does not hand its raw graph to Stage 1B: it applies the public-driving-v1
    # road selection first. Skipping it here silently readmits every service road, track
    # and private way the policy excludes - on junction-1 that is 196 edges becoming 561,
    # and a reviewed model of a different network than the one that was reviewed.
    graph, selection_audit = select_public_driving_graph(graph, snapshot)
    if not graph.number_of_edges():
        raise ApplyReviewError("the reviewed OSM produced no roads under public-driving-v1")

    local_crs = CRS.from_wkt(manifest["stage_1b"]["projection"]["local_crs_wkt"])
    forward = Transformer.from_crs(CRS.from_epsg(4326), local_crs, always_xy=True)
    projected = _project_graph(graph, forward, local_crs)
    source_checksum = _sha256(reviewed_osm)
    config_checksum = _canonical_checksum(config.model_dump(mode="json"))
    fingerprint = _canonical_checksum(
        {
            "generator_version": GENERATOR_VERSION,
            "schema_version": LANE_MODEL_SCHEMA_VERSION,
            "source_checksum": source_checksum,
            # The reviewed OSM is the graph's only input, and unlike a GraphML file it
            # carries no build timestamp, so this is stable across identical runs.
            "graph_checksum": source_checksum,
            "configuration_checksum": config_checksum,
            "review_overrides": {
                "forbidden": sorted(overrides.forbidden_connector_ids),
                "active": sorted(overrides.active_connector_ids),
            },
        }
    )
    model, counts = build_lane_model(
        snapshot=snapshot,
        graph=projected,
        config=config,
        driving_side=manifest["driving_side"],
        source_checksum=source_checksum,
        graph_checksum=source_checksum,
        config_checksum=config_checksum,
        fingerprint=fingerprint,
        coordinate_system_wkt=manifest["stage_1b"]["projection"]["local_crs_wkt"],
        overrides=overrides,
    )
    return model, counts, fingerprint, selection_audit


def _decision_is_satisfied(finding: ReviewFinding, unmapped_signal_nodes: set[str]) -> bool:
    """Does the reviewed model actually reflect the decision taken on this finding?

    Answering a finding and resolving it are not the same act. Accepting an inference
    leaves the map exactly as generated - that is what accepting *means* - so for almost
    every rule a decision is satisfied the moment it is made.

    `signal_lane_association` is the exception, and the reason this check exists.
    Accepting it accepts a `proposed_value` of `[]`, because the finding only fires when
    no approaching lane was found; the association stays `review_required` with no lanes.
    Keying resolution on "a decision exists" would call that settled for ever.
    """
    if finding.rule != "signal_lane_association":
        return True
    return not any(node in unmapped_signal_nodes for node in finding.source_ids)


def _decisions_by_reviewed_finding(
    before: dict[tuple[Any, ...], ReviewFinding],
    reviewed: list[ReviewFinding],
    decisions: list[dict[str, Any]],
    unmapped_signal_nodes: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Which reviewed finding each decision answers, joined by question, not identifier.

    A decision names a *preliminary* finding id. Regeneration mints new ids - and re-laning
    a way renames every question about it - so joining on the id would report a fully
    reviewed map as untouched the moment a lane-count override lands. The question key is
    the join that survives that, which is why it is computed here rather than left for
    Stage 5 to reinvent against the wrong key.

    Two states are recorded, never three: a finding is `decided` when a decision names its
    question, `undecided` when none does. The third state - "we were not told" - is the
    empty map returned when no decisions are supplied at all, so an absent entry never
    reads as an unreviewed finding.

    A decided entry also carries `satisfied`, which is whether the model agrees with the
    decision. `decided` is about the reviewer; `satisfied` is about the map.
    """
    unmapped = unmapped_signal_nodes or set()
    by_finding_id = {d["finding_id"]: d for d in decisions}
    joined: dict[str, dict[str, Any]] = {}
    for finding in reviewed:
        preliminary = before.get(_question_key(finding))
        decision = by_finding_id.get(preliminary.identifier) if preliminary else None
        if decision is None:
            # Either the reviewer left this question alone, or regeneration asked it for
            # the first time. Both are genuinely undecided; neither is an error here.
            joined[finding.identifier] = {"state": "undecided"}
            continue
        joined[finding.identifier] = {
            "state": "decided",
            "status": decision["status"],
            "satisfied": _decision_is_satisfied(finding, unmapped),
            "value": decision.get("value"),
            "proposed_value": decision.get("proposed_value", finding.proposed_value),
            "reason": decision.get("reason"),
            "answers_finding_id": decision["finding_id"],
        }
    return dict(sorted(joined.items()))


def _comparison(
    *,
    preliminary: PreliminaryLaneModel,
    reviewed: PreliminaryLaneModel,
    applied: dict[str, Any],
    # Defaults to nothing retagged, which is the shape of every review that overrides no
    # lane count - the common case, and the one where reviewed.osm is a byte copy.
    retagged: dict[str, dict[str, str]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    def ids(items: list[Any]) -> set[str]:
        return {item.identifier for item in items}

    before = {_question_key(f): f for f in preliminary.findings}
    after = {_question_key(f): f for f in reviewed.findings}
    created = [after[k] for k in after.keys() - before.keys()]
    resolved = [before[k] for k in before.keys() - after.keys()]
    unmapped_signal_nodes = {
        signal.source_node_id
        for signal in reviewed.signals
        if signal.status != "mapped" or not signal.lane_ids
    }
    # Empty when no decisions were supplied, which is how a caller says "not told" rather
    # than "nothing was decided" - the page must not badge a finding as unreviewed on that.
    decided = (
        _decisions_by_reviewed_finding(
            before, reviewed.findings, decisions, unmapped_signal_nodes
        )
        if decisions is not None
        else {}
    )
    # What Stage 5 gates on. A blocker is open when nobody answered it, or when the map
    # still contradicts the answer. Not when it merely reappears: an accepted inference
    # regenerates identically by design, so the same question comes back for ever.
    #
    # `not_applicable` is excluded because it *is* an answer - "this finding does not
    # describe a defect here". It is reported separately under `left_open_as_not_applicable`
    # rather than folded into zero, so closing a blocker still costs a visible sentence.
    #
    # With no decisions supplied at all, every blocker is open. "We were not told" cannot
    # be reported as zero: an empty list reads as a claim however it is documented, and a
    # map nobody reviewed must not look the same as one whose blockers were all answered.
    still_open = sorted(
        finding.identifier
        for finding in reviewed.findings
        if finding.severity == "blocker"
        and (
            decisions is None
            or (
                decided.get(finding.identifier, {}).get("status") != "not_applicable"
                and (
                    decided.get(finding.identifier, {}).get("state") == "undecided"
                    or not decided.get(finding.identifier, {}).get("satisfied", True)
                )
            )
        )
    )

    def by_rule(findings: list[ReviewFinding]) -> dict[str, dict[str, int]]:
        table: dict[str, dict[str, int]] = {}
        for finding in findings:
            table.setdefault(finding.rule, {"blocker": 0, "warning": 0})[finding.severity] += 1
        return dict(sorted(table.items()))

    # Suppressing a movement can be a lane's last way out. Stage 5 would be first to say
    # so otherwise, long after the decision that caused it is out of mind.
    #
    # Read `exit_lanes`, not the connectors. A lane leaves by a junction movement *or* by
    # a straight continuation into the next lane of the same road, and `exit_lanes` holds
    # both kinds of id while the connector list holds only the first. Counting connectors
    # alone reports a lane as stranded when it still runs straight on - it did, on
    # 863caef770461b65, whose U-turn was forbidden while its continuation was untouched.
    #
    # Only lanes that *gained* an empty exit count. Twenty-odd lanes have never had one:
    # they run off the edge of the extract, and that is the boundary, not this review.
    stranded_before = {lane.identifier for lane in preliminary.lanes if not lane.exit_lanes}
    stranded_after = {lane.identifier for lane in reviewed.lanes if not lane.exit_lanes}
    reviewed_lanes = ids(reviewed.lanes)

    return {
        "counts": {
            name: {"preliminary": len(a), "reviewed": len(b)}
            for name, a, b in (
                ("lanes", preliminary.lanes, reviewed.lanes),
                ("connectors", preliminary.connectors, reviewed.connectors),
                ("findings", preliminary.findings, reviewed.findings),
                ("signals", preliminary.signals, reviewed.signals),
                ("stop_lines", preliminary.stop_lines, reviewed.stop_lines),
                ("restrictions", preliminary.restrictions, reviewed.restrictions),
            )
        },
        "connector_status": {
            status: {
                "preliminary": sum(c.status == status for c in preliminary.connectors),
                "reviewed": sum(c.status == status for c in reviewed.connectors),
            }
            for status in ("active", "forbidden", "review_required")
        },
        # Findings are compared by question, not by identifier: a re-laned way renames its
        # findings without changing what they ask, and counting those as both removed and
        # created would drown the ones that genuinely appeared.
        "findings_the_review_created": {
            "total": len(created),
            "by_rule": by_rule(created),
            "blocking": [
                {
                    "identifier": f.identifier,
                    "rule": f.rule,
                    "source_ids": list(f.source_ids),
                    "reason": f.reason,
                }
                for f in sorted(created, key=lambda item: item.identifier)
                if f.severity == "blocker"
            ],
        },
        "findings_the_review_resolved": {"total": len(resolved), "by_rule": by_rule(resolved)},
        "lanes": {
            "added": sorted(ids(reviewed.lanes) - ids(preliminary.lanes)),
            "removed": sorted(ids(preliminary.lanes) - ids(reviewed.lanes)),
        },
        "connectors": {
            "added": sorted(ids(reviewed.connectors) - ids(preliminary.connectors)),
            "removed": sorted(ids(preliminary.connectors) - ids(reviewed.connectors)),
            "forbidden_by_review": applied["forbidden_connector_ids"],
            "activated_by_review": applied["activated_connector_ids"],
        },
        "ways_retagged": retagged or {},
        # What Stage 5 should read to decide whether a blocker is unresolved. A blocker
        # still in the reviewed model is not evidence of unreviewed work - accepting an
        # inference leaves the map unchanged, so the same question comes back. Unresolved
        # means `state == "undecided"`, not "a blocker exists".
        "finding_decisions": decided,
        # The single list Stage 5 gates on, derived from the above: blockers nobody
        # answered, plus blockers whose answer the map does not reflect. A blocker that
        # merely reappears is not here, and neither is one judged not applicable - that
        # one is reported under `left_open_as_not_applicable`, where it stays visible.
        "findings_still_open": still_open,
        "lanes_left_without_an_exit": sorted(
            lane for lane in (stranded_after - stranded_before) if lane in reviewed_lanes
        ),
        "lanes_given_an_exit": sorted(stranded_before - stranded_after),
        "lanes_without_an_exit_either_way": len(stranded_before & stranded_after),
        "left_open_as_not_applicable": applied["left_open_as_not_applicable"],
    }


def _markdown(comparison: dict[str, Any], applied: dict[str, Any]) -> str:
    lines = [
        "# Stage 4 - preliminary versus reviewed",
        "",
        "| feature | preliminary | reviewed |",
        "| --- | ---: | ---: |",
        *(
            f"| {name} | {counts['preliminary']} | {counts['reviewed']} |"
            for name, counts in comparison["counts"].items()
        ),
        "",
        "| connector status | preliminary | reviewed |",
        "| --- | ---: | ---: |",
        *(
            f"| {status} | {counts['preliminary']} | {counts['reviewed']} |"
            for status, counts in comparison["connector_status"].items()
        ),
        "",
        f"Forbidden by the review: {len(applied['forbidden_connector_ids'])}. "
        f"Activated by the review: {len(applied['activated_connector_ids'])}.",
        "",
    ]

    resolved = comparison["findings_the_review_resolved"]
    lines += [f"## Findings this review resolved: {resolved['total']}", ""]
    if resolved["total"]:
        lines += [
            "| rule | blocking | warnings |",
            "| --- | ---: | ---: |",
            *(
                f"| {rule} | {c['blocker']} | {c['warning']} |"
                for rule, c in resolved["by_rule"].items()
            ),
            "",
        ]

    created = comparison["findings_the_review_created"]
    lines += [f"## Findings this review created: {created['total']}", ""]
    if created["total"]:
        lines += [
            "Applying a review regenerates, and regeneration asks new questions. Stage 3 "
            "renders the *preliminary* model, so these appear nowhere else.",
            "",
            "| rule | blocking | warnings |",
            "| --- | ---: | ---: |",
            *(
                f"| {rule} | {c['blocker']} | {c['warning']} |"
                for rule, c in created["by_rule"].items()
            ),
            "",
        ]
    if created["blocking"]:
        lines += [
            f"### {len(created['blocking'])} of them block promotion",
            "",
            *(
                f"- `{item['identifier']}` - {item['rule']} - "
                f"{', '.join(item['source_ids'])} - {item['reason']}"
                for item in created["blocking"]
            ),
            "",
        ]

    stranded = comparison["lanes_left_without_an_exit"]
    given = comparison["lanes_given_an_exit"]
    lines += [f"## Lanes left without an exit: {len(stranded)}", ""]
    if stranded:
        lines += [
            "Nothing can leave these lanes any more - no junction movement and no "
            "continuation. Stage 5 would otherwise be the first to notice.",
            "",
            *(f"- `{lane}`" for lane in stranded),
            "",
        ]
    else:
        lines += [
            "No lane lost its last way out. "
            f"{comparison['lanes_without_an_exit_either_way']} lane(s) had none before "
            "this review and still have none - those run off the edge of the extract.",
            "",
        ]
    if given:
        lines += [
            f"## Lanes this review gave an exit: {len(given)}",
            "",
            *(f"- `{lane}`" for lane in given),
            "",
        ]

    left_open = comparison["left_open_as_not_applicable"]
    if left_open:
        reasons = applied.get("left_open_reasons", {})
        lines += [
            f"## Left open: {len(left_open)}",
            "",
            "Marked not applicable, which says the finding does not describe a defect "
            "here rather than deciding what it names. Nothing was applied, and the "
            "reviewed model asks the question again - deliberately, so a later reader "
            "sees the judgement instead of a silence.",
            "",
            *(
                f"- `{item}`" + (f" - {reasons[item]}" if reasons.get(item) else "")
                for item in left_open
            ),
            "",
        ]
    return "\n".join(lines)


def apply_review(*, workspace: Path, submission: Path, config: ConverterConfig) -> Path:
    """Apply a Stage 3 review to WORKSPACE and regenerate the reviewed lane model."""
    workspace = workspace.resolve()
    manifest, source_path = _check_stage_1_and_2(workspace)
    # Recorded before anything else runs and checked again at the end: the source is
    # acquisition evidence, and no Stage 4 outcome justifies changing it.
    source_before = _sha256(source_path)

    model_path = workspace / "lane-model" / "preliminary.json"
    preliminary = PreliminaryLaneModel.model_validate(
        _read_json(model_path, "preliminary lane model")
    )
    report = _read_json(workspace / "reports" / "lane-model-generation.json", "Stage 2 report")
    payload = _read_json(submission, "review submission")
    decisions = _check_submission(payload, preliminary, report, workspace.name)
    overrides, applied = _overrides_from(decisions, preliminary)

    review_dir = workspace / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    reviewed_osm = review_dir / "reviewed.osm"
    # The reviewer's lane counts land here, on a derived file, and never on the source.
    # That is what lets a lane count be corrected at all: editing `source/map.osm` to fix
    # one would move its checksum and invalidate every decision in the review.
    materialised_tags = _write_reviewed_osm(
        source_path, reviewed_osm, applied["lane_count_overrides"]
    )

    reviewed, counts, fingerprint, selection_audit = _regenerate(
        reviewed_osm=reviewed_osm, manifest=manifest, config=config, overrides=overrides
    )

    lane_model_dir = workspace / "lane-model"
    reports_dir = workspace / "reports"
    inspection_dir = workspace / "inspection"
    for directory in (lane_model_dir, reports_dir, inspection_dir):
        directory.mkdir(parents=True, exist_ok=True)

    reviewed_path = lane_model_dir / "reviewed.json"
    reviewed_path.write_text(
        json.dumps(reviewed.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    comparison = _comparison(
        preliminary=preliminary,
        reviewed=reviewed,
        applied=applied,
        retagged=materialised_tags,
        decisions=decisions,
    )
    comparison_json = reports_dir / "reviewed-comparison.json"
    comparison_json.write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    comparison_md = reports_dir / "reviewed-comparison.md"
    comparison_md.write_text(_markdown(comparison, applied) + "\n", encoding="utf-8")
    comparison_html = inspection_dir / "stage-4-comparison.html"
    comparison_html.write_text(
        render_comparison_html(preliminary=preliminary, reviewed=reviewed, comparison=comparison),
        encoding="utf-8",
    )

    # Named for what it holds. The spec calls this `review/review.json`, one path segment
    # from the hand-made Stage 3 export that is its input; two files a directory apart
    # with the same name is a mistake waiting to be made.
    applied_path = review_dir / "applied-decisions.json"
    applied_path.write_text(
        json.dumps(
            {
                "applied_version": APPLIED_DECISIONS_VERSION,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "submission": payload,
                "non_osm_overrides": applied,
                # What the review actually wrote into reviewed.osm, per way. Empty when
                # no override moved a tag, which is what makes the reviewed OSM a byte
                # copy of the source.
                "materialised_osm_tags": materialised_tags,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    artifacts = {}
    for name, path in (
        ("reviewed_osm", reviewed_osm),
        ("applied_decisions", applied_path),
        ("reviewed_lane_model", reviewed_path),
        ("comparison_json", comparison_json),
        ("comparison_markdown", comparison_md),
        ("comparison_html", comparison_html),
    ):
        artifacts[name] = {
            "path": path.relative_to(workspace).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    manifest["stage_4"] = {
        "status": "passed",
        "generator_version": GENERATOR_VERSION,
        "lane_model_schema_version": LANE_MODEL_SCHEMA_VERSION,
        "generation_fingerprint": fingerprint,
        "input_checksums": {
            "source_osm": source_before,
            "submission": _sha256(submission),
            "preliminary_lane_model": _sha256(model_path),
            "configuration": reviewed.metadata.configuration_checksum,
        },
        "feature_counts": counts,
        "road_selection": selection_audit,
        "decision_counts": {
            "total": len(decisions),
            "forbidden_connectors": len(applied["forbidden_connector_ids"]),
            "activated_connectors": len(applied["activated_connector_ids"]),
        },
        "artifacts": artifacts,
    }
    (workspace / "source" / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if _sha256(source_path) != source_before:
        raise ApplyReviewError(
            "the source OSM changed while the review was applied; it is acquisition "
            "evidence and must never be written"
        )
    return comparison_md
