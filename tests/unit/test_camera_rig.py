"""`tools/camera_rig.py` - the spec parser and the CARLA -> MetaDrive frame conversion.

**This is the first test to import from `tools/`, and it can only cover half the module.**
`tools/` runs on MetaDrive's Python 3.8 and pytest runs on this repo's 3.10, so anything
touching MetaDrive is unreachable from here. What is reachable is the half that decides where
a camera points: the parser, and the swap and sign flip that turn a CARLA transform into a
MetaDrive mount. `camera_rig` imports MetaDrive and numpy lazily, inside `sensors()` and
`read()`, precisely so this import works.

The other half - that MetaDrive's vehicle frame really is +y forward / +x right and that a
positive heading really turns left - was measured against a live engine and cannot be asserted
here. `python tools/camera_rig.py --check-frame <dataset>` re-measures it on 3.8; this file
pins what we do with the answer.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from camera_rig import RigError, _parse  # noqa: E402

ONE = """
sensors:
  - type: rgb_camera
    name: cam_front
    transform:
      x: 1
      y: 0.0
      z: 2
      pitch: 0.0
      yaw: 0.0
      roll: 0.0
    width: 512
    height: 288
    fov: 70
    tick_rate: 0.1
"""


def _with(field, value):
    """`ONE` with one `key: value` line replaced, keeping any leading `- ` list marker."""
    lines = []
    for line in ONE.splitlines():
        body = line.strip()
        marker = "- " if body.startswith("- ") else ""
        if body[len(marker):].startswith(field + ":"):
            indent = len(line) - len(line.lstrip())
            line = " " * indent + marker + f"{field}: {value}"
        lines.append(line)
    return "\n".join(lines)


def test_a_carla_transform_becomes_a_metadrive_mount():
    camera = _parse(ONE).cameras[0]
    # CARLA x is forward and MetaDrive's forward is y, so 1 m ahead lands in position[1].
    assert camera.position == (0.0, 1.0, 2.0)
    assert camera.hpr == (0.0, 0.0, 0.0)
    assert camera.width, camera.height == (512, 288)
    assert camera.fov == 70.0


@pytest.mark.parametrize(
    "carla_yaw, heading, aim",
    [
        (0.0, 0.0, "straight ahead"),
        (55.0, -55.0, "right"),  # CARLA + is right; MetaDrive + is left, so the sign flips
        (-55.0, 55.0, "left"),
        (125.0, -125.0, "rear-right"),
        (-125.0, 125.0, "rear-left"),
        (180.0, -180.0, "straight behind"),
    ],
)
def test_yaw_flips_sign_and_the_aim_is_reported_in_words(carla_yaw, heading, aim):
    camera = _parse(_with("yaw", carla_yaw)).cameras[0]
    assert camera.hpr[0] == pytest.approx(heading)
    assert aim in camera.aim


def test_the_spec_that_prompted_this_disagrees_with_itself_about_yaw():
    """cams.txt names `cam_back_left` a camera its own numbers aim to the rear-RIGHT.

    Under CARLA's reading the front pair is right and the back pair is backwards; under the
    opposite reading it is the other way round. No single convention names all four correctly,
    so the module resolves CARLA's and *says* where each camera points rather than trusting
    the name. If this assertion ever fails, `cams.txt` was corrected and the note in
    `docs/scenario-datapoints.md` should go with it.
    """
    rig = _parse(_with("name", "cam_back_left").replace("yaw: 0.0", "yaw: 125.0"))
    assert "rear-right" in rig.cameras[0].aim


def test_a_lateral_mount_swaps_into_x():
    camera = _parse(_with("y", 0.75)).cameras[0]
    assert camera.position[0] == pytest.approx(0.75)  # CARLA's right is MetaDrive's x
    assert camera.position[1] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("roll", 3.0, "roll"),  # never measured against MetaDrive - refused, not guessed
        ("tick_rate", 0.05, "tick_rate"),  # would silently be sampled at 10 Hz
        ("type", "lidar", "type"),
        ("fov", "wide", "not a number"),
    ],
)
def test_what_it_refuses_rather_than_guesses(field, value, reason):
    with pytest.raises(RigError) as raised:
        _parse(_with(field, value))
    assert reason in str(raised.value)


def test_pitch_is_carried_through_untouched():
    """`rigs/av3.txt` pitches four of its six cameras toward the road, and the sign was
    MEASURED before this was allowed: `camera_rig --check-frame` probes a pitched NodePath
    and panda3d's P is nose-up positive, which is CARLA's own convention. So there is nothing
    to convert. Roll stays refused - wing-sim measured that IT does not flip where y and yaw
    both do, and nothing here has checked that against MetaDrive."""
    camera = _parse(_with("pitch", -10.0)).cameras[0]
    assert camera.hpr[1] == pytest.approx(-10.0)
    assert camera.hpr[2] == pytest.approx(0.0)


def test_a_tick_rate_matching_the_interval_it_is_read_at_is_accepted():
    """A 20 Hz spec is exactly right on a 100 Hz world with 20 Hz decisions, and refusing it
    against a hard-coded 0.1 s was the whole of what step 2.3 had to undo."""
    rig = _parse(_with("tick_rate", 0.05), read_interval_s=0.05)
    assert rig.tick_rate_s == pytest.approx(0.05)


def test_a_tick_rate_is_carried_on_when_the_interval_is_not_known_yet():
    """`step_timing` sweeps every rate a workspace holds, so it has no one interval to judge
    against at load time. `None` defers the check and the declared rate rides along for it."""
    rig = _parse(_with("tick_rate", 0.05), read_interval_s=None)
    assert rig.tick_rate_s == pytest.approx(0.05)


def test_a_spec_naming_two_tick_rates_is_refused():
    """The rate has one source - the flags - so a spec cannot ask for two."""
    second = _with("name", "cam_two").replace("tick_rate: 0.1", "tick_rate: 0.05")
    with pytest.raises(RigError, match="declares"):
        _parse(ONE + second.replace("sensors:", ""), read_interval_s=None)


def test_a_missing_transform_key_is_named():
    text = "\n".join(line for line in ONE.splitlines() if not line.strip().startswith("z:"))
    with pytest.raises(RigError, match="transform has no z"):
        _parse(text)


def test_duplicate_names_are_refused():
    with pytest.raises(RigError, match="duplicate"):
        _parse(ONE + ONE.replace("sensors:", ""))


def test_something_that_is_not_a_spec_is_refused():
    with pytest.raises(RigError, match="expected `sensors:`"):
        _parse("cameras:\n  - one\n")
    with pytest.raises(RigError, match="does not look like a camera spec"):
        _parse("# a comment and nothing else\n")


def test_the_real_spec_parses():
    """`rigs/cams.txt` is in the repo, so this is an unconditional gate rather than a skip.

    It used to look outside the repo and at `/rig`, and skipped on any machine that had
    neither - a gate that quietly stops running is worse than one that fails, the reason
    `test_conversion._metadrive_src` gives for its own fallback. `/rig` is still looked in
    after, for a spec deliberately kept outside the repo; `pytest.skip` is now reachable only
    if someone deletes the tracked file.
    """
    path = next(
        (
            one
            for one in (
                REPO / "rigs" / "cams.txt",
                Path("/rig/cams.txt"),
            )
            if one.exists()
        ),
        None,
    )
    if path is None:
        pytest.skip("rigs/cams.txt has been removed from the repo")
    rig = _parse(path.read_text())
    assert len(rig) == 7
    assert rig.names[0] == "cam_front"
    # 6 x 512x288x3 + 1 x 1280x720x3, which is what makes --rig-record expensive.
    assert rig.megabytes == pytest.approx(5.42, abs=0.01)
