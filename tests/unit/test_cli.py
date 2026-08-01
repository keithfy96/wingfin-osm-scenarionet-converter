from typer.testing import CliRunner

from osm_scenario.cli import app

runner = CliRunner()


def test_top_level_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "fetch",
        "generate-lanelet2",
        "inspect",
        "validate-lanelet2",
        "convert",
        "validate-scenario",
    ):
        assert command in result.stdout


def test_fetch_requires_exactly_one_source() -> None:
    result = runner.invoke(app, ["fetch"])

    assert result.exit_code != 0
    assert "provide exactly one" in result.output
    assert "Traceback" not in result.output


def test_workspace_commands_require_workspace() -> None:
    for command in (
        "generate-lanelet2",
        "inspect",
        "validate-lanelet2",
        "convert",
        "validate-scenario",
    ):
        result = runner.invoke(app, [command])
        assert result.exit_code != 0
        assert "workspace" in result.output.lower()
        assert "Traceback" not in result.output
