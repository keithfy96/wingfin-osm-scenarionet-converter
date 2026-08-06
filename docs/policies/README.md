# Converter Policies

This directory documents the versioned policies that control deterministic
conversion decisions. A policy is a locally owned rule set, not an official
OpenStreetMap standard and not an AI classification.

Policy identifiers are recorded in workspace manifests and reports so an output
can be traced to the exact rule set that produced it. A behavioral change must
receive a new policy version rather than silently changing an existing version.

## Available Policies

| Policy | Status | Purpose | Implementation |
| --- | --- | --- | --- |
| [`public-driving-v1`](public-driving-v1.md) | Active | Select public motor-vehicle roads from an OSM snapshot | [`osm_source.py`](../../src/osm_scenario/osm_source.py) |
| [`stage-2-generation-v1`](stage-2-generation-v1.md) | Active | Generate preliminary lane geometry, connectivity, restrictions, signals, and review findings | [`generation.py`](../../src/osm_scenario/generation.py), [`topology.py`](../../src/osm_scenario/topology.py) |

Supporting reference:

- [Stage 2 finding reference](stage-2-finding-reference.md) — exact finding
  triggers, current mosque counts, and Stage 3 visual-review implications.
- [Stage 2 lane-width algorithm](stage-2-lane-width-algorithm.md) — width
  parsing, fallback precedence, generated geometry, and review traceability.
- [Stage 2 lane-count inference](stage-2-lane-count-inference.md) — directional
  lane-count precedence, confidence, findings, and review handling.

## Source Of Truth

The policy pages explain behavior for operators and reviewers. The executable
source code remains authoritative:

- Policy identifier: `ROAD_SELECTION_POLICY_ID` in
  [`src/osm_scenario/osm_source.py`](../../src/osm_scenario/osm_source.py)
- Included road classes: `PUBLIC_DRIVING_HIGHWAYS` in the same module
- Prohibited access values: `PROHIBITED_ACCESS` in the same module
- Selection decision: `road_exclusion_reason()` in the same module
- Source-to-graph audit: `select_public_driving_graph()` in the same module

Tests covering policy selection, toll roads, excluded roads, exact tag
preservation, and direction mismatches are in
[`tests/unit/test_inspection.py`](../../tests/unit/test_inspection.py).

The Stage 2 generation policy is tracked by `GENERATOR_VERSION` and the
generation fingerprint in the workspace manifest. Its focused coverage is in
[`tests/unit/test_generation.py`](../../tests/unit/test_generation.py) and
[`tests/unit/test_topology.py`](../../tests/unit/test_topology.py).

## Versioning Rules

1. Documentation clarification that does not change selection behavior may update
   the existing policy page.
2. Any change to included highway classes, access handling, or exclusion behavior
   requires a new policy identifier and a new page in this directory.
3. Existing workspaces retain their recorded policy identifier.
4. Reports must identify the policy used and summarize included and excluded
   source ways.
5. Old policy implementations must remain available while supported workspaces
   still depend on them.
6. Stage 2 geometry or topology semantic changes require a new generator
   version and generation fingerprint. Review files bound to the old
   fingerprint must be rejected or explicitly migrated.
