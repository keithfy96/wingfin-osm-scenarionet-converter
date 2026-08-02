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

