"""Top-level command-line interface."""

from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from osm_scenario.acquisition import AcquisitionError, acquire_osm
from osm_scenario.apply_review import ApplyReviewError, apply_review
from osm_scenario.config import ConverterConfig, load_config
from osm_scenario.generation import GenerationError, generate_lane_model
from osm_scenario.inspection import InspectionError, generate_inspection
from osm_scenario.logging import configure_logging
from osm_scenario.normalization import NormalizationError, normalize_workspace

app = typer.Typer(
    name="osm-scenario",
    help="Acquire, normalize, audit, and inspect OpenStreetMap road data.",
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
    review = "review"


Workspace = Annotated[
    Path,
    typer.Option("--workspace", "-w", help="Map workspace directory.", file_okay=False),
]


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


@app.command()
def inspect(
    workspace: Workspace,
    view: Annotated[
        InspectView,
        typer.Option(
            "--view",
            help=(
                "View to render: source, normalized, audit, or stage-1 for Stage 1; "
                "review for the Stage 3 decision view over a generated lane model."
            ),
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


@app.command("generate-map")
def generate_map(
    workspace: Workspace,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", exists=True, dir_okay=False, help="Versioned YAML config."),
    ] = None,
) -> None:
    """Generate the Stage 2 preliminary lane model for WORKSPACE."""
    try:
        config = (
            load_config(config_path)
            if config_path is not None
            else ConverterConfig(config_version=1)
        )
        report_path = generate_lane_model(workspace=workspace, config=config)
    except (GenerationError, ValueError, KeyError) as error:
        typer.echo(f"Stage 2 failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Stage 2 complete: {report_path}")


@app.command("apply-review")
def apply_review_command(
    workspace: Workspace,
    submission: Annotated[
        Path,
        typer.Option(
            "--submission",
            exists=True,
            dir_okay=False,
            help="The review.json exported by Stage 3.",
        ),
    ],
    config_path: Annotated[
        Path | None,
        typer.Option("--config", exists=True, dir_okay=False, help="Versioned YAML config."),
    ] = None,
) -> None:
    """Apply a Stage 3 review to WORKSPACE and regenerate the reviewed lane model."""
    try:
        config = (
            load_config(config_path)
            if config_path is not None
            else ConverterConfig(config_version=1)
        )
        report_path = apply_review(workspace=workspace, submission=submission, config=config)
    except (ApplyReviewError, ValueError, KeyError) as error:
        typer.echo(f"Stage 4 failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Stage 4 complete: {report_path}")


if __name__ == "__main__":
    app()
