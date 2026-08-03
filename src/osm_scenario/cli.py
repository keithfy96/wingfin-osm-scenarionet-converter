"""Top-level command-line interface."""

from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from osm_scenario.acquisition import AcquisitionError, acquire_osm
from osm_scenario.config import ConverterConfig, load_config
from osm_scenario.inspection import InspectionError, generate_inspection
from osm_scenario.lanelet_generation import LaneletGenerationError, generate_preliminary_lanelet2
from osm_scenario.logging import configure_logging
from osm_scenario.normalization import NormalizationError, normalize_workspace

app = typer.Typer(
    name="osm-scenario",
    help="Build editable Lanelet2 maps and standalone scenario datasets from OSM.",
    no_args_is_help=True,
)


class DrivingSide(str, Enum):
    left = "left"
    right = "right"


class InspectView(str, Enum):
    source = "source"
    normalized = "normalized"
    audit = "audit"
    stage_1 = "stage-1"
    lanelet2 = "lanelet2"


Workspace = Annotated[
    Path,
    typer.Option("--workspace", "-w", help="Map workspace directory.", file_okay=False),
]


def _stage_pending(stage: int) -> None:
    typer.echo(f"This command is reserved for Stage {stage} and is not implemented yet.", err=True)
    raise typer.Exit(code=2)


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    """Configure process-wide CLI behavior."""
    configure_logging(verbose=verbose)


@app.command()
def fetch(
    osm_file: Annotated[
        Path | None, typer.Option("--osm-file", exists=True, dir_okay=False)
    ] = None,
    place: Annotated[str | None, typer.Option("--place")] = None,
    bbox: Annotated[
        tuple[float, float, float, float] | None,
        typer.Option("--bbox", help="Bounding box as WEST SOUTH EAST NORTH."),
    ] = None,
    workspace: Workspace = Path("workspaces"),
    driving_side: Annotated[DrivingSide | None, typer.Option("--driving-side")] = None,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", exists=True, dir_okay=False, help="Versioned YAML config."),
    ] = None,
) -> None:
    """Acquire, project, and preflight exactly one OSM source."""
    selected = sum(value is not None for value in (osm_file, place, bbox))
    if selected != 1:
        raise typer.BadParameter(
            "provide exactly one of --osm-file, --place, or --bbox",
            param_hint="OSM source",
        )
    if driving_side is None:
        raise typer.BadParameter(
            "provide --driving-side left or --driving-side right",
            param_hint="--driving-side",
        )
    try:
        acquire_osm(
            workspace=workspace,
            driving_side=driving_side.value,
            osm_file=osm_file,
            place=place,
            bbox=bbox,
        )
        config = (
            load_config(config_path)
            if config_path is not None
            else ConverterConfig(config_version=1)
        )
        report_path = normalize_workspace(workspace=workspace, config=config)
    except AcquisitionError as error:
        typer.echo(f"Stage 1A failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    except (NormalizationError, ValueError) as error:
        typer.echo(f"Stage 1B failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Stage 1 complete: {report_path}")


@app.command("generate-lanelet2")
def generate_lanelet2(
    workspace: Workspace,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", exists=True, dir_okay=False, help="Versioned YAML config."),
    ] = None,
) -> None:
    """Generate a preliminary Lanelet2 map in WORKSPACE."""
    try:
        config = (
            load_config(config_path)
            if config_path is not None
            else ConverterConfig(config_version=1)
        )
        output_path = generate_preliminary_lanelet2(workspace=workspace, config=config)
    except (LaneletGenerationError, ValueError, KeyError) as error:
        typer.echo(f"Stage 2 failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Stage 2 preliminary Lanelet2 created: {output_path}")


@app.command()
def inspect(
    workspace: Workspace,
    view: Annotated[
        InspectView,
        typer.Option(
            "--view",
            help="Checkpoint to render: source, normalized, audit, stage-1, or lanelet2.",
        ),
    ] = InspectView.stage_1,
) -> None:
    """Generate a browser-based visual checkpoint for WORKSPACE."""
    try:
        output_path = generate_inspection(workspace=workspace, view=view.value)
    except (InspectionError, ValueError, KeyError) as error:
        typer.echo(f"Inspection failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Inspection created: {output_path}")


@app.command("validate-lanelet2")
def validate_lanelet2(workspace: Workspace) -> None:
    """Validate the reviewed Lanelet2 map in WORKSPACE."""
    del workspace
    _stage_pending(4)


@app.command()
def convert(workspace: Workspace) -> None:
    """Convert validated Lanelet2 into a standalone scenario dataset."""
    del workspace
    _stage_pending(5)


@app.command("validate-scenario")
def validate_scenario(workspace: Workspace) -> None:
    """Validate and read back the standalone dataset in WORKSPACE."""
    del workspace
    _stage_pending(5)


if __name__ == "__main__":
    app()
