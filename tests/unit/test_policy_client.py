"""`tools/policy_client.py` - what goes on the wire to a hosted model.

**Half the module is unreachable from here, for `test_camera_rig.py`'s reason.** `tools/`
runs on MetaDrive's Python 3.8 and pytest runs on this repo's 3.10, and `SensorPack.read`
needs a live engine. What *is* reachable is the encoding - `policy_client` imports numpy and
MetaDrive lazily, inside the functions that need them, so the module imports here - and the
decision about which sensors may cross the wire as 8-bit, which is a table rather than a
measurement.

That table is what these tests exist for. `perceive(to_float=False)` is a free format change
on a camera and a **lossy conversion** on a depth buffer or a point cloud, and MetaDrive
raises on neither, so the split has to be pinned against MetaDrive's own source rather than
trusted.
"""

import base64
import importlib.util
import struct
import sys
from pathlib import Path

import numpy
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "examples"))

from policy_client import (  # noqa: E402
    _SENSOR_KEYS,
    _UINT8_SENSORS,
    SensorPack,
    encode_array,
)
from policy_server import decode_array  # noqa: E402

# ---------------------------------------------------------------------------------------
# What crosses the wire
# ---------------------------------------------------------------------------------------


def test_a_camera_frame_round_trips_as_uint8():
    """The Phase A change: a picture stays 8-bit from the GPU to the model."""
    frame = numpy.arange(320 * 180 * 3, dtype=numpy.uint8).reshape(180, 320, 3)

    decoded = decode_array(encode_array(frame))

    assert decoded.dtype == numpy.uint8
    assert decoded.shape == (180, 320, 3)
    assert numpy.array_equal(decoded, frame)


def test_a_uint8_frame_is_a_quarter_of_the_float32_one_on_the_wire():
    """Why it is worth doing at all, as arithmetic rather than as a claim.

    Base64 inflates both ends by the same 4/3, so the ratio on the wire is the ratio of the
    buffers - and the float32 that used to be sent was itself a downcast from the float64
    `uint8 / 255` produces, so the CPU-side peak was 8x.
    """
    frame = numpy.zeros((288, 512, 3), dtype=numpy.uint8)

    small = len(encode_array(frame)["b64"])
    large = len(encode_array(numpy.asarray(frame, numpy.float32))["b64"])

    assert large == 4 * small
    assert (frame / 255).dtype == numpy.float64


def test_the_float_a_camera_used_to_send_carried_nothing_the_uint8_does_not():
    """All 256 values survive `/255` and back, so nothing was lost by dropping the float."""
    values = numpy.arange(256, dtype=numpy.uint8)

    assert numpy.array_equal((values / 255 * 255).round().astype(numpy.uint8), values)


def test_a_float32_observation_still_round_trips_unchanged():
    """The numeric path is untouched: the observation is not a picture and stays float32."""
    observation = numpy.linspace(-1.0, 1.0, 161, dtype=numpy.float32)

    decoded = decode_array(encode_array(observation))

    assert decoded.dtype == numpy.float32
    assert numpy.array_equal(decoded, observation)


def test_a_point_cloud_in_metres_survives_the_wire():
    """Values a uint8 could not hold - measured -18438 m to +10991 m on a real drive."""
    cloud = numpy.asarray([[-18438.0, 0.5, 10991.0]], dtype=numpy.float32)

    decoded = decode_array(encode_array(cloud))

    assert decoded.dtype == numpy.float32
    assert numpy.array_equal(decoded, cloud)


def test_encode_array_is_self_describing():
    """A server that is not Python must not have to guess the layout."""
    encoded = encode_array(numpy.zeros((2, 3), dtype=numpy.uint8))

    assert set(encoded) == {"dtype", "shape", "b64"}
    assert encoded["dtype"] == "uint8"
    assert encoded["shape"] == [2, 3]


def test_a_non_contiguous_frame_is_encoded_in_its_visible_order():
    """`perceive` returns a doubly-reversed view, so the buffer is not the array."""
    frame = numpy.arange(12, dtype=numpy.uint8).reshape(2, 2, 3)[::-1]

    assert numpy.array_equal(decode_array(encode_array(frame)), frame)


def test_the_numpy_free_fallback_reads_uint8():
    """`policy_server.decode_array` promises stdlib-only, and uint8 is now a dtype it meets."""
    encoded = encode_array(numpy.asarray([[0, 127, 255]], dtype=numpy.uint8))
    raw = base64.b64decode(encoded["b64"])

    assert list(struct.unpack("<3B", raw)) == [0, 127, 255]


# ---------------------------------------------------------------------------------------
# That the read really uses the split
# ---------------------------------------------------------------------------------------


def test_the_sensor_read_takes_its_dtype_from_the_carve_out():
    """`SensorPack.__call__` must decide `to_float` from `_UINT8_SENSORS`, not from a literal.

    **Every other test in this file passes against a `__call__` that ignores the carve-out
    entirely** - they cover `encode_array`, `decode_array` and the contents of the constant,
    and the loop itself needs a live engine this interpreter cannot start. So put the loop
    back the way it was and the suite stays green, which is exactly the shape of failure
    Phase A exists to prevent: a depth buffer quantised to 76 levels, silently.

    Asserted on the source for `test_step_timing.py`'s reason, and the specific thing being
    forbidden is the old code - a bare `to_float=True` for every sensor alike.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(SensorPack.__call__)))
    perceives = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "perceive"
    ]
    assert len(perceives) == 1, f"expected one perceive() in __call__, found {len(perceives)}"

    flags = [kw.value for kw in perceives[0].keywords if kw.arg == "to_float"]
    assert flags, "perceive() is called without to_float; the dtype is then MetaDrive's default"
    assert not isinstance(flags[0], ast.Constant), (
        "to_float is a literal, so every sensor is read the same way - which is the bug: "
        "depth and point-cloud are not 8-bit and must not be cast"
    )

    # And the flag has to be derived from the carve-out rather than from anything else.
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "_UINT8_SENSORS" in names, "SensorPack.__call__ does not consult _UINT8_SENSORS"


def test_the_float32_cast_is_not_applied_to_the_uint8_branch():
    """The other half of the same change: an explicit float32 would undo the read.

    `numpy.asarray(frame, numpy.float32)` on a uint8 frame casts it straight back up, so the
    wire carries float32 again and only the `perceive` call looks changed. Both halves move
    together or neither does.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(SensorPack.__call__)))

    def forces_a_dtype(node):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "asarray"
            and (len(node.args) > 1 or any(kw.arg == "dtype" for kw in node.keywords))
        )

    casts = [node for node in ast.walk(tree) if forces_a_dtype(node)]
    assert len(casts) == 1, f"expected one dtype-forcing asarray in __call__, found {len(casts)}"

    # `any If in the function` is not the test - `__call__` has several, for `route` and the
    # rest, so that version passed against the very code this is here to reject. The cast has
    # to sit *inside* a branch: a conditional expression, or an if/else body.
    branched = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.IfExp, ast.If))
        and any(forces_a_dtype(child) for part in (node.body, node.orelse)
                for statement in (part if isinstance(part, list) else [part])
                for child in ast.walk(statement))
    ]
    assert branched, "the float32 cast is unconditional, so the uint8 read is undone"


# ---------------------------------------------------------------------------------------
# That the split still matches MetaDrive
# ---------------------------------------------------------------------------------------


def _metadrive_src():
    """MetaDrive's source tree, checkout first and installed package second.

    Copied in spirit from `test_conversion._metadrive_src`: found with `importlib.util` and
    then **read as files**, because importing `metadrive` pulls in panda3d and none of that
    is needed to check which class defines which method.
    """
    checkout = Path("/home/keith/Desktop/work/wingfin/metadrive/metadrive")
    if checkout.is_dir():
        return checkout
    found = importlib.util.find_spec("metadrive")
    if found is not None and found.submodule_search_locations:
        return Path(next(iter(found.submodule_search_locations)))
    return checkout


METADRIVE_SRC = _metadrive_src()
SENSORS_DIR = METADRIVE_SRC / "component" / "sensors"
needs_metadrive = pytest.mark.skipif(
    not SENSORS_DIR.is_dir(), reason=f"no MetaDrive source at {SENSORS_DIR}"
)


@needs_metadrive
def test_only_two_format_implementations_exist():
    """The whole split rests on there being exactly two, so notice if a third appears."""
    defining = sorted(
        path.name for path in SENSORS_DIR.glob("*.py") if "def _format" in path.read_text()
    )

    assert defining == ["base_camera.py", "depth_camera.py"]


@needs_metadrive
def test_the_uint8_sensors_are_the_ones_that_are_8_bit_natively():
    """`_UINT8_SENSORS` against MetaDrive, not against a comment.

    A sensor may be sent as uint8 exactly when its `_format` is `BaseCamera`'s, which casts;
    `DepthCamera`'s multiplies by 255 first, which is a conversion. Inheritance decides it,
    so this walks the class of each `--sensors` name to whichever `_format` it reaches.
    """
    text = {path.name: path.read_text() for path in SENSORS_DIR.glob("*.py")}
    classes = {
        "camera": ("rgb_camera.py", "RGBCamera"),
        "semantic": ("semantic_camera.py", "SemanticCamera"),
        "depth": ("depth_camera.py", "DepthCamera"),
        "point-cloud": ("point_cloud_lidar.py", "PointCloudLidar"),
    }
    assert set(classes) == set(_SENSOR_KEYS)

    def base_of(module, name):
        """The single base class named in `class <name>(<base>)`, as (module, name)."""
        header = f"class {name}("
        line = next(row for row in text[module].splitlines() if row.startswith(header))
        base = line.split("(", 1)[1].split(")", 1)[0].strip()
        module = next(m for m, body in text.items() if f"class {base}(" in body)
        return module, base

    casts = set()
    for sensor, (module, name) in classes.items():
        while "def _format" not in text[module]:
            module, name = base_of(module, name)
        # `base_camera.py` casts; `depth_camera.py` scales first, which is not a format change.
        if "* 255" not in text[module].split("def _format", 1)[1]:
            casts.add(sensor)

    assert casts == set(_UINT8_SENSORS)


@needs_metadrive
def test_the_depth_path_would_scale_rather_than_cast():
    """Name the thing being avoided, so a reader does not have to open MetaDrive to see it."""
    body = (SENSORS_DIR / "depth_camera.py").read_text().split("def _format", 1)[1]

    assert "(ret * 255).astype(np.uint8)" in body
