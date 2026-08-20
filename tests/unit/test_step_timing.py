"""`tools/step_timing.py` and `scripts/_common.sh:list_datasets` - the halves that are testable.

Like `test_camera_rig.py`, this reaches into `tools/` from this repo's 3.10 while the module
itself runs on MetaDrive's 3.8, so anything that needs an engine is out of reach: the timing
is a real drive and cannot be asserted here. What is reachable is the arithmetic that decides
*what* is driven and how the result is summarised - the rate pair, and the distribution - plus
the shell function that decides which datasets the sweep is handed.

The measurements themselves are checked by running the tool, which is what its own table is
for. `step_timing.py` imports MetaDrive lazily, inside the functions that need it, precisely
so this import works.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from step_timing import percentiles, rate_keys  # noqa: E402

# -- the rate pair ---------------------------------------------------------------------


def test_an_unpinned_rate_is_metadrives_own_pair():
    """10 Hz must return exactly (0.02, 5), which is what makes --step-hz 10 and no flag the
    same run - the property `drive.step_config` exists to hold."""
    assert rate_keys(10, None) == {"physics_world_step_size": 0.02, "decision_repeat": 5}
    assert rate_keys(100, None) == {"physics_world_step_size": 0.01, "decision_repeat": 1}


def test_pinning_the_physics_puts_the_ticks_in_decision_repeat():
    """100 Hz physics at a 10 Hz decision rate is ten ticks per step - CARLA's own default
    shape, and the thing a single --step-hz cannot express."""
    assert rate_keys(10, 100) == {"physics_world_step_size": 0.01, "decision_repeat": 10}
    assert rate_keys(100, 100) == {"physics_world_step_size": 0.01, "decision_repeat": 1}
    assert rate_keys(10, 200) == {"physics_world_step_size": 0.005, "decision_repeat": 20}


def test_a_decision_finer_than_a_tick_is_refused():
    """Rounding this would silently measure a rate nobody asked for."""
    with pytest.raises(ValueError, match="does not divide"):
        rate_keys(100, 30)
    with pytest.raises(ValueError, match="does not divide"):
        rate_keys(100, 10)


def test_a_rate_that_divides_unevenly_is_refused_rather_than_rounded():
    assert rate_keys(10, 100)["decision_repeat"] == 10
    with pytest.raises(ValueError):
        rate_keys(15, 100)


# -- the distribution ------------------------------------------------------------------


def test_durations_are_summarised_in_milliseconds():
    summary = percentiles([0.001, 0.002, 0.003])
    assert summary["median"] == pytest.approx(2.0)
    assert summary["mean"] == pytest.approx(2.0)
    assert summary["max"] == pytest.approx(3.0)


def test_nothing_measured_is_nan_rather_than_zero():
    """A skipped or warmup-only row has no samples, and 0 ms would read as infinitely fast."""
    summary = percentiles([])
    assert all(value != value for value in summary.values())


# -- which datasets the sweep is handed ------------------------------------------------


def _list_datasets(workspace: Path) -> list[str]:
    """Run `list_datasets` the way a real script does.

    From a file rather than `bash -c`: `_common.sh` resolves `${BASH_SOURCE[1]}` to work out
    which script sourced it, and that is unbound when there is no enclosing script at all.
    """
    runner = workspace.parent / "run-list.sh"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        f'source "{REPO}/scripts/_common.sh"\n'
        'resolve_workspace "$1"\n'
        "list_datasets\n",
        encoding="utf-8",
    )
    finished = subprocess.run(
        ["bash", str(runner), str(workspace)], capture_output=True, text=True
    )
    if finished.returncode != 0:
        raise AssertionError(finished.stderr.strip())
    return [line.strip() for line in finished.stdout.splitlines() if line.strip()]


def _workspace(tmp_path: Path, *datasets: str) -> Path:
    workspace = tmp_path / "ws"
    (workspace / "source").mkdir(parents=True)
    (workspace / "source" / "manifest.json").write_text("{}", encoding="utf-8")
    (workspace / "source" / "map.osm").write_text("", encoding="utf-8")
    for name in datasets:
        directory = workspace / name
        directory.mkdir()
        (directory / "dataset_summary.pkl").write_bytes(b"")
    return workspace


def test_every_rate_is_listed_lowest_first(tmp_path):
    """Ordered by rate rather than by name: `sort` on the string puts 100hz before 10hz."""
    workspace = _workspace(tmp_path, "scenarionet-100hz", "scenarionet-10hz")
    assert [Path(one).name for one in _list_datasets(workspace)] == [
        "scenarionet-10hz",
        "scenarionet-100hz",
    ]


def test_a_directory_without_a_summary_is_not_a_dataset(tmp_path):
    workspace = _workspace(tmp_path, "scenarionet-10hz")
    (workspace / "scenarionet-20hz").mkdir()
    assert [Path(one).name for one in _list_datasets(workspace)] == ["scenarionet-10hz"]


def test_a_pre_rename_dataset_is_used_only_when_nothing_else_exists(tmp_path):
    """The same rule `resolve_dataset` follows: once a workspace has a rate-named dataset, a
    bare `scenarionet` is a build from before the rename rather than an answer about a rate."""
    alone = _workspace(tmp_path / "a", "scenarionet")
    assert [Path(one).name for one in _list_datasets(alone)] == ["scenarionet"]

    beside = _workspace(tmp_path / "b", "scenarionet", "scenarionet-10hz")
    assert [Path(one).name for one in _list_datasets(beside)] == ["scenarionet-10hz"]


def test_a_workspace_with_no_dataset_at_all_is_refused(tmp_path):
    workspace = _workspace(tmp_path)
    with pytest.raises(AssertionError, match="no dataset at all"):
        _list_datasets(workspace)
