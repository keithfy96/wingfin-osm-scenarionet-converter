# Wingfin OSM Stage 1 Tools

This repository currently implements Stage 1 only: acquire an OpenStreetMap
snapshot, normalize its public-driving road graph, audit source-data readiness,
and generate inspection views. No downstream map or scenario conversion is
implemented on this branch.

## Setup

```bash
uv sync --dev
uv run osm-scenario --help
```

The CLI exposes two commands: `fetch` and `inspect`.

## Build a Stage 1 workspace

Choose exactly one source and explicitly set the driving side:

```bash
uv run osm-scenario fetch \
  --osm-file tests/fixtures/osm/tiny.osm \
  --workspace workspaces/example \
  --driving-side left
```

`fetch` performs both parts of the baseline:

- Stage 1A copies or downloads the OSM snapshot, applies the
  `public-driving-v1` selection policy, and creates an unsimplified directed
  graph.
- Stage 1B creates a local projected graph, validates projection round trips,
  and audits lane counts, connectivity, restrictions, signals, widths, and
  stop-line evidence.

The workspace contains source provenance in `source/`, normalized GraphML and
GeoPackage artifacts in `normalized/`, and JSON/Markdown reports in `reports/`.
The original OSM XML and GraphML are authoritative inputs; GeoPackage files are
inspection artifacts.

## Inspect Stage 1

```bash
uv run osm-scenario inspect --workspace workspaces/example --view source
uv run osm-scenario inspect --workspace workspaces/example --view normalized
uv run osm-scenario inspect --workspace workspaces/example --view audit
uv run osm-scenario inspect --workspace workspaces/example --view stage-1
```

The four supported views are:

| View | Purpose |
| --- | --- |
| `source` | Included and excluded source ways plus signals |
| `normalized` | Directed normalized road graph |
| `audit` | Source-data warnings and review evidence |
| `stage-1` | Combined Stage 1 source and normalized layers |

Generated workspaces are ignored by Git. The files under
`workspaces/mosque/source/` and `workspaces/mosque/images/`, when present, are
local redesign references and are not committed.

## Validate the baseline

```bash
uv run pytest
uv run ruff check .
git diff --check
```

See [guide/project-guide.md](guide/project-guide.md) for artifact ownership and
[docs/policies/public-driving-v1.md](docs/policies/public-driving-v1.md) for the
road-selection policy.
