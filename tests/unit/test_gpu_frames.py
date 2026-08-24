"""`tools/gpu_frames.py`, and the three places a device array must be copied before use.

`image_on_cuda=True` turns every rendered frame -- and, offscreen, the observation stack
itself -- into a **CuPy** array living in GPU memory. Three things in `tools/` write bytes:
the socket in `policy_client`, the observation on the same socket, and the `.npz` in
`agent_env.ActionRecorder`. None of them can read a device pointer, and `numpy.asarray` on a
CuPy array **raises** rather than copying -- CuPy refuses the implicit PCIe round trip on
purpose -- so each has to make the copy deliberately.

**None of that is reachable from pytest**, for `test_policy_client.py`'s reason: the copy
happens inside a function that needs a live engine and a CUDA context. What is reachable is
the classifier, the copy itself, and -- by walking the AST -- whether each of the three call
sites still routes through it. The AST guards are the point of this file: the failure they
catch is a `TypeError` minutes into a drive, on a machine that has to have a GPU to see it.
"""

import ast
import inspect
import re
import sys
import textwrap
from pathlib import Path

import numpy
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from gpu_frames import is_device_array, to_host  # noqa: E402


class _FakeDeviceArray:
    """What CuPy looks like from outside: an interface, a `.get()`, and no host buffer."""

    def __init__(self, host):
        self._host = host
        self.__cuda_array_interface__ = {
            "shape": host.shape,
            "typestr": host.dtype.str,
            "data": (0x7F0000000000, False),
            "version": 3,
        }

    def get(self):
        return self._host


class _FakeTorchCudaTensor:
    """Carries the same interface but answers to `.cpu().numpy()` instead."""

    def __init__(self, host):
        self._host = host
        self.__cuda_array_interface__ = {"data": (0x7F0000000000, False), "version": 3}

    def cpu(self):
        return self

    def numpy(self):
        return self._host


class _UnreachableDeviceArray:
    __cuda_array_interface__ = {"data": (0x7F0000000000, False), "version": 3}


# ---------------------------------------------------------------------------------------
# Telling a device array from a host one
# ---------------------------------------------------------------------------------------


def test_a_plain_ndarray_is_not_a_device_array():
    assert not is_device_array(numpy.zeros((2, 2), numpy.uint8))


def test_a_cuda_array_interface_without_a_host_buffer_is_a_device_array():
    assert is_device_array(_FakeDeviceArray(numpy.zeros((2, 2), numpy.uint8)))


def test_something_carrying_both_interfaces_is_treated_as_host_memory():
    """A numpy array is never dragged through `.get()` on the strength of a stray attribute.

    Anything exposing `__array_interface__` already *is* readable where it stands, so the
    host interface wins. This is why the test is `hasattr(cai) and not hasattr(ai)` rather
    than `isinstance(value, cupy.ndarray)` -- which would need CuPy imported, and `tools/`
    has to keep running in the default environment, without the `gpu` group.
    """

    class Both(numpy.ndarray):
        __cuda_array_interface__ = {"data": (0x7F0000000000, False), "version": 3}

    both = numpy.zeros((2, 2), numpy.uint8).view(Both)
    assert hasattr(both, "__cuda_array_interface__")
    assert not is_device_array(both)


def test_the_classifier_does_not_import_cupy():
    source = (REPO / "tools" / "gpu_frames.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "cupy" not in imported, (
        "gpu_frames must import in the default environment, which has no `gpu` group"
    )


# ---------------------------------------------------------------------------------------
# Making the copy
# ---------------------------------------------------------------------------------------


def test_a_host_array_is_returned_untouched():
    host = numpy.arange(6, dtype=numpy.uint8)
    assert to_host(host) is host


def test_a_device_array_comes_back_as_its_host_values():
    host = numpy.arange(6, dtype=numpy.uint8).reshape(2, 3)
    copied = to_host(_FakeDeviceArray(host))
    assert isinstance(copied, numpy.ndarray)
    assert numpy.array_equal(copied, host)


def test_a_device_tensor_without_get_is_read_through_cpu():
    host = numpy.arange(4, dtype=numpy.float32)
    assert numpy.array_equal(to_host(_FakeTorchCudaTensor(host)), host)


def test_a_device_array_with_no_way_back_raises_rather_than_being_passed_on():
    """Silently returning it would fail later, at `tobytes()`, with nothing naming the cause."""

    with pytest.raises(TypeError, match="on the GPU"):
        to_host(_UnreachableDeviceArray())


# ---------------------------------------------------------------------------------------
# The three call sites, pinned by AST because none of them can be run here
# ---------------------------------------------------------------------------------------


def _asarray_calls(function):
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "asarray")
            or (isinstance(node.func, ast.Name) and node.func.id == "asarray")
        )
    ]
    assert calls, f"no numpy.asarray(...) in {function.__qualname__} any more"
    return calls


def _copies_before_asarray(function, device_names):
    """True when no `numpy.asarray` in `function` is handed one of `device_names` bare.

    Named subjects rather than "every asarray call", because `ActionRecorder.record` also
    calls `asarray` on `vehicle.current_action`, which is a pair of floats and never on a
    card -- a blanket rule would demand a copy of something that was never on the GPU. And
    named subjects rather than "the name `to_host` appears somewhere in the function",
    which one already-converted call site would satisfy while the frame went untouched.
    """

    for call in _asarray_calls(function):
        if not call.args:
            continue
        first = call.args[0]
        if isinstance(first, ast.Name) and first.id in device_names:
            return False
    return True


def test_the_sensor_frames_are_copied_off_the_card_before_they_are_encoded():
    from policy_client import SensorPack

    tree = ast.parse(textwrap.dedent(inspect.getsource(SensorPack.__call__)))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "to_host" in names, (
        "a `perceive()` result is a CuPy array under image_on_cuda, and numpy.asarray on one "
        "raises rather than copying"
    )


def test_the_observation_is_copied_off_the_card_before_it_is_encoded():
    from policy_client import RemotePolicy

    assert _copies_before_asarray(RemotePolicy.__call__, {"observation", "value"}), (
        "--render offscreen makes the observation a stack of camera frames, and under "
        "image_on_cuda that stack is on the GPU: it is the largest single thing on the wire"
    )


def test_the_recorder_is_copied_off_the_card_before_it_is_written():
    """The subjects are the halves now, not the whole observation.

    `record` splits a `{"image", "state"}` observation before it converts anything, so naming
    only `observation` would pass vacuously -- there is no longer an `asarray(observation)` to
    catch. This is the one of the three call sites that is *reachable* from pytest since the
    recorder learned that dict; `test_agent_env.py` exercises it for real, and this stays as
    the cheap structural check beside its two siblings.
    """
    from agent_env import ActionRecorder

    assert _copies_before_asarray(ActionRecorder.record, {"observation", "state", "image"}), (
        "an .npz is host bytes"
    )


# ---------------------------------------------------------------------------------------
# The dependency group, whose cap is the whole reason the gate opens
# ---------------------------------------------------------------------------------------


def _named_list(table, name):
    """The string entries of a `name = [...]` list in pyproject.toml.

    Read textually rather than with `tomllib`, which is 3.11 and this repo is 3.10, and
    rather than adding a parser as a test-only dependency for one list.
    """

    source = (REPO / "pyproject.toml").read_text()
    section = source.split(f"[{table}]", 1)[1] if table else source
    body = re.search(rf"^{re.escape(name)} = \[(.*?)^\]", section, re.S | re.M)
    assert body, f"no `{name} = [...]` under [{table}]"
    return re.findall(r'"([^"]+)"', body.group(1))


def _gpu_group():
    return _named_list("dependency-groups", "gpu")


def test_the_gpu_group_holds_the_three_packages_the_gate_imports():
    """`base_camera.py:10-18` is one try/except over cupy, PyOpenGL and cuda-python.

    Any one of them missing makes `_cuda_enable` False, and MetaDrive then asserts at env
    construction with a hint that reads "pip install pypiwin32".
    """

    group = " ".join(_gpu_group()).lower()
    assert "cupy-cuda12x" in group
    assert "pyopengl" in group
    assert "cuda-python" in group


def test_cuda_python_is_capped_below_13():
    """Measured: 13.3.1 raises `ImportError: cannot import name 'cudart' from 'cuda'`.

    cuda-python dropped the top-level `cuda.cudart` shim at 13.0 in favour of
    `cuda.bindings.runtime`, and `base_camera.py:14` imports the old name. An uncapped
    resolve picks 13.3.1, which closes the gate; 12.9.7 imports it, with a FutureWarning.
    """

    pin = next(entry for entry in _gpu_group() if entry.lower().startswith("cuda-python"))
    assert "<13" in pin.replace(" ", ""), (
        "cuda-python >= 13 has no `cuda.cudart`, which is what `_cuda_enable` imports"
    )


def test_cupy_is_the_cuda_12_build():
    """Not `cupy-cuda13x`, though the driver offers CUDA 13.2.

    Phase C's model stack is pinned at cu128, and CuPy shares a process and a CUDA context
    with torch.
    """

    pin = next(entry for entry in _gpu_group() if entry.lower().startswith("cupy"))
    assert "cupy-cuda12x" in pin


def test_the_group_is_opt_in_rather_than_a_project_dependency():
    """`uv sync` must keep working on a machine with no CUDA at all."""

    runtime = " ".join(_named_list("project", "dependencies")).lower()
    assert "cupy" not in runtime and "cuda" not in runtime
