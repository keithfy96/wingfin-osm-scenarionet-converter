# wingfin-osm-scenarionet-converter

Standalone OSM-to-Lanelet2 and scenario-dataset conversion tools. The project
does not install or import MetaDrive, ScenarioNet, or sibling repositories.

## Development setup

Python 3.10 and [`uv`](https://docs.astral.sh/uv/) are required.

```bash
uv sync --locked
uv run osm-scenario --help
uv run pytest
uv run ruff check .
```

The CLI currently exposes the complete planned interface. Individual conversion
commands are implemented in their corresponding stages; until then, they return
a concise nonzero "reserved for Stage N" result after validating their inputs.

See [the implementation plan](docs/implementation-plan/README.md) and
[interface explanation](docs/implementation-plan/00-interface-explanation.md).
