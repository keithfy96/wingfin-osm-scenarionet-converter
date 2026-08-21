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

import csv
import io
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from step_timing import (  # noqa: E402
    DEFAULT_ROWS,
    FIELDS,
    ROWS,
    RowWriter,
    camera_label,
    carried,
    percentiles,
    rate_keys,
    row_listing,
)

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


# -- the reference cannot drift from what runs -----------------------------------------

DOC = REPO / "docs" / "step-timing-rows.md"


def test_the_listing_describes_every_row_as_the_dict_holds_it():
    """Rendered from `ROWS`, so a row added later cannot be missing - and its render mode,
    policy and sensor list cannot be described as something they are not."""
    listing = row_listing()
    for number, row in ROWS.items():
        line = next(
            (one for one in listing.splitlines() if one.strip().startswith(f"{number} ")), None
        )
        assert line is not None, f"row {number} is missing from --list-rows"
        assert (row["render"] or "none") in line
        assert row["policy"] in line
        assert (",".join(carried(row)) or "-") in line
        assert row["isolates"] in line


def test_the_listing_marks_the_default_rows():
    listing = row_listing()
    for line in listing.splitlines():
        for number in ROWS:
            if line.strip().startswith(f"{number} "):
                assert ("[default]" in line) == (number in DEFAULT_ROWS)


def test_the_default_rows_all_run_unattended():
    """`./step-timing.sh <workspace>` with no flags must not try to open a window. Row 7 is
    the display row; a default that included it would block on a machine with no screen and
    would put a window in front of whoever started an overnight sweep."""
    for number in DEFAULT_ROWS:
        assert ROWS[number]["render"] != "3D", f"row {number} needs a display"


def test_a_rig_is_counted_in_the_sensors_column():
    """The count is on the word because a seven-camera rig and the single 320x180 camera this
    tool invents are not the same measurement, and printing `camera` for both is exactly the
    mislabelling `carried` exists to prevent."""
    assert camera_label(0) == ()
    assert camera_label(1) == ("camera",)
    assert camera_label(7) == ("camera x7",)
    for number, row in ROWS.items():
        if row["render"] == "offscreen":
            assert "camera x7" in carried(row, 7), f"row {number} loses the rig's count"
        else:
            assert not any(one.startswith("camera") for one in carried(row, 7)), (
                f"row {number} builds no camera and must not claim a rig"
            )


def test_no_row_reads_a_camera_in_the_loop():
    """Every offscreen row draws and reads a camera - MetaDrive does it inside `env.step`
    while building the observation - so `sensors` must name one. It must never be in `read`:
    `SensorPack` reads with a parent node, which forces a second render of the same frame and
    would charge the benchmark for work no training loop does."""
    for number, row in ROWS.items():
        assert "camera" not in row["read"], f"row {number} would render twice a step"
        if row["render"] == "offscreen":
            assert "camera" in carried(row), f"row {number} draws a camera and does not say so"


def test_the_doc_names_the_sensors_each_row_really_carries():
    """A doc that says `imu+gps` where the drive renders a camera describes a run that is not
    happening, which is worse than saying nothing - the camera is most of what a step costs."""
    doc = DOC.read_text(encoding="utf-8").splitlines()
    for number, row in ROWS.items():
        heading = next((one for one in doc if one.startswith(f"### Row {number} ")), None)
        assert heading is not None, f"the doc has no section for row {number}"
        listed = next((one for one in doc if one.startswith(f"| **{number}** |")
                       or one.startswith(f"| {number} |")), None)
        assert listed is not None, f"row {number} is missing from the doc's row table"
        for sensor in carried(row):
            assert sensor in heading, f"row {number}'s heading does not name {sensor}"
            assert sensor in listed, f"row {number}'s table line does not name {sensor}"


def test_the_doc_covers_every_row():
    """The doc is the fuller version of the same table; a row it does not mention is a row
    nobody can look up."""
    doc = DOC.read_text(encoding="utf-8")
    for number, row in ROWS.items():
        assert f"### Row {number}" in doc, f"the doc has no section for row {number}"
        assert row["policy"] in doc


def test_the_doc_covers_every_csv_field():
    """A CSV reference that falls behind the writer is worse than none - a reader trusts it."""
    doc = DOC.read_text(encoding="utf-8")
    missing = [field for field in FIELDS if f"`{field}`" not in doc]
    assert not missing, f"docs/step-timing-rows.md does not describe: {missing}"


# -- the CSV, written as the run goes --------------------------------------------------


def _record(number):
    """A record with every field the writer expects, distinguishable by `row`."""
    return {field: "" for field in FIELDS} | {"row": number}


def test_a_row_is_on_disk_before_the_run_finishes(tmp_path):
    """The property the writer exists for. It used to collect rows in a list and write once at
    the end, so a sweep interrupted at row 11 of 12 left nothing at all - minutes of GPU time
    discarded for want of reaching the last line. Read back with the handle still open."""
    def rows_on_disk():
        return [row["row"] for row in csv.DictReader(io.StringIO(writer.path.read_text()))]

    writer = RowWriter(tmp_path / "reports" / "step-timing-x-2026-01-01-00:00:00.csv")
    writer.write(_record(1))
    assert rows_on_disk() == ["1"]

    writer.write(_record(2))
    assert rows_on_disk() == ["1", "2"]
    writer.close()


def test_the_header_is_written_once_and_holds_every_field(tmp_path):
    """Two rows, one header - a reader concatenates these files across machines."""
    writer = RowWriter(tmp_path / "out.csv")
    writer.write(_record(1))
    writer.write(_record(2))
    writer.close()

    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0].split(",") == list(FIELDS)


def test_the_path_is_still_reported_after_the_file_is_closed(tmp_path):
    """`main` prints the `csv <path>` line *after* closing, and that line is what the docs tell
    a reader to look at. Reading the open handle to answer "did anything get written" made it
    disappear from every run - the closing nulls the handle."""
    writer = RowWriter(tmp_path / "out.csv")
    writer.write(_record(1))
    writer.close()
    assert writer.wrote_anything


def test_a_run_that_measures_nothing_leaves_no_file(tmp_path):
    """Opened on the first row rather than up front, so an empty sweep does not litter the
    reports directory with a header nobody asked for."""
    writer = RowWriter(tmp_path / "out.csv")
    writer.close()
    assert not writer.path.exists()
    assert not writer.wrote_anything


def test_no_csv_writes_nothing_however_many_rows_arrive(tmp_path):
    """`--no-csv` is "print the table and write nothing", and it reaches the writer as a flag
    rather than as a branch at every call site."""
    writer = RowWriter(tmp_path / "out.csv", enabled=False)
    writer.write(_record(1))
    writer.write(_record(2))
    writer.close()
    assert not writer.path.exists()
