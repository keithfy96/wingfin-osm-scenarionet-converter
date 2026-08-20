"""Top-level command-line interface."""

import json
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from osm_scenario.acquisition import AcquisitionError, acquire_osm
from osm_scenario.apply_review import ApplyReviewError, apply_review
from osm_scenario.config import ConverterConfig, load_config
from osm_scenario.conversion import ConversionError, convert_scenario
from osm_scenario.generation import GenerationError, generate_lane_model
from osm_scenario.inspection import InspectionError, generate_inspection
from osm_scenario.logging import configure_logging
from osm_scenario.normalization import NormalizationError, normalize_workspace
from osm_scenario.validation import ValidationError, validate_map

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


@app.command("validate-map")
def validate_map_command(
    workspace: Workspace,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", exists=True, dir_okay=False, help="Versioned YAML config."),
    ] = None,
) -> None:
    """Validate WORKSPACE's reviewed lane model and write the Stage 5 reports."""
    try:
        config = (
            load_config(config_path)
            if config_path is not None
            else ConverterConfig(config_version=1)
        )
        report_path, _, html_path = validate_map(workspace=workspace, config=config)
    except (ValidationError, ValueError, KeyError) as error:
        typer.echo(f"Stage 5 failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    status = json.loads(report_path.read_text(encoding="utf-8"))["status"]
    typer.echo(f"Stage 5 {status}: {html_path}")
    # A failed validation is a result, not a crash - but the exit code has to say so, or a
    # pipeline reads "wrote a report" as "the map is fit to convert".
    if status != "passed":
        raise typer.Exit(code=1)


@app.command()
def convert(
    workspace: Workspace,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", exists=True, dir_okay=False, help="Versioned YAML config."),
    ] = None,
    routes_path: Annotated[
        Path | None,
        typer.Option(
            "--routes",
            exists=True,
            dir_okay=False,
            help="routes.json from the Stage 6 route builder. Without it the dataset is "
            "map-only, which MetaDrive can load and check but cannot drive.",
        ),
    ] = None,
    signals_path: Annotated[
        Path | None,
        typer.Option(
            "--signals",
            exists=True,
            dir_okay=False,
            help="signals.json from the Stage 6 signal builder. Without it the dataset has "
            "no traffic lights; OSM supplies no signal timing, so the plan is chosen there.",
        ),
    ] = None,
    speed_kph: Annotated[
        float | None,
        typer.Option(
            "--speed-kph",
            min=1.0,
            help="Cruising speed for the recorded car, overriding the road's posted limit. "
            "Without it the car obeys the limit, which is also the ceiling on how quick the "
            "drive can be: it still slows for corners, but it can never average more than the "
            "road allows.",
        ),
    ] = None,
    step_hz: Annotated[
        float | None,
        typer.Option(
            "--step-hz",
            min=1.0,
            help="How many times a second the drive is written down. Default 10, which is "
            "MetaDrive's own step. It changes the density of the recording and nothing else - "
            "the same route at the same speeds. A dataset written at any other rate must be "
            "driven at that rate, so tools/drive.py needs the same --step-hz.",
        ),
    ] = None,
) -> None:
    """Convert WORKSPACE's validated lane model into a ScenarioNet dataset."""
    try:
        config = (
            load_config(config_path)
            if config_path is not None
            else ConverterConfig(config_version=1)
        )
        scenario_paths, _, _, report_path, html_paths = convert_scenario(
            workspace=workspace,
            config=config,
            routes=routes_path,
            signals=signals_path,
            speed_kph=speed_kph,
            step_hz=step_hz,
        )
    except (ConversionError, ValueError, KeyError) as error:
        typer.echo(f"Stage 6 failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # Echoed only when it was asked for, so an unflagged run's stdout does not move.
    if step_hz is not None:
        typer.echo(f"Stage 6 step rate: {step_hz:g} Hz ({1.0 / step_hz:g} s per step)")
    typer.echo(
        f"Stage 6 complete: {report['map_features']} map features, "
        f"{len(scenario_paths)} scenario(s) -> {scenario_paths[0].parent}"
    )
    for route in report["routes"]:
        # The slowest speed is 0 whenever the car stops for a light, so the stops have to be
        # named beside it or the line reads as a route with an impossibly tight corner on it.
        stops = route.get("stops") or []
        held = (
            f", stopping {len(stops)}x for {route['waiting_s']:.0f} s at a red light"
            if stops
            else ""
        )
        typer.echo(
            f"  route {route['name']}: {route['distance_m']:.0f} m in "
            f"{route['duration_s']:.0f} s, {route['slowest_kph']:.0f}-"
            f"{route['speed_kph']:.0f} km/h, {route['junction_movements']} junction "
            f"movement(s), {route['lane_changes']} lane change(s){held}"
        )
    if not report["routes"]:
        typer.echo(
            "  map-only: no ego route, so ScenarioEnv cannot reset. Draw routes in the "
            "route builder and pass --routes to make it drivable."
        )
    plan = report["signals"]
    if plan:
        lanes = sum(len(group["lanes"]) for group in plan["groups"])
        typer.echo(
            f"  signals: {lanes} lane(s) in {len(plan['groups'])} phase group(s) on a "
            f"{plan['cycle_seconds']:.0f} s cycle, marked {plan['source']}"
        )
    reachability, builder, signals = html_paths
    typer.echo(f"Stage 6 reachability map: {reachability}")
    typer.echo(f"Stage 6 route builder:    {builder}")
    typer.echo(f"Stage 6 signal builder:   {signals}")


if __name__ == "__main__":
    app()
