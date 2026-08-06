`lane_count_inference` is the warning/blocker that says:

> “Stage 2 had to decide how many lanes travel in this direction instead of reading an unambiguous directional lane count from OSM.”

The algorithm runs once for each directed graph edge. Its precedence is:

| Evidence | Generated count | Confidence | Finding |
|---|---:|---|---|
| `lanes:forward` or `lanes:backward` | That value | High | None |
| One-way/roundabout plus total `lanes` | Total value | High | None |
| Total `lanes` plus the opposite direction's count | `total - opposite` | High | None |
| Total `lanes` at or below the opposite direction's count | 1 | Low | Blocker |
| Two-way plus even total `lanes` | `total // 2` | Medium | Warning |
| Two-way plus odd total `lanes` | `max(1, total // 2)` | Low | Blocker |
| No usable lane count | 1 | Low | Blocker |

The implementation is [`_directional_lane_count`](</home/keith/Desktop/work/wingfin/converter-scenarionet-stage2-redesign/src/osm_scenario/generation.py:127>).

For example:

```text
lanes:forward=2
→ forward count = 2
→ high confidence
→ no lane_count_inference finding
```

```text
lanes=4, two-way road
→ forward count = 4 // 2 = 2
→ backward count = 4 // 2 = 2
→ medium confidence warnings
```

```text
lanes=4, lanes:backward=1, two-way road
→ backward count = 1 (explicit)
→ forward count = 4 - 1 = 3 (the remainder)
→ high confidence, no findings
```

The remainder rule matters because OSM mappers routinely tag only the minority
direction and leave the majority direction implicit. Halving the total in that
case silently drops lanes: `lanes=4` with `lanes:backward=1` would generate
three lanes on a four-lane road. Once one direction is stated, the other is
subtraction rather than inference, so it carries no finding.

```text
lanes=3, two-way road
→ forward count = max(1, 3 // 2) = 1
→ backward count = 1
→ low confidence blockers
```

The problem with a bare `lanes=3`, with neither direction stated, is that arithmetic cannot determine where the third lane belongs. It could be:

- two lanes in one direction and one in the other;
- a reversible/shared lane;
- a turn pocket;
- a lane that exists only along part of the way; or
- incorrect or incomplete OSM tagging.

When no count exists at all, Stage 2 generates one lane because it needs geometry to continue. That is the most conservative structural fallback, but it has no source evidence, so it becomes a blocker.

In the current mosque snapshot:

- 550 findings defaulted to one lane because no count was usable;
- 70 even totals inferred one lane per direction;
- total: 620 findings across 111 source ways;
- 550 blockers and 70 warnings;
- six ways resolved by the remainder rule emit no finding at all.

A finding is emitted after Stage 2 has already created the lanes. It does not stop generation. For example, one finding might contain:

```json
{
  "source_ids": ["107911733"],
  "affected_feature_ids": ["ea31af3e40b087f2"],
  "proposed_value": {
    "direction": "backward",
    "lane_count": 1
  },
  "reason": "default_single_lane",
  "severity": "blocker",
  "confidence": "low"
}
```

Here:

- `source_ids` identifies the OSM way;
- `affected_feature_ids` identifies the generated lane or lanes;
- `proposed_value` records what Stage 2 generated;
- `reason` explains which fallback was used.

The finding is not saying “this road definitely has the wrong number of lanes.” It is saying “the generated count is not proven by the available OSM evidence.”

The reviewer should inspect the source way and visual map for:

- `lanes:forward` and `lanes:backward`;
- total `lanes` versus directional lanes;
- one-way or roundabout tagging;
- reversible/shared lanes;
- turn pockets;
- lane markings and arrows;
- whether the way is split or merged in the graph.

Stage 3 then accepts, rejects, or replaces the proposed directional count in `review.json`; Stage 4 regenerates the reviewed model. The visual Stage 2 audit itself is read-only. See the detailed policy in [stage-2-generation-v1.md](/home/keith/Desktop/work/wingfin/converter-scenarionet-stage2-redesign/docs/policies/stage-2-generation-v1.md:55).
