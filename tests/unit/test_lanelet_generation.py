import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from lanelet2 import io, projection
from typer.testing import CliRunner

from osm_scenario.acquisition import acquire_osm
from osm_scenario.cli import app
from osm_scenario.config import ConverterConfig
from osm_scenario.lanelet_generation import (
    ConnectorCandidate,
    _connector_is_ambiguous,
    _lanelet_attributes,
    _resolve_via_way_restrictions,
    generate_preliminary_lanelet2,
)
from osm_scenario.normalization import normalize_workspace
from osm_scenario.osm_source import OsmRelation, OsmRelationMember, OsmSnapshot, OsmWay

FIXTURE = Path(__file__).parents[1] / "fixtures" / "osm" / "tiny.osm"
runner = CliRunner()


def test_via_way_lanelet_spawn_eligibility_is_explicit() -> None:
    common = {
        "source_way_id": "10",
        "source_u": "1",
        "source_v": "2",
        "lane_index": 0,
        "tags": {"highway": "residential"},
    }
    assert _lanelet_attributes(**common, spawn_eligible=False)["spawn_eligible"] == "no"
    assert _lanelet_attributes(**common, spawn_eligible=True)["spawn_eligible"] == "yes"


def _candidate(from_way: str, to_way: str, node: str, identifier: int) -> ConnectorCandidate:
    source = SimpleNamespace(lanelet=SimpleNamespace(id=identifier * 2), source_way_id=from_way)
    target = SimpleNamespace(lanelet=SimpleNamespace(id=identifier * 2 + 1), source_way_id=to_way)
    return ConnectorCandidate(node, source, target, "through", 0.0)


def _restriction(identifier: str, restriction_type: str, sequence: tuple[str, ...]) -> OsmRelation:
    return OsmRelation(
        identifier,
        (
            OsmRelationMember("way", sequence[0], "from"),
            *(OsmRelationMember("way", way_id, "via") for way_id in sequence[1:-1]),
            OsmRelationMember("way", sequence[-1], "to"),
        ),
        {"type": "restriction", "restriction": restriction_type},
    )


def _resolve(
    relation: OsmRelation,
    candidates: list[ConnectorCandidate],
    *,
    missing: set[str] = frozenset(),
    omitted: dict[tuple[str, str], set[str]] | None = None,
) -> tuple[list[ConnectorCandidate], dict[str, object], list[dict[str, object]], int]:
    way_ids = {
        member.reference
        for member in relation.members
        if member.member_type == "way" and member.reference not in missing
    }
    sequence = [member.reference for member in relation.members if member.member_type == "way"]
    nodes_by_way: dict[str, set[str]] = {way_id: set() for way_id in way_ids}
    for index, (from_way, to_way) in enumerate(zip(sequence, sequence[1:], strict=False)):
        junction = next(
            (
                candidate.node_id
                for candidate in candidates
                if candidate.source_lane.source_way_id == from_way
                and candidate.target_lane.source_way_id == to_way
            ),
            f"chain-{index}",
        )
        if from_way in nodes_by_way:
            nodes_by_way[from_way].add(junction)
        if to_way in nodes_by_way:
            nodes_by_way[to_way].add(junction)
    for candidate in candidates:
        nodes_by_way.setdefault(candidate.source_lane.source_way_id, set()).add(candidate.node_id)
        nodes_by_way.setdefault(candidate.target_lane.source_way_id, set()).add(candidate.node_id)
    snapshot = OsmSnapshot(
        nodes={},
        ways={
            way_id: OsmWay(way_id, tuple(sorted(nodes_by_way[way_id])), {}) for way_id in way_ids
        },
        relations={relation.identifier: relation},
    )
    active, resolutions, corrections, count = _resolve_via_way_restrictions(
        snapshot=snapshot,
        candidates=candidates,
        omitted_node_transitions=omitted or {},
    )
    return active, resolutions[0], corrections, count


def test_via_way_no_restriction_recognizes_already_absent_transition() -> None:
    relation = _restriction("r1", "no_straight_on", ("a", "b", "c"))
    active, resolution, corrections, count = _resolve(
        relation,
        [_candidate("a", "b", "1", 1)],
        omitted={("b", "c"): {"node-r"}},
    )

    assert len(active) == 1
    assert resolution["status"] == "already_satisfied"
    assert resolution["enforcing_relation_ids"] == ["node-r"]
    assert corrections == []
    assert count == 0


def test_via_way_no_restriction_removes_unique_suffix_at_exact_node() -> None:
    relation = _restriction("r2", "no_straight_on", ("a", "b", "c"))
    unrelated = _candidate("a", "exit", "1", 3)
    active, resolution, corrections, count = _resolve(
        relation,
        [_candidate("a", "b", "1", 1), _candidate("b", "c", "2", 2), unrelated],
    )

    assert resolution["status"] == "topology_enforced"
    assert resolution["removed_connector_count"] == 1
    assert unrelated in active
    assert corrections == []
    assert count == 1


def test_via_way_no_restriction_removes_unique_prefix_for_multiple_via_ways() -> None:
    relation = _restriction("r3", "no_straight_on", ("a", "b", "c", "d"))
    active, resolution, _, count = _resolve(
        relation,
        [
            _candidate("a", "b", "1", 1),
            _candidate("b", "c", "2", 2),
            _candidate("c", "d", "3", 3),
            _candidate("c", "exit", "3", 4),
        ],
    )

    assert resolution["status"] == "topology_enforced"
    assert count == 1
    assert len(active) == 3


def test_via_way_branching_history_stays_review_required() -> None:
    relation = _restriction("r4", "no_straight_on", ("a", "b", "c"))
    candidates = [
        _candidate("a", "b", "1", 1),
        _candidate("x", "b", "1", 2),
        _candidate("b", "c", "2", 3),
        _candidate("b", "exit", "2", 4),
    ]
    active, resolution, corrections, count = _resolve(relation, candidates)

    assert active == candidates
    assert resolution["status"] == "review_required"
    assert corrections[0]["source_osm_relation_id"] == "r4"
    assert count == 0


def test_via_way_missing_member_reports_precise_way_ids() -> None:
    relation = _restriction("r5", "no_straight_on", ("a", "missing", "c"))
    _, resolution, corrections, _ = _resolve(relation, [], missing={"missing"})

    assert resolution["missing_way_ids"] == ["missing"]
    assert corrections[0]["missing_way_ids"] == ["missing"]


def test_via_way_disconnected_chain_stays_review_required() -> None:
    relation = _restriction("r6", "no_straight_on", ("a", "b", "c"))
    _, resolution, _, _ = _resolve(
        relation,
        [
            _candidate("a", "b", "1", 1),
            _candidate("a", "b", "9", 2),
            _candidate("b", "c", "2", 3),
        ],
    )
    assert resolution["status"] == "review_required"
    assert "more than one source junction" in str(resolution["topology_proof"])


def test_only_via_way_restriction_recognizes_and_enforces_safe_cases() -> None:
    relation = _restriction("r7", "only_straight_on", ("a", "b", "c"))
    _, satisfied, _, satisfied_count = _resolve(
        relation,
        [_candidate("a", "b", "1", 1), _candidate("b", "c", "2", 2)],
    )
    alternatives = [
        _candidate("a", "b", "1", 1),
        _candidate("b", "c", "2", 2),
        _candidate("b", "exit", "2", 3),
    ]
    active, enforced, _, enforced_count = _resolve(relation, alternatives)

    assert satisfied["status"] == "already_satisfied"
    assert satisfied_count == 0
    assert enforced["status"] == "topology_enforced"
    assert enforced_count == 1
    assert all(item.target_lane.source_way_id != "exit" for item in active)


def test_only_via_way_restriction_with_nonunique_predecessor_requires_review() -> None:
    relation = _restriction("r8", "only_straight_on", ("a", "b", "c"))
    candidates = [
        _candidate("a", "b", "1", 1),
        _candidate("x", "b", "1", 2),
        _candidate("b", "c", "2", 3),
        _candidate("b", "exit", "2", 4),
    ]
    active, resolution, corrections, count = _resolve(relation, candidates)

    assert active == candidates
    assert resolution["status"] == "review_required"
    assert corrections
    assert count == 0


def test_connector_ambiguity_includes_fractional_borderline_angles() -> None:
    assert _connector_is_ambiguous(1, 30.25)
    assert _connector_is_ambiguous(1, -39.75)
    assert not _connector_is_ambiguous(1, 29.99)
    assert not _connector_is_ambiguous(1, 40.01)
    assert _connector_is_ambiguous(2, 12.0)


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "map-workspace"
    source = workspace / "source" / "map.osm"
    source.parent.mkdir(parents=True)
    source.write_bytes(FIXTURE.read_bytes())
    acquire_osm(workspace=workspace, driving_side="left", osm_file=source)
    normalize_workspace(workspace=workspace, config=ConverterConfig(config_version=1))
    return workspace, source


def test_generate_preliminary_lanelet2_is_reloadable_and_traceable(tmp_path: Path) -> None:
    workspace, source = _workspace(tmp_path)
    source_checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    output = generate_preliminary_lanelet2(
        workspace=workspace, config=ConverterConfig(config_version=1)
    )

    assert output == workspace / "lanelet2" / "preliminary.osm"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_checksum
    report = json.loads((workspace / "reports" / "lanelet2-generation.json").read_text())
    assert report["report_version"] == 2
    assert "restriction_resolutions" in report
    assert (
        report["counts"]["unsupported_via_way_restrictions"]
        == report["counts"]["via_way_restrictions_review_required"]
    )
    origin = report["configuration"]["origin"]
    lanelet_map, errors = io.loadRobust(
        str(output),
        projection.LocalCartesianProjector(io.Origin(origin["latitude"], origin["longitude"])),
    )
    assert errors == []
    assert len(lanelet_map.laneletLayer) == (
        report["counts"]["road_lanelets"] + report["counts"]["connector_lanelets"]
    )
    assert report["counts"]["road_lanelets"] > 0
    assert report["counts"]["connector_lanelets"] > 0
    assert report["counts"]["traffic_light_associations"] > 0
    assert report["counts"]["inferred_stop_lines"] > 0
    assert report["parser_errors"] == []
    assert all(
        record["source_osm_way_id"] in {"10", "11"}
        for record in report["lanelets"]
        if record["kind"] == "road"
    )
    assert all(lanelet.leftBound and lanelet.rightBound for lanelet in lanelet_map.laneletLayer)
    assert any(item["code"] == "lane_count_inferred" for item in report["inferences"])
    assert any(item["code"] == "stop_line_inferred" for item in report["inferences"])
    assert (workspace / "reports" / "lanelet2-generation.md").is_file()


def test_generate_preliminary_lanelet2_recreates_with_stable_ids(tmp_path: Path) -> None:
    workspace, source = _workspace(tmp_path)
    source_checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    generate_preliminary_lanelet2(workspace=workspace, config=ConverterConfig(config_version=1))
    first = json.loads((workspace / "reports" / "lanelet2-generation.json").read_text())
    first_ids = [item["lanelet_id"] for item in first["lanelets"]]
    generate_preliminary_lanelet2(workspace=workspace, config=ConverterConfig(config_version=1))
    second = json.loads((workspace / "reports" / "lanelet2-generation.json").read_text())

    assert [item["lanelet_id"] for item in second["lanelets"]] == first_ids
    assert len(first_ids) == len(set(first_ids))
    assert all(identifier > 0 for identifier in first_ids)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_checksum
    manifest = json.loads((workspace / "source" / "manifest.json").read_text())
    assert (
        manifest["stage_2"]["artifacts"]["preliminary_lanelet2"]["sha256"]
        == hashlib.sha256((workspace / "lanelet2" / "preliminary.osm").read_bytes()).hexdigest()
    )


def test_generate_preliminary_lanelet2_reuses_nearby_mapped_stop_line(tmp_path: Path) -> None:
    workspace = tmp_path / "map-workspace"
    source = workspace / "source" / "map.osm"
    source.parent.mkdir(parents=True)
    source.write_text(
        FIXTURE.read_text().replace(
            '  <relation id="20">',
            """  <way id="15">
    <nd ref="1" />
    <nd ref="3" />
    <tag k="road_marking" v="stop_line" />
  </way>
  <relation id="20">""",
        )
    )
    acquire_osm(workspace=workspace, driving_side="left", osm_file=source)
    normalize_workspace(workspace=workspace, config=ConverterConfig(config_version=1))

    generate_preliminary_lanelet2(workspace=workspace, config=ConverterConfig(config_version=1))

    report = json.loads((workspace / "reports" / "lanelet2-generation.json").read_text())
    assert report["counts"]["mapped_stop_lines"] > 0
    assert report["counts"]["inferred_stop_lines"] == 0
    associations = [
        item
        for item in report["correction_queue"]
        if item["code"] == "traffic_signal_association_review"
    ]
    assert associations
    assert all(item["source_osm_stop_line_way_id"] == "15" for item in associations)


def test_generate_lanelet2_cli_runs_stage_2(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)

    result = runner.invoke(app, ["generate-lanelet2", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert "Stage 2 preliminary Lanelet2 created" in result.output
    assert (workspace / "lanelet2" / "preliminary.osm").is_file()
