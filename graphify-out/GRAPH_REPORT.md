# Graph Report - converter-scenarionet-stage2-redesign  (2026-08-18)

## Corpus Check
- 177 files · ~284,910 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2648 nodes · 6183 edges · 149 communities (133 shown, 16 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 502 edges (avg confidence: 0.58)
- Token cost: 736,545 input · 0 output

## Community Hubs (Navigation)
- Stage 3 Review Application
- Review Client UI
- Route Builder Client
- Signal Plan and Light Tape
- Signal Conflict Detection
- Ego Drive-Line Geometry
- Stage 3 Comparison View
- CLI Command Surface
- Stage 2 Lane Model Generation
- Ego Route Tests
- Lane Model Fixtures and Signals
- Lane Allocation and Merge Side
- Lane Block Geometry
- Inspection Map Overlays
- Junction Kerb Painting
- Movement Classification Topology
- Browser Drive-Line Geometry
- Stage 3 Review Payload
- Lane Boundary Styling
- Stage 5 Validation Tests
- Stage 6 Conversion Tests
- Junction Crossings Payload
- Stage 1B Normalization
- Stage 6 Map Feature Export
- Kerb Coverage Tests
- Feature Popups and Details
- MetaDrive Schema Gate
- Topology Unit Tests
- OSM Road Selection
- Review Control Fields
- Review Decision State
- Connector Ambiguity Rules
- Lane Neighbour Tests
- Drive Runner and Logging
- Front-End Build Config
- Stage 6 Convert Pipeline
- Stage 5 Validation Checks
- Review Panel Hooks
- Route Planning Refusals
- Agent Env and IDM Driver
- Route Page Rendering
- Stage 3 Inspection View
- Polyline Geometry Helpers
- Merge Geometry Tests
- Stage 5 Validation View
- Junction Kerb Rings
- Stage 1B Data Audit
- Ego Route Design Notes
- Node Package Manifest
- Finding Locations
- Merge Taper Planning
- Overlay Tests
- Balanced Lane Assignment
- Policy Client and Example
- Stage 1A Acquisition
- Stage 4 Gates and Termini
- Legacy Draft Purging
- Review Page Boot
- Junction Bridge Features
- Lane Painting Tests
- Approach Assignment Notes
- One-Lane One-Way Tests
- Remote Policy Errors
- Stage 3A Legacy Renderer
- Route Search Graph
- Fingerprint and Policy Rules
- Sensors, GPS and Early Logs
- Stage 3 Review Client Notes
- Drive-Line Constants
- Policy Server
- Stage 1 Workflow Notes
- Lanelet2 Stage 4 Validation
- Lane Transition Counts
- Merging Road Corrections
- Movement Classification Policy
- Road Separation Tests
- Legacy Lanelet2 Stage 2
- Lane Mapping Change Notes
- Dataset Check and 3D Terrain
- Signal Association at the Rim
- Junction Interiors
- Blocked Group Allocation
- Off-Ramp Bypass Tests
- Lane Pull Regression Tests
- Inspection Tests
- Lane Payload Tests
- Browser Route Path
- Reference Checkouts and Stage 8
- Reachability Search
- Action Recorder
- Live Signal Control
- Browser Geometry Tests
- Review Decision Identity
- Connector Ambiguity Corrections
- Tag Versus Geometry Authority
- Reviewed OSM Writing
- Routes and Signals at Convert
- One-Lane One-Way Reading
- Merge Join Lines
- Reachability Page Boot
- Browser Route Graph
- Leaflet Bindings
- 3D Runner and Recording
- Stage 6 Convert Contract
- MetaDrive Sanity Check
- Stage Script Helpers
- Converter Config Models
- Block Alignment Tests
- Carriageway Placement Rules
- Export-Time Paint Passes
- Traffic Light Tapes
- Lane Count and Width Inference
- Turn Restriction Enforcement
- Off-Ramp Bypass Evidence
- Movement Roles in Findings
- Dataset Checker
- Connector Candidate Splitting
- Road Alignment Guards
- Opposing Carriageway Separation
- Junction Crossing Tests
- Asset Bundling
- Localized Review Pointers
- Portable Pickling
- Ramp Chain Tests
- Stage 5 Condition Re-derivation
- Point Cloud Viewer
- Details Tests
- Turn Tag Side Authority
- Scenario File Naming
- Restriction Effect Policy
- Lane Direction Arrows
- Forbidden Movement Links
- Speed Defaults
- One-Way Graph Reading
- Bare Road Ends
- Finding Granularity
- drive.sh Script
- Stages 1-3 Script
- Stages 4-6 Script
- sensor-survey.sh Script
- view-point-cloud.sh Script
- Compiled Front-End Assets
- Centreline Polygon Test
- Lane Stub Reporting Test
- CSS Type Declarations
- Lanelet2 Retired
- Project Package Name

## God Nodes (most connected - your core abstractions)
1. `PreliminaryLaneModel` - 115 edges
2. `build_lane_model()` - 80 edges
3. `LaneFeature` - 71 edges
4. `ConverterConfig` - 51 edges
5. `Point2D` - 51 edges
6. `_apply()` - 42 edges
7. `_model()` - 42 edges
8. `ApplyReviewError` - 40 edges
9. `_submission()` - 39 edges
10. `convert_scenario()` - 37 edges

## Surprising Connections (you probably didn't know these)
- `test_lane_model_rejects_numeric_identifier()` --uses--> `PreliminaryLaneModel`  [INFERRED]
  tests/unit/test_generation.py → src/osm_scenario/lane_model.py
- `Mapping-algorithm change log rule` --semantically_similar_to--> `Policy versioning rules`  [INFERRED] [semantically similar]
  CLAUDE.md → docs/policies/README.md
- `TrajectoryIDMPolicy takes only a PointLane` --semantically_similar_to--> `resolve_waits — red-light stops baked into the tape`  [INFERRED] [semantically similar]
  docs/implementation-plan/stage-8-live-traffic.md → CLAUDE.md
- `Why the traffic is live rather than written into the dataset` --semantically_similar_to--> `--lights live versus --lights tape`  [INFERRED] [semantically similar]
  docs/implementation-plan/stage-8-live-traffic.md → CLAUDE.md
- `Stage 5 triage — a judgement is not a silence` --semantically_similar_to--> `Findings, warnings and blockers`  [INFERRED] [semantically similar]
  stage 3,4,5 guide.md → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **The six-stage pipeline hand-off, each stage refusing an unverifiable input** — guide_project_guide_stage_1a, guide_project_guide_stage_1b, docs_policies_stage_2_generation_v1_policy, stage_3_4_5_guide_stage_3, stage_3_4_5_guide_stage_4, stage_3_4_5_guide_stage_5, readme_stage_6_map_features, readme_manifest_chain [EXTRACTED 1.00]
- **The rules that stop a destination lane being fed by nothing** — claude_balanced_approach_assignment, claude_balanced_merge_assignment, claude_merge_side, claude_mapped_lane_index, claude_restricted_groups, claude_link_bypass_way [EXTRACTED 1.00]
- **Export-time passes that decide where paint and tarmac actually are** — claude_sealed_surfaces, claude_uncovered_boundaries, claude_junction_kerb_boundaries, claude_stub_lanes, claude_keep_line_ends [EXTRACTED 1.00]
- **Review decisions survive regeneration only through checksum-bound identity** — docs_ai_action_logs_2026_08_08_18_32_20_stage_3_review_application_evidence_checksum_binding, docs_ai_action_logs_2026_08_08_18_32_20_stage_3_review_application_draft_key_scoping, docs_ai_action_logs_2026_08_09_10_41_00_partial_export_and_draft_recovery_find_recoverable_drafts, docs_ai_action_logs_2026_08_08_19_42_07_review_required_movements_reachable_finding_identifier_scheme, docs_ai_action_logs_2026_08_09_10_16_30_finding_coordinates_location_after_checksum [INFERRED 0.85]
- **Connector pipeline: candidates, restriction resolution, ambiguity, geometry** — docs_ai_action_logs_2026_08_04_03_42_45_via_way_topology_resolution_connector_pipeline_split, docs_ai_action_logs_2026_08_04_03_42_45_via_way_topology_resolution_restriction_proof_engine, docs_ai_action_logs_2026_08_04_02_33_57_connector_angle_ambiguity_fix_connector_is_ambiguous, docs_ai_action_logs_2026_08_05_00_03_00_complete_stage_2_decision_node_detection, docs_ai_action_logs_2026_08_08_19_42_07_review_required_movements_reachable_ambiguity_causes [INFERRED 0.85]
- **How lanes are dealt across a node: proportional mapping, side blocks, collapse findings** — docs_ai_action_logs_2026_08_05_00_03_00_complete_stage_2_proportional_lane_mapping, docs_ai_action_logs_2026_08_09_16_44_44_side_blocks_and_movement_roles_mapped_lane_index, docs_ai_action_logs_2026_08_09_16_44_44_side_blocks_and_movement_roles_side_block_offset, docs_ai_action_logs_2026_08_09_15_12_08_lane_transition_counts_lane_collapse_findings, docs_ai_action_logs_2026_08_05_00_03_00_complete_stage_2_driving_side_turn_lane_indexing [INFERRED 0.85]
- **The three Stage 6 pages are built from one lane payload** — docs_ai_action_logs_2026_08_11_04_38_11_a_route_builder_and_a_scenario_that_drives_lane_payload, docs_ai_action_logs_2026_08_11_00_07_14_stage_6_reachability_page_reachability_view, docs_ai_action_logs_2026_08_11_04_38_11_a_route_builder_and_a_scenario_that_drives_route_builder_view, docs_ai_action_logs_2026_08_11_17_08_45_traffic_lights_placed_at_stage_6_and_driven_live_signal_builder_view [EXTRACTED 1.00]
- **Tooling that lives outside the package because MetaDrive runs Python 3.8 / numpy 1** — docs_ai_action_logs_2026_08_11_02_53_01_the_dataset_opens_where_it_is_meant_to_be_used_portable_pickler, docs_ai_action_logs_2026_08_11_02_53_01_the_dataset_opens_where_it_is_meant_to_be_used_check_dataset_tool, docs_ai_action_logs_2026_08_11_14_09_12_the_3d_view_was_metadrives_terrain_defaults_drive_tool, docs_ai_action_logs_2026_08_11_17_08_45_traffic_lights_placed_at_stage_6_and_driven_live_signal_control_tool [INFERRED 0.85]
- **The lane allocation rule stack at a node** — docs_mapping_algo_changes_2026_08_07_12_03_07_turn_lanes_must_not_strand_a_lane_stranded_permission_fallback, docs_mapping_algo_changes_2026_08_07_12_33_19_a_peeling_lane_cannot_also_be_the_straight_on_lane_balanced_approach_assignment, docs_mapping_algo_changes_2026_08_07_12_34_23_merging_approaches_starve_the_middle_lane_balanced_merge_assignment, docs_mapping_algo_changes_2026_08_07_12_33_19_a_peeling_lane_cannot_also_be_the_straight_on_lane_mapped_lane_index [EXTRACTED 1.00]
- **The lane allocation is blinded to destinations that are about to be deleted** — docs_mapping_algo_changes_2026_08_13_04_42_58_lanes_were_dealt_across_a_destination_a_restriction_forbids_restricted_groups, docs_mapping_algo_changes_2026_08_13_16_32_06_a_turn_an_off_ramp_already_carries_was_offered_twice_link_bypassed_groups, docs_mapping_algo_changes_2026_08_13_04_42_58_lanes_were_dealt_across_a_destination_a_restriction_forbids_balanced_approach_assignment, docs_mapping_algo_changes_2026_08_12_16_36_30_a_one_lane_road_was_given_a_lane_each_way_balanced_merge_assignment [EXTRACTED 1.00]
- **Export-time painting pipeline: kerb, seam closing, surface sealing and paint clipping** — docs_mapping_algo_changes_2026_08_16_02_28_52_a_junction_had_no_kerb_so_a_shader_artifact_stood_in_for_one_junction_kerb_boundaries, docs_mapping_algo_changes_2026_08_16_03_19_30_one_kerb_was_drawn_as_a_chain_of_unequal_lines_kerb_rings, docs_mapping_algo_changes_2026_08_16_03_42_57_the_kerb_painted_the_seams_between_road_surfaces_kerb_gap_close, docs_mapping_algo_changes_2026_08_16_04_50_16_holes_in_the_tarmac_painted_themselves_sealed_surfaces, docs_mapping_algo_changes_2026_08_16_20_01_55_a_turning_lanes_edge_was_painted_through_the_road_beside_it_uncovered_boundaries [EXTRACTED 1.00]
- **Where a lane block sits across its way: survey, then the road behind, then the corridor** — docs_mapping_algo_changes_2026_08_15_22_21_37_a_lane_block_was_centred_where_the_survey_placed_it_lane_offset, docs_mapping_algo_changes_2026_08_16_01_58_09_a_road_that_carries_on_re_centred_on_its_own_line_aligned_blocks, docs_mapping_algo_changes_2026_08_16_14_32_44_a_carriageway_was_laid_over_the_traffic_coming_the_other_way_separated_roads, docs_mapping_algo_changes_2026_08_16_18_10_01_a_two_way_street_was_parted_down_its_own_middle_two_way_roads [EXTRACTED 1.00]

## Communities (149 total, 16 thin omitted)

### Community 0 - "Stage 3 Review Application"
Cohesion: 0.06
Nodes (126): apply_review(), ApplyReviewError, _check_override_value(), _check_stage_1_and_2(), _check_submission(), _comparison(), _decision_is_satisfied(), _decisions_by_reviewed_finding() (+118 more)

### Community 1 - "Review Client UI"
Cohesion: 0.08
Nodes (62): A(), ae(), allDecisions(), C(), chipRow(), clearFilters(), constructor(), de() (+54 more)

### Community 2 - "Route Builder Client"
Cohesion: 0.08
Nodes (61): Ae(), B(), be(), constructor(), $e(), ee(), Fe(), find() (+53 more)

### Community 3 - "Signal Plan and Light Tape"
Cohesion: 0.07
Nodes (60): colour_at(), light_states(), PhaseGroup, plan_metadata(), _positive(), Any, Path, RuntimeError (+52 more)

### Community 4 - "Signal Conflict Detection"
Cohesion: 0.07
Nodes (52): Conflict, ConflictKind, findConflicts(), greenOverlapSeconds(), orientation(), pathsCross(), Segment, segmentsCross() (+44 more)

### Community 5 - "Ego Drive-Line Geometry"
Cohesion: 0.08
Nodes (51): _advance_past(), _arrival_times(), _bezier(), _cut(), _handle_fraction(), _headings(), _lane_change(), _length_of() (+43 more)

### Community 6 - "Stage 3 Comparison View"
Cohesion: 0.10
Nodes (48): _blocker_verdict(), _decision_label(), _finding_rows(), _findings(), Any, How to badge one finding: what was decided, and what number that approved.…, A short reading of an approved value, or None for shapes with no short reading.…, The reviewed model's findings, blockers first and anything undecided ahead of… (+40 more)

### Community 7 - "CLI Command Surface"
Cohesion: 0.08
Nodes (41): callback, command, dir_okay, Enum, exists, help, min, Option (+33 more)

### Community 8 - "Stage 2 Lane Model Generation"
Cohesion: 0.08
Nodes (48): _approach_blocks(), build_lane_model(), _closest_on(), _directional_lane_count(), _edge_direction(), _edge_geometry(), _finding(), GenerationError (+40 more)

### Community 9 - "Ego Route Tests"
Cohesion: 0.09
Nodes (45): ego_track(), The recorded car, resampled at MetaDrive's own step. Shape read off…, _chain(), _light(), _plan(), _plan_with(), Any, Turning two chosen lanes into the car MetaDrive drives. The geometry assertions… (+37 more)

### Community 10 - "Lane Model Fixtures and Signals"
Cohesion: 0.10
Nodes (44): ConverterConfig, generate_lane_model(), Path, Generate deterministic preliminary lane geometry from Stage 1 artifacts., _sha256(), PreliminaryLaneModel, _link_bypass_model(), _live_targets() (+36 more)

### Community 11 - "Lane Allocation and Merge Side"
Cohesion: 0.08
Nodes (42): _mapped_lane_index(), _merge_side(), Which lanes a signal governs, and what the reviewer must be told about it.…, Choose which lane of the outgoing group a movement lands in. Indices run…, Which side of a shared destination an approach merges onto. A road that joins…, _signal_association(), _block(), _groups() (+34 more)

### Community 12 - "Lane Block Geometry"
Cohesion: 0.07
Nodes (40): AbstractSet, BlockKey, _aligned_blocks(), _as_line(), _join_turn_degrees(), _lane_block(), _lane_samples(), _lane_surface() (+32 more)

### Community 13 - "Inspection Map Overlays"
Cohesion: 0.06
Nodes (19): BAND_STATUSES, buildOverlays(), DEFAULT_ON, ENTRY_HIGHLIGHT, EXIT_HIGHLIGHT, FocusPlan, GENERATED_HIGHLIGHT, LAYER_LABELS (+11 more)

### Community 14 - "Junction Kerb Painting"
Cohesion: 0.06
Nodes (38): conversion._exported_links / _connector_feature / _bridge_feature, MetaDrive builds road only from lane features, so a turn must be written as one, A junction is bare inside and kerbed outside, A junction had no kerb, and a shader artifact stood in for one, Export-time only, so generation_fingerprint does not move and the Stage 3 review stays bound, conversion._junction_kerb_boundaries, terrain.frag.glsl white value band hairline, _uncreased and _MAX_KERB_TURN_DEG (+30 more)

### Community 15 - "Movement Classification Topology"
Cohesion: 0.08
Nodes (37): _kerb_first_key(), _link_bypass_way(), The off-ramp that already serves this movement, or None if none does. Both ends…, Rank a movement by how far it turns toward the kerb, most kerbward first., classify_movement(), connector_curve(), forbidden_by_node_restriction(), _leg_collateral() (+29 more)

### Community 16 - "Browser Drive-Line Geometry"
Cohesion: 0.11
Nodes (37): advancePast(), bezier(), CHANGE_MAX_FRACTION, COINCIDENT_M, cutMetres(), dropRepeats(), handleFraction(), Join (+29 more)

### Community 17 - "Stage 3 Review Payload"
Cohesion: 0.13
Nodes (34): build_review_payload(), Projected features, findings and counts shared by the Stage 2 audit and Stage 3…, build_payload(), _center(), client_source(), _identity(), Any, Path (+26 more)

### Community 18 - "Lane Boundary Styling"
Cohesion: 0.10
Nodes (36): LaneBoundary, _built(), _lane(), `edges` and `merged` are counted by type, so a kerb left in them would corrupt…, A lane and the turn leaving it are meant to touch, so the turn never clips its…, `_side_by_side()` with boundaries, so the line between the two lanes has a…, The line style is `_lane_change_moves` drawn, not a second opinion about it.…, Both lanes carry the same line, and two copies of a broken line render as a… (+28 more)

### Community 19 - "Stage 5 Validation Tests"
Cohesion: 0.15
Nodes (34): Point2D, _codes(), _connector(), _lane(), _model(), Any, Stage 5 — validating the reviewed map. Each check gets one negative that names…, Pydantic accepts inf and nan for a bare float, so nothing upstream catches this. (+26 more)

### Community 20 - "Stage 6 Conversion Tests"
Cohesion: 0.08
Nodes (32): _lane_neighbours(), Each lane's entries and exits, as lane ids only. A connector reference is…, OSM scenario conversion tools., _connector(), _modules_named_in(), Stage 6 — converting the validated map into a map-only ScenarioNet dataset. The…, Solid, like the lane edge it takes over from, and with no `lane_id` to be wrong…, `ScenarioDescription.METADATA_KEYS`, and the shape `sanity_check` reads off… (+24 more)

### Community 21 - "Junction Crossings Payload"
Cohesion: 0.09
Nodes (30): _bend_deg(), junction_crossings(), _junction_nodes(), Nodes where roads meet, as opposed to where one road is merely split in two.…, How far the road turns between the end of one lane and the start of the next., Every lane-to-lane step that crosses a junction, as `(from_lane_id,…, build_lane_payload(), edge_name() (+22 more)

### Community 22 - "Stage 1B Normalization"
Cohesion: 0.14
Nodes (31): CRS, _markdown_report(), NormalizationError, normalize_workspace(), _osm_identifier(), _positive_int(), _preflight(), _project_graph() (+23 more)

### Community 23 - "Stage 6 Map Feature Export"
Cohesion: 0.12
Nodes (32): _connector_feature(), _divider_boundaries(), _lane_feature(), _map_features(), MapFeatures, _polyline(), Any, DiGraph (+24 more)

### Community 24 - "Kerb Coverage Tests"
Cohesion: 0.11
Nodes (33): _end_alignment(), _kerbs(), Any, LineString, Polygon, The defect this whole pass exists to remove, asserted at zero. One physical…, A bar of paint across a carriageway is a stop line, and there is no stop line…, The test that would have caught this. A residual run can survive where a hole… (+25 more)

### Community 25 - "Feature Popups and Details"
Cohesion: 0.19
Nodes (15): buildPopup(), FeatureIndex, isConnector(), laneNumbers(), LinkRow, linkTable(), PopupHooks, Properties (+7 more)

### Community 26 - "MetaDrive Schema Gate"
Cohesion: 0.12
Nodes (29): skipif, _load_metadrive_schema(), _model(), _pickled(), Path, Lane `a` joins lane `b` through connector `c`, and `b` continues into `d`. So…, MetaDrive's `ScenarioDescription`, loaded from a checkout without installing…, The gate that stops this converter drifting from the format it targets.… (+21 more)

### Community 27 - "Topology Unit Tests"
Cohesion: 0.12
Nodes (28): Index of the lane on `side` of a carriageway of `lane_count` lanes. Indices run…, Classify a plausible U-turn from lane-tag evidence alone., side_lane_index(), uturn_evidence_status(), candidate(), Member, parametrize, Resolve against the adjacency the candidates themselves describe. (+20 more)

### Community 28 - "OSM Road Selection"
Cohesion: 0.13
Nodes (27): Element, _against_the_grain(), _apply_single_lane_oneway(), _audit_selected_graph(), _expected_directions(), _flood(), _is_deleted(), _osmid_values() (+19 more)

### Community 29 - "Review Control Fields"
Cohesion: 0.11
Nodes (23): ChoiceField, CLEAR_EFFECT, controlFor(), ControlSpec, FALLBACK, IGNORE_EFFECT, LanePickerField, NOT_APPLICABLE_EFFECT (+15 more)

### Community 30 - "Review Decision State"
Cohesion: 0.14
Nodes (14): ReviewState, validateDecision(), ConnectorSummary, Decision, DecisionStatus, FindingLocation, FindingSource, GeoPoint (+6 more)

### Community 31 - "Connector Ambiguity Rules"
Cohesion: 0.14
Nodes (27): _ambiguity_causes(), _ambiguity_reason(), How far inside the block's leading lane this lane sits, in lane widths. A block…, Keep only the movements this source lane is on the correct side of the road…, Return the movement to restore when `turn:lanes` rejected every candidate.…, True when a movement doubles back far enough to need the evidence a U-turn…, Which ambiguity triggers a movement fired, most decision-relevant first. These…, One sentence naming why this movement needs review, from its headline cause. (+19 more)

### Community 32 - "Lane Neighbour Tests"
Cohesion: 0.10
Nodes (26): _lane_change_moves(), Each lane's side-by-side neighbours - the lanes a car can move across into. OSM…, _payload(), MonkeyPatch, `_model()` with a second lane running alongside `a`, so lane changes have a…, It would be a drivable edge straight into oncoming traffic., `left_neighbor` means alongside. Anything else would teleport a car down the…, `exit_lanes` means where the lane leads. Moving sideways is not that. The… (+18 more)

### Community 33 - "Drive Runner and Logging"
Cohesion: 0.12
Nodes (23): Structured logging configuration., A ScenarioEnv an external policy can drive, a recorder for what it did, and a…, _baked_stops(), _ground_around(), _keep_line_ends(), _longest_red(), main(), _max_texture_dimension() (+15 more)

### Community 34 - "Front-End Build Config"
Cohesion: 0.08
Nodes (24): build.mjs, DOM, DOM.Iterable, ES2022, src/**/*.ts, test/**/*.ts, vitest.config.ts, vitest/globals (+16 more)

### Community 35 - "Stage 6 Convert Pipeline"
Cohesion: 0.13
Nodes (25): _check_stage_5(), ConversionError, convert_scenario(), Path, RuntimeError, The plan flattened to one entry per signalled lane, for the route builder. The…, The routes drawn in the route builder, refused unless they were drawn on this…, The map-only scenario, plus the car that turns it into a drive. A shallow copy… (+17 more)

### Community 36 - "Stage 5 Validation Checks"
Cohesion: 0.19
Nodes (24): _boundary_report(), _connector_issues(), _dispositioned_osm_ids(), _finite(), _geometry_issues(), _issue(), _line(), _markdown_report() (+16 more)

### Community 37 - "Review Panel Hooks"
Cohesion: 0.18
Nodes (6): applyFilters(), PanelHooks, readFields(), ReviewPanel, Finding, ReviewPayload

### Community 38 - "Route Planning Refusals"
Cohesion: 0.11
Nodes (24): RuntimeError, Raised when a chosen start and end cannot become a route., Refuse a drive that snaps round, however it got that way. The mirror of…, The path a car actually drives along a chain of lanes. Junction movements…, _refuse_reversals(), route_polyline(), RouteError, _lane() (+16 more)

### Community 39 - "Agent Env and IDM Driver"
Cohesion: 0.12
Nodes (19): IdmDriver, make_env(), MetaDrive's own `TrajectoryIDMPolicy`, called from outside and fed through…, A `ScenarioEnv` over a converted dataset, with the terrain settings that fit an…, aeqd_inverse(), geodesic_direct(), projection_origin(), Turn a converted dataset's metres back into latitude and longitude, with no… (+11 more)

### Community 40 - "Route Page Rendering"
Cohesion: 0.15
Nodes (20): CHANGE, END, FADED, IDLE, REACHABLE, ROUTE, START, nameProblem() (+12 more)

### Community 41 - "Stage 3 Inspection View"
Cohesion: 0.22
Nodes (22): InspectionView, _audit_map_data(), _feature(), _feature_collection(), generate_inspection(), _graph_edge_layers(), InspectionError, _json_for_script() (+14 more)

### Community 42 - "Polyline Geometry Helpers"
Cohesion: 0.12
Nodes (23): _drop_repeats(), Collapse coincident consecutive points. Lanes meet where one ends and the next…, _corner(), _free(), _headings(), ndarray, A lane along any line, for the geometry cases a horizontal one cannot express., `n` runs north and `e` runs east, meeting at a 90° left turn. The two lane… (+15 more)

### Community 43 - "Merge Geometry Tests"
Cohesion: 0.09
Nodes (23): _generated_models(), _merge_stream(), _merging_lanes(), LineString, What Keith reported about v26, asserted where it happened. A way carrying both…, The line a car drives: the approach lane, the connector, then the lane it…, Every merge the geometry owns, as (subject, the end that moves, anchor, the end…, The v12 case, which every later change to this geometry has to keep working. (+15 more)

### Community 44 - "Stage 5 Validation View"
Cohesion: 0.14
Nodes (21): _lonlat(), Transformer, Leaflet wants [lat, lon]; the transformer yields (lon, lat) under always_xy., _issue_features(), _issue_rows(), Any, The generated features an issue points at, so a row can highlight something., Issues grouped by code, each row naming the OSM feature it came from. (+13 more)

### Community 45 - "Junction Kerb Rings"
Cohesion: 0.15
Nodes (21): _closed(), _end_squareness(), _extended(), _junction_kerb_boundaries(), _kerb_rings(), LineString, MultiPolygon, ndarray (+13 more)

### Community 46 - "Stage 1B Data Audit"
Cohesion: 0.22
Nodes (20): OsmSnapshot, _component_report(), generate_stage1b_data_audit(), _graph_osm_ids(), _is_stop_line_candidate(), _markdown_report(), Any, MultiDiGraph (+12 more)

### Community 47 - "Ego Route Design Notes"
Cohesion: 0.12
Nodes (19): ConnectorFeature.centerline is a marker, not a driving line, ego_route drive-line construction, The gym contract — env.step is the tick, resolve_waits — red-light stops baked into the tape, signal_plan — the traffic-light clock, speed_profile — curvature-capped drive speed, 30-degrees-per-step heading gate, _turn_reserve — every manoeuvre leaves room for the next (+11 more)

### Community 48 - "Node Package Manifest"
Cohesion: 0.11
Nodes (18): typescript, vitest, description, devDependencies, esbuild, typescript, vitest, esbuild (+10 more)

### Community 49 - "Finding Locations"
Cohesion: 0.16
Nodes (17): FindingLocation, _finding_location(), The OSM ways and nodes a finding points at. A relation carries no geometry of…, Where a finding is, in WGS84, with the referenced geometry copied in. OSM node…, _source_refs(), FindingLocation, FindingSource, GenerationMetadata (+9 more)

### Community 50 - "Merge Taper Planning"
Cohesion: 0.20
Nodes (18): _lane_collapse_findings(), _merge_taper_plan(), Where a merging lane's free end has to land, and which lane it is landing on.…, Decide which lane ends should be pulled onto the lane they merge into. A lane…, Where the lane mapping put several approach lanes onto one destination lane.…, TaperTarget, ConnectorFeature, _collapse_connector() (+10 more)

### Community 51 - "Overlay Tests"
Cohesion: 0.11
Nodes (5): FEATURES, map, overlayControl, overlayHandlers, StubLayer

### Community 52 - "Balanced Lane Assignment"
Cohesion: 0.15
Nodes (17): _balanced_approach_assignment — one approach across several destinations, _balanced_merge_assignment — several approaches into one destination, Plan-view junction diagram requirement, Centre-out lane index convention, _link_bypass_way — an off-ramp already carries the turn (v22), _mapped_lane_index — a side picks where a block starts, _merge_side — which side a road joins from (v20), _restricted_groups — restrictions known before lanes are dealt (v21) (+9 more)

### Community 53 - "Policy Client and Example"
Cohesion: 0.15
Nodes (11): main(), The stage 7b loop, runnable, with MetaDrive's own IDM standing in for a model.…, encode_array(), Drive the ego from a model hosted in another process, over localhost. from…, Reads the named sensors off a live env each step, ready to go on the wire.…, Re-read the projection for the scenario that has just been loaded., What the server is told once, in `/spec`, about what it is going to receive., The MetaDrive `sensors` entries the named sensors need. `{}` when none do.… (+3 more)

### Community 54 - "Stage 1A Acquisition"
Cohesion: 0.22
Nodes (16): acquire_osm(), AcquisitionError, _configure_preserved_tags(), _package_version(), _prepare_local_source(), Any, GeoDataFrame, Path (+8 more)

### Community 55 - "Stage 4 Gates and Termini"
Cohesion: 0.15
Nodes (17): OSM nodes where no source way continues through. A node is a terminus when it…, way_terminus_nodes(), _check_stage_4(), Path, RuntimeError, Gate 1 - the model on disk is the one Stage 4 signed, unchanged since. Checked…, Gate 2 - Stage 4's report is present and new enough to say what was decided.…, OSM nodes where no source way continues through - see `osm_source`. The verdict… (+9 more)

### Community 56 - "Legacy Draft Purging"
Cohesion: 0.18
Nodes (8): DraftStore, purgeLegacyDrafts(), DecisionError, IDENTITY, memoryStore(), payload(), clock(), state()

### Community 57 - "Review Page Boot"
Cohesion: 0.24
Nodes (12): boot(), download(), injectStyles(), clearFocus(), focusFeatures(), assert(), compareIdentity(), IdentityRelation (+4 more)

### Community 58 - "Junction Bridge Features"
Cohesion: 0.13
Nodes (14): _already_meet(), _bend_between(), _bridge_feature(), _exported_links(), _gap_ahead(), Whether the two lanes touch at the end the movement uses, so nothing spans them., How far ahead the next lane starts, and how far away it is. The first is the…, Radians a car must swing through between the end of one lane and the start of… (+6 more)

### Community 59 - "Lane Painting Tests"
Cohesion: 0.21
Nodes (16): _alongside(), _band(), _line(), _painted(), A lane surface centred on `y` rather than on zero., One straight lane, with one or more lanes of another way laid over its left…, Every painted line that belongs to a lane, by id, with the length it was drawn…, The defect Keith reported, in both directions at once. A merging lane and the… (+8 more)

### Community 60 - "Approach Assignment Notes"
Cohesion: 0.17
Nodes (15): ambiguous_connector finding, _approach_blocks, _balanced_approach_assignment, Destinations were asked one at a time, _mapped_lane_index proportional mapping, approach_assignments (shared allocation slot), _balanced_merge_assignment, _kerb_first_key (+7 more)

### Community 61 - "One-Lane One-Way Tests"
Cohesion: 0.22
Nodes (14): Whether `lanes=1` on this way should be read as one lane in one direction. A…, single_lane_implies_oneway(), _by_id(), parametrize, Path, `_expected_directions` has to learn the same rule or every applied way is an…, _selected(), test_a_bare_single_lane_way_is_read_as_one_way() (+6 more)

### Community 62 - "Remote Policy Errors"
Cohesion: 0.22
Nodes (7): PolicyError, RuntimeError, A hosted model, called as `policy(observation) -> [steering, throttle]`. Drops…, Tell the server once what it is about to be sent. A server may not implement it., Everything MetaDrive would have swallowed, refused here instead., The hosted policy could not be reached, or answered with something undrivable., RemotePolicy

### Community 63 - "Stage 3A Legacy Renderer"
Cohesion: 0.15
Nodes (14): --checkpoint preliminary CLI contract, Checksum binding against the Stage 2 manifest, lanelet_inspection.py Stage 3A renderer, Stage 3A artifact isolation from Stage 3C files, Five high-priority review overlay categories, Inferred lane-count and width excluded from visual overlays, Review-record to geometry mapping strategy, Identifier-to-string normalization in GeoJSON properties (+6 more)

### Community 64 - "Route Search Graph"
Cohesion: 0.14
Nodes (14): _densify(), _graph(), plan_route(), DiGraph, A light the route may pass: where it stops the car, and the plan that governs…, The drivable graph, weighted by how far taking each step actually travels.…, The shortest drive from `start_lane` to `end_lane`, or why there isn't one.…, The same line with a vertex every `spacing` metres, and how far along each one… (+6 more)

### Community 65 - "Fingerprint and Policy Rules"
Cohesion: 0.18
Nodes (13): generation_fingerprint, Mapping-algorithm change log rule, --skip-fetch keeps a review bound, Surveyed tags outrank inferred angles, ids.deterministic_id() stable string IDs, Test and acceptance plan, Policy versioning rules, REVIEW_PRIORITY — hardest judgement first (+5 more)

### Community 66 - "Sensors, GPS and Early Logs"
Cohesion: 0.15
Nodes (13): GPS by inverting the azimuthal-equidistant projection, LidarStateObservation 161-float layout, Hosted-model socket and the TCP_NODELAY trap, Stage 0 project foundation action log, Stage 1A acquisition action log, Local azimuthal-equidistant projection and round-trip check, Stage 1B projection and preflight action log, RemotePolicy / policy_server — the model socket (+5 more)

### Community 67 - "Stage 3 Review Client Notes"
Cohesion: 0.17
Nodes (13): build_review_payload shared payload builder, inspect --view review Stage 3 view, review.py Stage 3 HTML shell and identity binding, Road-class scoping for bulk actions, web/ TypeScript esbuild review client, details.ts FeatureIndex and lane labelling, Popups built as DOM nodes, not HTML strings, Selection is yellow only; state lives elsewhere (+5 more)

### Community 68 - "Drive-Line Constants"
Cohesion: 0.15
Nodes (13): ConnectorFeature.centerline is a marker, not a driving line, A lane change is positioned by projection; a run of them is one manoeuvre, MAX_CROSSING_M vs MAX_JOIN_M gap allowance, _refuse_reversals (MAX_VERTEX_TURN_DEG 150), speed_profile (curvature as turn per metre), _turn (cubic built from the two lanes' tangents), COINCIDENT_M raised to 1e-3 m, Counting refusals is not the same as counting faults (+5 more)

### Community 69 - "Policy Server"
Cohesion: 0.21
Nodes (12): act(), _backend(), build_handler(), decode_array(), _flat(), main(), Host a driving model in your own process. Edit `act` and run this on any…, The observation as a plain list of floats, whatever shape it arrived in. (+4 more)

### Community 70 - "Stage 1 Workflow Notes"
Cohesion: 0.18
Nodes (12): single_lane_implies_oneway — lanes=1 with no oneway, Stage inspection workflow action log, Visual checkpoints after each stage for fault isolation, Stage 1B data audit action log, stage1b_data_audit module, public-driving-v1 road selection policy, read_osm_snapshot — the source snapshot boundary, Executable source code is authoritative over policy pages (+4 more)

### Community 71 - "Lanelet2 Stage 4 Validation"
Cohesion: 0.17
Nodes (12): lanelet2/preliminary.osm artifact, Explicit signed review waivers for warnings, lanelet_validation.py Stage 4 validator, Unavailable pinned native lanelet2_validate is a blocking error, Stage 4 validation gate (errors plus unwaived warnings), Index findings by coordinate to score a VLM against Keith's answers, Orphaned draft key defect: stricter export gate than the plan, Partial export: review.partial.json when not ready (+4 more)

### Community 72 - "Lane Transition Counts"
Cohesion: 0.18
Nodes (12): lane_transition_count_mismatch compared two roads' widths, not the movement, _lane_collapse_findings, Count the movement after it is final, not the ways before it exists, lane_transition_count_mismatch finding rule, A side said where every lane went, not where the block started, _mapped_lane_index, _side_block_offset, A side fixes where a block starts, and the lanes behind it follow inward (+4 more)

### Community 73 - "Merging Road Corrections"
Cohesion: 0.18
Nodes (12): A merging road crossed the lane it was joining, and the merge hauled it back, JoinLine / _join_line / _road_behind, The overshoot is usually not in the lane the merge owns, so the walk goes back through single continuations, A merge correction is a sideways pull, never a bend, TaperTarget and _merge_taper_plan anchor lane, _uncrossed_lanes, A lane block was centred on the way line where the survey had placed it, _lane_offset (+4 more)

### Community 74 - "Movement Classification Policy"
Cohesion: 0.18
Nodes (12): ambiguous_connector, Movement classification by signed heading change, Decision node — continuations versus intersection connectors, U-turn policy, ConnectorFeature, Continuation versus connector, Findings, warnings and blockers, A reappearing finding is not open work (+4 more)

### Community 75 - "Road Separation Tests"
Cohesion: 0.20
Nodes (12): _carriageway(), A block of `lane_count` lanes running due east or west, centred on `y =…, `_separated_roads` over hand-built lanes; returns how far north each lane ended…, A one-lane link laid on top of the carriageway coming the other way. The link…, Each exclusion, on the shape that made it necessary., The unit that moves has to be coarser than an alignment component. Two edges of…, Keith's report on v26: 22 of mosque's two-way ways split open along their own…, _run_separation() (+4 more)

### Community 76 - "Legacy Lanelet2 Stage 2"
Cohesion: 0.20
Nodes (11): Explicit OSM evidence outranks configured defaults, generate-lanelet2 CLI command, lanelet_generation.py Stage 2 preliminary generator, Deterministic straight-normal offset fallback, legacy-stage2-implementation archival branch, Pydantic forbid-extra rejects removed downstream configuration, Stage-1-only baseline reset in redesign worktree, generate-map CLI command (+3 more)

### Community 77 - "Lane Mapping Change Notes"
Cohesion: 0.20
Nodes (11): lane_transition_count_mismatch review finding, Deterministic proportional lane mapping across lane-count changes, Assert lanes and connectors byte-identical across a reporting change, _lane_collapse_findings measured over surviving movements, Lane starvation left uncovered by the narrowed rule, Measure first: count real links before changing the rule, Answer a blast-radius question with a census, not an argument, _mapped_lane_index sideways-teleport defect (+3 more)

### Community 78 - "Dataset Check and 3D Terrain"
Cohesion: 0.20
Nodes (11): tools/check_dataset.py, numpy._core pickle boundary between numpy 2 and numpy 1, _PortablePickler, tools/drive.py, height_scale default 50 (sinking and flying), map_region_size measured from the dataset, Road-surface texture over the GL size ceiling, _set_semantic_detail runtime monkeypatch (+3 more)

### Community 79 - "Signal Association at the Rim"
Cohesion: 0.18
Nodes (11): A signal at the edge of the extract governs the lanes it releases, A feature at the rim of the extract is not a defect and must not be asked as one, ReviewOverrides.signal_lane_associations, _signal_association, GENERATOR_VERSION held because the changed case always moves its own finding id, way_terminus_nodes, The adjacency is an upper bound on purpose: over-counting sends a restriction to review, topology.way_adjacency (+3 more)

### Community 80 - "Junction Interiors"
Cohesion: 0.20
Nodes (11): Junctions had no interior, so the turns had nowhere to go, topology.connector_curve, A junction must have an interior for a turn to cross, _node_setbacks / _trimmed_edge, ego_route.route_polyline junction crossing allowance, lane_payload.build_lane_payload crossings, A road running straight through a junction still crosses it, ego_route.junction_crossings (+3 more)

### Community 81 - "Blocked Group Allocation"
Cohesion: 0.24
Nodes (11): GroupKey, _balanced_approach_assignment(), _balanced_merge_assignment(), _is_exact_reverse(), _link_bypassed_groups(), Destinations a node-via restriction forbids this approach at this node. The…, Destinations an off-ramp already serves, hidden from the two balanced rules.…, Deal an approach's lanes across its destinations when the arithmetic closes. A… (+3 more)

### Community 82 - "Off-Ramp Bypass Tests"
Cohesion: 0.24
Nodes (11): _bypass_findings(), _movements(), The bypass warnings, keyed on the connector each one names., Case A. Ramp 130 leaves way 100 at node 10 and comes out at node 12, which is…, Case B, and the reason the rejoin test is the tight one. Ramp 230 ends at node…, Case C. Ramp 350 leaves way 300 at node 30 and rejoins at node 32, where way…, Case D. Node 4 is signalised, so way 400 bending into way 420 is a movement…, test_a_ramp_never_leaves_a_lane_with_nowhere_to_go() (+3 more)

### Community 83 - "Lane Pull Regression Tests"
Cohesion: 0.27
Nodes (11): _overshooting_ramp(), _pull(), A lane placed by hand, with the single lane it continues from named., The regression that reverted the previous attempt. Drawing the correction as a…, Two carriageways of different widths, mapped as separate ways. `mosque` way…, _road_lane(), test_a_road_running_parallel_off_the_line_is_left_alone(), test_a_road_that_never_approached_from_a_side_is_left_alone() (+3 more)

### Community 84 - "Inspection Tests"
Cohesion: 0.45
Nodes (10): Path, test_a_way_deleted_in_an_editor_is_not_a_road_that_went_missing(), test_audit_view_maps_stage_1b_findings_and_discloses_later_checks(), test_inspect_cli_accepts_audit_view(), test_inspect_cli_reports_output(), test_normalized_view_contains_only_projected_layer(), test_source_audit_filters_non_driving_ways_and_preserves_all_tags(), test_source_view_omits_projected_layer_features() (+2 more)

### Community 85 - "Lane Payload Tests"
Cohesion: 0.25
Nodes (10): _payload(), The payload both Stage 6 pages draw from. There is one assertion here worth…, Equal to `junction_crossings`, not merely overlapping it. The page judges a…, Otherwise the field would be redundant and the old behaviour would have been…, A pair keyed on something the page has no lane for is silently ignored by it., `crossings` is a list of lists on purpose: a tuple is not JSON, and a set is…, test_every_crossing_names_lanes_the_payload_actually_contains(), test_the_crossings_are_more_than_the_connectors_the_page_can_see() (+2 more)

### Community 86 - "Browser Route Path"
Cohesion: 0.24
Nodes (10): projector(), refuseReversals(), routeGeometry, crossingKey(), FoundRoute, LANE_CHANGE_COST_M, laneLength(), lineLength() (+2 more)

### Community 87 - "Reference Checkouts and Stage 8"
Cohesion: 0.20
Nodes (10): The ray lidar block is blind on a one-car scenario, _PortablePickler — numpy 2 to numpy 1 pickle stream, MetaDrive and ScenarioNet reference checkouts, Direct OSM-to-ScenarioNet pipeline plan, Source-of-truth hierarchy, Stage 8 — live traffic around the ego, Stage 1 artifact ownership, The manifest checksum hand-off chain (+2 more)

### Community 88 - "Reachability Search"
Cohesion: 0.22
Nodes (10): entry_lanes / exit_lanes hold two kinds of id, _lane_neighbours (connector ids swapped for lane ids), Python twin of the page's breadth-first search, reachability_view.py, The search runs in the browser over the graph the dataset was built from, inspection/stage-6-reachability.html, lane_payload.py (shared by the Stage 6 pages), The preview must measure the same line, not estimate it (+2 more)

### Community 89 - "Action Recorder"
Cohesion: 0.20
Nodes (4): ActionRecorder, `(observation, executed action)` pairs, one per `env.step()`, for imitation…, True when no policy ever wrote an action - a recording of a car nobody drove., Write an `.npz` and return what went into it, or None when nothing was recorded.

### Community 90 - "Live Signal Control"
Cohesion: 0.22
Nodes (9): build_manager(), colour_at(), live_signal_env(), _plan_of(), Drive a converted dataset's traffic lights live, instead of replaying a fixed…, `ScenarioEnv` that registers the live manager instead of the stock one.…, The colour a group shows `seconds` into the episode. The third implementation…, `metadata.signals`, or None when the dataset was built without `--signals`. (+1 more)

### Community 91 - "Browser Geometry Tests"
Cohesion: 0.22
Nodes (7): cut(), CORNER, CORNER_CROSSING, CROSSINGS, headings(), LANES, worstTurnDeg()

### Community 92 - "Review Decision Identity"
Cohesion: 0.22
Nodes (9): Ranked correction queue for uncertain results, Decision state model: three terminal values plus unresolved, Draft key: workspace, source checksum and generation fingerprint, Every decision stores the finding's evidence_checksum, ControlSpec acceptEffect / overrideEffect contract, Finding identifiers exclude the reason; evidence_checksum covers it, Assign location after evidence_checksum so prior reviews still import, findRecoverableDrafts() cross-generation draft recovery (+1 more)

### Community 93 - "Connector Ambiguity Corrections"
Cohesion: 0.25
Nodes (9): Borderline turn-angle interval 30 to 40 degrees, _connector_is_ambiguous() helper, Do not suppress warnings without a deterministic rule preserving legal movements, Connector candidate explosion correction (v3), Branch/control/restriction-based decision-node detection, Ambiguous connectors stay review-required rather than silently activated, topology.py connector geometry and movement classification, Evidence-aware U-turn handling policy (+1 more)

### Community 94 - "Tag Versus Geometry Authority"
Cohesion: 0.25
Nodes (9): _decision_is_satisfied, _OSM_NATIVE_RULES (Gate 5 refusal set), turn_permission_geometry_conflict override, Absence of a change tag permits the lane change, classify_movement 35 degree through bin, _side_filtered_candidates, _stranded_permission_fallback, Surveyed tags outrank inferred angles; a tag must not strand a lane (+1 more)

### Community 95 - "Reviewed OSM Writing"
Cohesion: 0.22
Nodes (9): Edit the source turn:lanes string rather than recompose it, _overrides_from (driving_side-aware override collection), _turn_lane_slot (inverts _turn_permissions), _turn_lanes_tag (writes turn:lanes into reviewed.osm), _write_reviewed_osm, affected_feature_ids sorted and unique, deterministic_id folds ids positionally, lane_count_inference / lane_width_default / speed_default rules (+1 more)

### Community 96 - "Routes and Signals at Convert"
Cohesion: 0.25
Nodes (9): convert --routes (one scenario per route, one shared map), ego_route.py, ScenarioEnv has no start-and-end setting; TrajectoryNavigation needs a recorded track, A light in a dataset is a tape, not a signal, dynamic_map_states (portable baked tape), signal_plan.py, stop_point sits at the top level of a light entry, Signal timing must not live in config/default.yaml (+1 more)

### Community 97 - "One-Lane One-Way Reading"
Cohesion: 0.25
Nodes (9): _apply_single_lane_oneway strand guard, _balanced_merge_assignment, A one-lane road was given a lane each way, and the phantom lane broke the merge, A refusal is a real outcome: driving off the edge of the map counts as a way out, osm_source.single_lane_implies_oneway, _balanced_approach_assignment, Only the allocation is blinded: the forbidden movements are still generated and keep their ids, _restricted_groups (+1 more)

### Community 98 - "Merge Join Lines"
Cohesion: 0.25
Nodes (7): _join_line(), JoinLine, Where a lane is entered, and the direction of travel there., The line of the lane a road merges into: a point on it and its unit direction., Signed distance from the line, positive to the left of the direction of travel., `point` moved perpendicular onto the line, keeping its distance along it., _unit()

### Community 99 - "Reachability Page Boot"
Cohesion: 0.39
Nodes (9): boot(), describe(), pick(), redraw(), refresh(), renderList(), styleFor(), download() (+1 more)

### Community 100 - "Browser Route Graph"
Cohesion: 0.33
Nodes (4): RouteGraph, RouteLane, chain(), lane()

### Community 102 - "3D Runner and Recording"
Cohesion: 0.29
Nodes (8): Why 3D needs its own runner, _keep_line_ends — MetaDrive draws painted lines short, map_region_size sizing, _max_texture_dimension — GPU ceiling probe, ActionRecorder — (obs, action) pairs, make_env — the env builder in tools/agent_env.py, Stage 7b — a model's numbers reaching the car, ScenarioWaypointEnv — the second socket

### Community 103 - "Stage 6 Convert Contract"
Cohesion: 0.25
Nodes (8): osm-scenario convert (Stage 6), Reachability respecting one-way direction, not routing_components, _read wrapper re-raising as ConversionError, Stage 5 gate on convert (signed reviewed.json sha256), EdgeRoadNetwork.bfs_paths and bare-id neighbours, A lane change is never an exit in the map features, _lane_change_moves, _reachability (with and without lane changes)

### Community 104 - "MetaDrive Sanity Check"
Cohesion: 0.25
Nodes (8): _UNVERIFIED_FIELDS table, _load_metadrive_schema (loads scenario_description.py by path), metadata.metadrive_processed / METADATA_KEYS, ScenarioDescription.sanity_check, scenario_file_name() and is_scenario_file naming rule, metadata.ts must be an ndarray with .shape, Written against the format's documentation rather than its validator, The one test using MetaDrive's real code still ran in our interpreter

### Community 105 - "Stage Script Helpers"
Cohesion: 0.32
Nodes (5): banner(), die(), resolve_workspace(), run_stage(), _common.sh script

### Community 106 - "Converter Config Models"
Cohesion: 0.39
Nodes (7): CoordinateOrigin, LaneGeometryConfig, LaneSelectionConfig, LaneWidthDefaults, BaseModel, Versioned converter configuration models., TagInferenceConfig

### Community 107 - "Block Alignment Tests"
Cohesion: 0.25
Nodes (8): One centred block of `lane_count` lanes running due east from `start`., Three lanes into two, the offside lane peeling off: the two that continue must…, Each guard, on the shape that made it necessary., A continuation link carries no turn angle, so `_aligned_blocks` measures one.…, _straight_run(), test_a_road_that_carries_on_is_placed_where_the_road_behind_it_is(), test_alignment_stands_aside_where_the_step_is_not_a_defect(), test_alignment_will_not_follow_a_road_round_a_corner()

### Community 108 - "Carriageway Placement Rules"
Cohesion: 0.38
Nodes (7): _aligned_blocks — a road that carries on takes its position from the road behind (v25), placement tag read for lane-block position (v24), _separated_roads — opposing carriageways get a median (v26), _two_way_roads — a street may never be parted down its own middle, _uncrossed_lanes — a merging road must not cross the lane it joins (v23), lane_geometry merge taper settings, Junction geometry and the merge taper

### Community 109 - "Export-Time Paint Passes"
Cohesion: 0.38
Nodes (7): _junction_kerb_boundaries — kerb painting at junction edges, _KERB_GAP_CLOSE_M — close the seams before tracing, _road_on_both_sides — a kerb separates road from not-road, _sealed_surfaces — a hole in the tarmac draws itself as a white line, _stub_lanes — clamped junction-interior lanes lose their paint, _uncovered_boundaries — no line may lie on tarmac, Stage 6 map_features and boundary styling

### Community 110 - "Traffic Light Tapes"
Cohesion: 0.29
Nodes (7): --lights live versus --lights tape, A light in a dataset is a tape, not a controller, Why the traffic is live rather than written into the dataset, inferred_stop_line, signal_lane_association, Signals and inferred stop lines, A lane that stops dead is usually the extract edge

### Community 111 - "Lane Count and Width Inference"
Cohesion: 0.29
Nodes (7): lane_width_defaults.vehicle = 3.5, tag_inference.infer_missing_lane_count, _directional_lane_count precedence table, The remainder rule for directional lane counts, Width-driven lane geometry construction, OSM width tag parsing, Lane-width selection algorithm

### Community 112 - "Turn Restriction Enforcement"
Cohesion: 0.29
Nodes (7): A via-way turn restriction always deleted its last movement, and sealed a road off, restriction_enforced_leg warning, A restriction names a route; a connector is one step of one, topology.via_way_resolution, topology.node_restriction_forbids, _side_filtered_candidates no-stranding catch, movement_served_by_link_bypass warning

### Community 113 - "Off-Ramp Bypass Evidence"
Cohesion: 0.29
Nodes (7): Lanes were dealt across a destination a turn restriction forbids, Both ends must match and the movement must carry a side; a ramp replaces a turn, never a road going ahead, A turn an off-ramp already carries was offered at the junction as well, _link_bypass_ends chain walk, _link_bypass_way, _link_bypassed_groups in blocked_groups, An off-ramp before the junction is evidence the junction does not carry that turn

### Community 114 - "Movement Roles in Findings"
Cohesion: 0.43
Nodes (7): _movement_roles(), Which lanes a finding names are approached from, and which are arrived at. A…, _roles_finding(), test_a_finding_that_names_lanes_says_which_one_is_approached_from(), test_a_lane_at_both_ends_of_a_chain_is_left_uncoloured(), test_a_way_scoped_finding_is_never_oriented(), test_nothing_is_oriented_without_a_link_between_two_named_lanes()

### Community 115 - "Dataset Checker"
Cohesion: 0.38
Nodes (6): main(), _paint_on_tarmac(), _polyline_length(), Open a converted dataset the way MetaDrive opens it, in MetaDrive's own…, Metres along a polyline. Written out rather than imported: numpy is the only…, The longest run of a painted line lying inside a lane surface it does not…

### Community 116 - "Connector Candidate Splitting"
Cohesion: 0.33
Nodes (6): Connector generation split into candidates, restriction resolution, geometry, Removal at one proven exact junction, OSM relation 15336555 (unresolved missing members), Candidate-level via-way restriction proof engine, Explicitly approximate placement fallback for incomplete via-way relations, Relation 10421009 via-way no_u_turn over-removal

### Community 117 - "Road Alignment Guards"
Cohesion: 0.33
Nodes (6): movement_side and side_movement_min_degrees, _aligned_blocks, _join_turn_degrees and ALIGNMENT_MAX_TURN_DEG, A road is a chain of ways, so where its tarmac lies is a property of the road and not of one way, Three guards: one feeder only, both sides centred one-way, and the join must be straight, Within a component a placement-tagged block never moves and otherwise the widest block stays

### Community 118 - "Opposing Carriageway Separation"
Cohesion: 0.33
Nodes (6): The unit that moves must be coarser than an alignment component, or a way kinks by the difference, opposing_carriageways_overlap warning / MAX_ROAD_SHIFT_M, _road_components, _separated_roads, A road is a whole street, both directions of it, _two_way_roads pinned to a zero budget

### Community 119 - "Junction Crossing Tests"
Cohesion: 0.33
Nodes (6): `junction-1`'s reviewed model, when the workspace is there. `workspaces/` is…, A step wider than `MAX_JOIN_M` must be one `junction_crossings` knows about.…, A road running straight through a junction has no connector, and still crosses…, _real_model(), test_every_step_too_wide_for_a_plain_join_is_named_as_a_junction(), test_the_crossings_a_route_is_judged_on_include_more_than_the_connectors()

### Community 120 - "Asset Bundling"
Cohesion: 0.33
Nodes (4): assets, bundles, here, outfile

### Community 121 - "Localized Review Pointers"
Cohesion: 0.50
Nodes (5): Click-through review lines and deferred popup opening, Localized circle pointers at review geometry, SVG pointer renderer replacing Leaflet Canvas pane, FindingLocation, GeoPoint and FindingSource models, Representative point is the middle node of the longest source polyline

### Community 122 - "Portable Pickling"
Cohesion: 0.40
Nodes (4): _portable_pickle(), _PortablePickler, Pickle arrays so an older numpy can read them. The same argument as the…, `pickle.dumps`, minus the numpy version dependence. See `_PortablePickler`.

### Community 123 - "Ramp Chain Tests"
Cohesion: 0.40
Nodes (5): _link_chain_snapshot(), Nothing distinguishes one ramp mapped as two ways from two ramps in series.…, Past a fork the chain no longer names one place the traffic comes out, so the…, test_a_ramp_chain_that_forks_is_cut_rather_than_guessed_at(), test_a_ramp_records_every_place_it_meets_the_network_not_only_the_last()

### Community 124 - "Stage 5 Condition Re-derivation"
Cohesion: 0.40
Nodes (5): _finding(), Stage 5 re-derives conditions from the model, so it re-detects what a reviewer…, Accepting says the proposal was right, not that the condition does not matter., test_a_condition_judged_not_applicable_is_dispositioned(), test_a_condition_merely_accepted_is_not_dispositioned()

### Community 125 - "Point Cloud Viewer"
Cohesion: 0.50
Nodes (4): _colours(), main(), Open a sensor-survey point cloud in an interactive 3-D viewer. Reads the…, Per-point RGB in [0, 1], as a blue -> cyan -> yellow -> red ramp.

### Community 126 - "Details Tests"
Cohesion: 0.60
Nodes (4): laneSide(), connector(), feature(), lane()

### Community 127 - "Turn Tag Side Authority"
Cohesion: 0.50
Nodes (4): An explicit turn:lanes tag is adhered to and the conflict is raised as review, never silenced, tagged_movement_side, _tagged_side_block, Order of authority for a side: turn:lanes, then _merge_side, then movement_side

### Community 128 - "Scenario File Naming"
Cohesion: 0.50
Nodes (4): The one filename the dataset is keyed on, in the form MetaDrive insists on.…, scenario_file_name(), `ScenarioDescription.is_scenario_file` accepts `sd_*` or all-digits, nothing…, test_the_scenario_file_is_named_the_way_metadrive_demands()

### Community 129 - "Restriction Effect Policy"
Cohesion: 0.67
Nodes (3): A turn restriction names a route; a connector is one step, restriction_effect_review, Stage 2 restriction policy

### Community 130 - "Lane Direction Arrows"
Cohesion: 0.67
Nodes (3): _direction_arrow(), Chevron at the midpoint of a lane centreline, pointing along direction of…, test_direction_arrow_points_downstream_from_the_centreline_midpoint()

### Community 131 - "Forbidden Movement Links"
Cohesion: 0.67
Nodes (3): _links_by_node(), Every lane-to-lane link the model keeps, indexed by the node it happens at.…, test_a_forbidden_movement_is_not_a_link_between_two_lanes()

## Ambiguous Edges - Review These
- `_merge_taper_plan` → `Generation fingerprint unstable across a re-fetch (osmnx created_date)`  [AMBIGUOUS]
  docs/mapping-algo-changes/2026-08-07-15:38:02-review-status-parked-a-link-on-the-centreline.md · relation: conceptually_related_to

## Knowledge Gaps
- **202 isolated node(s):** `wingfin-osm-scenarionet-converter`, `_common.sh script`, `drive.sh script`, `run-stages-1-3.sh script`, `run-stages-4-6.sh script` (+197 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `_merge_taper_plan` and `Generation fingerprint unstable across a re-fetch (osmnx created_date)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `PreliminaryLaneModel` connect `Lane Model Fixtures and Signals` to `Stage 3 Review Application`, `Forbidden Movement Links`, `Signal Plan and Light Tape`, `Ego Drive-Line Geometry`, `Stage 3 Comparison View`, `Stage 2 Lane Model Generation`, `Ego Route Tests`, `Lane Allocation and Merge Side`, `Stage 3 Review Payload`, `Lane Boundary Styling`, `Stage 5 Validation Tests`, `Stage 6 Conversion Tests`, `Junction Crossings Payload`, `Stage 6 Map Feature Export`, `Kerb Coverage Tests`, `MetaDrive Schema Gate`, `Lane Neighbour Tests`, `Stage 6 Convert Pipeline`, `Stage 5 Validation Checks`, `Route Planning Refusals`, `Polyline Geometry Helpers`, `Merge Geometry Tests`, `Stage 5 Validation View`, `Finding Locations`, `Junction Bridge Features`, `Lane Painting Tests`, `Route Search Graph`, `Off-Ramp Bypass Tests`, `Lane Payload Tests`, `Junction Crossing Tests`?**
  _High betweenness centrality (0.082) - this node is a cross-community bridge._
- **Why does `ConverterConfig` connect `Lane Model Fixtures and Signals` to `Stage 3 Review Application`, `Stage 6 Convert Pipeline`, `Stage 5 Validation Checks`, `CLI Command Surface`, `Stage 2 Lane Model Generation`, `Converter Config Models`, `Stage 1B Data Audit`, `Stage 3 Review Payload`, `Inspection Tests`, `Stage 1B Normalization`, `Stage 6 Map Feature Export`, `MetaDrive Schema Gate`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Why does `RemotePolicy` connect `Remote Policy Errors` to `Drive Runner and Logging`, `Policy Client and Example`?**
  _High betweenness centrality (0.015) - this node is a cross-community bridge._
- **Are the 97 inferred relationships involving `PreliminaryLaneModel` (e.g. with `apply_review()` and `_check_submission()`) actually correct?**
  _`PreliminaryLaneModel` has 97 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `build_lane_model()` (e.g. with `ConverterConfig` and `ReviewFinding`) actually correct?**
  _`build_lane_model()` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 55 inferred relationships involving `LaneFeature` (e.g. with `_already_meet()` and `_bend_between()`) actually correct?**
  _`LaneFeature` has 55 INFERRED edges - model-reasoned connections that need verification._