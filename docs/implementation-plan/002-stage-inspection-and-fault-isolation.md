# Stage Inspection and Fault Isolation

The converter exposes the earliest available visual representation after every
data transformation. The purpose is not merely to approve the final image; it
is to identify the first stage at which the map becomes incorrect.

## Stage 1A: Source Selection

Run:

```bash
uv run osm-scenario inspect --workspace workspaces/mosque --view source
```

The page compares the preserved OSM snapshot with `public-driving-v1`:

- green: selected public driving roads, including toll roads;
- grey: excluded source highways, with the exclusion reason;
- red: selected graph edges carrying a preflight warning;
- yellow markers: OSM traffic signals;
- optional arrows: the directed graph representation.

If a required road is grey or absent, the problem is source selection or source
parsing. If its arrow points the wrong way, the problem is direction handling.
Click the feature and use its OSM ID and tags to investigate the source evidence.

## Stage 1B: Projection

Run:

```bash
uv run osm-scenario inspect --workspace workspaces/mosque --view normalized
```

The projected metric graph is transformed back to WGS84 only for display and
drawn as a separate blue layer. Toggle it over the green Stage 1A lines. A blue
line that does not coincide with its green source identifies a projection,
origin, axis-order, or serialization defect.

The numerical counterpart is `reports/acquisition.json`. It contains projection
round-trip error and a Stage 1A-to-1B parity result for topology and source tags.

## Combined Stage 1 Review

Run:

```bash
uv run osm-scenario inspect --workspace workspaces/mosque --view stage-1
```

This creates `inspection/stage-1.html` with every Stage 1 layer. The matching
JSON and Markdown summaries are written under `reports/`.

## Later Stages

Stage 2 will extend the inspector with source centerlines, inferred lane
centerlines, left/right boundaries, connectors, stop lines, and traffic-light
associations. Until `lanelet2/preliminary.osm` exists, `--view lanelet2` exits
with a clear error rather than displaying incomplete or invented geometry.

Use the first incorrect representation to assign the defect:

| Representation | Defect owner |
| --- | --- |
| Stage 1A source view | acquisition, filtering, tags, or direction |
| Stage 1B overlay | projection or coordinate serialization |
| Preliminary Lanelet2 | lane inference or geometry generation |
| Scenario dataset | scenario conversion |
| MetaDrive rendering only | consumer loading or rendering |
