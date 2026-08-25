"""`model_probe.read_archive`, and the pins that have to match what it reads.

Stage 9 Phase C.1. Almost nothing about the probe is testable here -- it needs a GPU, a
1.2 GB checkpoint and a TensorRT runtime -- but the part that decides whether the run can
possibly succeed is a pair of *files*, and that part is checked.

**The pins are the guard that earns its keep.** `step_440000_trt_direct_full.ep`'s 1.2 GB
constant is a serialized TensorRT engine rather than weights, so a stack that does not match
the one that compiled it does not degrade -- it fails to deserialise, minutes into a run,
with a message about a plan file. The archive states the version it was exported by, and
`pyproject.toml` states the version that gets installed, so the two can be compared before
anything is downloaded.
"""

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from model_probe import (  # noqa: E402
    _ENGINE_FIELDS,
    _SERDE_SCALAR_TYPES,
    ProbeError,
    decode_engine_metadata,
    read_archive,
)

CHECKPOINT = Path(
    "/home/keith/Desktop/work/wingfin/wingfin-openpilot-temp/assets/models/"
    "step_440000_trt_direct_full.ep"
)
MODEL_DEV = Path(
    "/home/keith/Desktop/work/wingfin/wingfin-openpilot-temp/assets/configurations/model_dev.yml"
)

# A skipif that stops running silently is worse than one that fails -- `test_conversion`'s
# `_metadrive_src` lesson, where two gates skipped themselves in a container for months. Both
# reasons below name the path they looked for, so a moved checkout reads as a moved checkout.
needs_checkpoint = pytest.mark.skipif(
    not CHECKPOINT.is_file(), reason=f"no checkpoint at {CHECKPOINT}"
)
needs_model_dev = pytest.mark.skipif(
    not MODEL_DEV.is_file(), reason=f"no model_dev.yml at {MODEL_DEV}"
)


def _named_list(table, name):
    """The string entries of a `name = [...]` list in pyproject.toml.

    Textual for `test_gpu_frames._named_list`'s reason: `tomllib` is 3.11 and this repo is
    3.10, and one list does not justify a test-only parser dependency.
    """
    source = (REPO / "pyproject.toml").read_text()
    section = source.split(f"[{table}]", 1)[1] if table else source
    body = re.search(rf"^{re.escape(name)} = \[(.*?)^\]", section, re.S | re.M)
    assert body, f"no `{name} = [...]` under [{table}]"
    return re.findall(r'"([^"]+)"', body.group(1))


def _model_group():
    return _named_list("dependency-groups", "model")


def _yaml_scalar(text, key):
    """One `key: value` out of model_dev.yml, without taking a YAML parser as a dependency."""
    found = re.search(rf"^\s*{re.escape(key)}:\s*([^\n#]+)", text, re.M)
    assert found, f"no `{key}:` in model_dev.yml"
    return found.group(1).strip()


# ---------------------------------------------------------------------------------------
# The serde enum, which is not torch's own
# ---------------------------------------------------------------------------------------


def test_the_scalar_type_table_is_the_serde_one_and_not_torchs():
    """13 is bfloat16 here and quint8 in `torch.ScalarType`, and nothing raises either way.

    This is the whole reason the table is written out rather than looked up: reading the
    serialized graph with the runtime enum mislabels every tensor in the report without
    failing, which is exactly the kind of wrong a probe must not be.
    """
    assert _SERDE_SCALAR_TYPES[13] == "bfloat16"
    assert _SERDE_SCALAR_TYPES[6] == "float16"
    assert _SERDE_SCALAR_TYPES[7] == "float32"


# The serde enum spells its members in torch's C++ names (`BYTE`, `HALF`) and the probe
# reports the dtype names a reader recognises, so the two are compared through a written-out
# correspondence rather than by string. Writing it out is the point: it is the place a wrong
# pairing has to be argued for rather than assumed.
_SERDE_MEMBER_NAMES = {
    1: "BYTE",
    2: "CHAR",
    3: "SHORT",
    4: "INT",
    5: "LONG",
    6: "HALF",
    7: "FLOAT",
    8: "DOUBLE",
    9: "COMPLEXHALF",
    10: "COMPLEXFLOAT",
    11: "COMPLEXDOUBLE",
    12: "BOOL",
    13: "BFLOAT16",
}


def test_the_table_agrees_with_torchs_own_copy_of_it_where_torch_is_installed():
    """Torch is the `model` group, so this runs on a machine that has synced it and skips
    elsewhere. It is what stops the baked table drifting from the schema it copies."""
    schema = pytest.importorskip(
        "torch._export.serde.schema",
        reason="torch is the opt-in `model` group: uv sync --group model",
    )
    assert set(_SERDE_MEMBER_NAMES) == set(_SERDE_SCALAR_TYPES) - {0}
    for code, member in _SERDE_MEMBER_NAMES.items():
        assert schema.ScalarType(code).name == member, (
            f"serde ScalarType({code}) is {schema.ScalarType(code).name}, not {member} -- so "
            f"the probe would label it {_SERDE_SCALAR_TYPES[code]} and nothing would raise"
        )


# ---------------------------------------------------------------------------------------
# The archive reader
# ---------------------------------------------------------------------------------------


@needs_checkpoint
def test_the_archive_declares_the_shapes_the_probe_builds_its_inputs_from():
    info = read_archive(CHECKPOINT)

    assert info["inputs"]["images"]["shape"] == (1, 5, 6, 3, 288, 512)
    assert info["inputs"]["navigation"]["shape"] == (1, 20, 7)
    assert info["inputs"]["ego_state"]["shape"] == (1, 5, 2)
    assert all(described["dtype"] == "bfloat16" for described in info["inputs"].values())


@needs_checkpoint
def test_the_output_is_twenty_waypoints_eight_wide():
    """Both halves contradict what the Stage 9 plan assumed, so both are pinned.

    `av3_base.N_WAYPOINTS = 4` is a *fallback until the warm-up runs*, not this model's count,
    and the plan read it as the count. 8 is `MODELV2_OUTPUT_WIDTH`, so the bridge's
    `msg["modelv2"]` path is reachable rather than the 3-wide `derive` one we send today. A
    swapped checkpoint changing either is a decision for Phase C.2, not a silent difference.
    """
    (output,) = read_archive(CHECKPOINT)["outputs"].values()
    assert output["shape"] == (1, 20, 8)


@needs_checkpoint
def test_the_engine_is_the_archive_rather_than_the_weights_being_it():
    """The 1.2 GB is a serialized TensorRT engine; `data/weights/model.pt` is ~1 KB beside it.

    That asymmetry is what makes the version pins load-bearing and the checkpoint
    unrebuildable from here -- there is no source model in the file to recompile.
    """
    info = read_archive(CHECKPOINT)
    assert info["engine_bytes"] > 1_000_000_000
    assert info["engine_bytes"] > 0.99 * info["archive_bytes"]


def test_a_missing_checkpoint_is_named_rather_than_raised_from_zipfile(tmp_path):
    with pytest.raises(ProbeError, match="not found"):
        read_archive(tmp_path / "absent.ep")


def test_a_zip_that_is_not_a_pt2_archive_is_refused(tmp_path):
    import zipfile

    path = tmp_path / "not-a-model.ep"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hello.txt", "not a model")
    with pytest.raises(ProbeError, match="models/model.json"):
        read_archive(path)


# ---------------------------------------------------------------------------------------
# The pins, against what the archive says compiled it
# ---------------------------------------------------------------------------------------


@needs_checkpoint
def test_the_torch_pin_matches_the_version_that_exported_the_checkpoint():
    """The guard this file exists for. `2.8.0+cu128` in the archive against `torch==2.8.0`
    from the cu128 index -- the local version is the index's doing, so the pin is compared on
    the release part alone."""
    declared = read_archive(CHECKPOINT)["torch_version"]
    assert declared, "the archive states no torch_version"
    release = declared.split("+")[0]

    pinned = [entry for entry in _model_group() if entry.startswith("torch==")]
    assert pinned == [f"torch=={release}"], (
        f"the checkpoint was exported by torch {declared}, and pyproject.toml pins {pinned}. "
        "A serialized TensorRT engine does not degrade across versions, it fails to load."
    )


def test_the_three_packages_are_pinned_exactly_rather_than_bounded():
    """`>=` here would let a resolve pick a stack that cannot open the engine, and the failure
    lands minutes into a run rather than at sync time."""
    group = _model_group()
    assert {entry.split("==")[0] for entry in group} == {
        "torch",
        "torch-tensorrt",
        "tensorrt",
    }
    for entry in group:
        assert "==" in entry and ">" not in entry and "<" not in entry, entry


def test_the_model_group_is_opt_in_rather_than_a_project_dependency():
    """`uv sync` with no flags must stay small: torch cu128 is several GB and nothing in
    `src/osm_scenario` or the default `tools/` path imports it."""
    dependencies = _named_list("project", "dependencies")
    assert not [
        entry
        for entry in dependencies
        if entry.startswith(("torch", "tensorrt", "nvidia-", "triton"))
    ]


def test_the_cuda_wheel_indexes_are_declared_or_the_pins_resolve_to_the_cpu_build():
    """`torch==2.8.0` from PyPI is the CPU wheel, and `torch.cuda.is_available()` is then
    False with nothing about the pin looking wrong. The index is what makes it cu128."""
    source = (REPO / "pyproject.toml").read_text()
    assert "https://download.pytorch.org/whl/cu128" in source
    assert "https://pypi.nvidia.com" in source
    assert re.search(r"^torch = \{ index = \"pytorch-cu128\" \}", source, re.M)


# ---------------------------------------------------------------------------------------
# The archive against the configuration that will feed it, in Phase C.2
# ---------------------------------------------------------------------------------------


@needs_checkpoint
@needs_model_dev
def test_the_archive_agrees_with_model_dev_yml_about_every_shape_it_shares():
    """A mismatch here fails inside torch on the first frame of a drive, with a message naming
    a tensor dimension and not the file that got it wrong -- which is what
    `av3_base._apply_modifier`'s docstring says cost a whole pipeline run. Two files, checked
    before anything is built."""
    info = read_archive(CHECKPOINT)
    text = MODEL_DEV.read_text()

    _, t_frames, cameras, channels, height, width = info["inputs"]["images"]["shape"]
    assert t_frames == int(_yaml_scalar(text, "t_frames"))
    assert height == int(_yaml_scalar(text, "expected_camera_image_height"))
    assert width == int(_yaml_scalar(text, "expected_camera_image_width"))
    assert channels == 3

    order = _yaml_scalar(text, "camera_order").strip("[]")
    assert cameras == len([name for name in order.split(",") if name.strip()])

    _, n_route, features = info["inputs"]["navigation"]["shape"]
    assert n_route == int(_yaml_scalar(text, "n_route"))
    assert features == 7, "routes.route.ROUTE_FEATURE_DIM"


# ---------------------------------------------------------------------------------------
# The engine's own metadata, which is what says where else this file will run
# ---------------------------------------------------------------------------------------


class _Settings:
    """Stands in for torch-tensorrt's `CompilationSettings`, which the real blob holds.

    Only `vars()` is read, so a plain object with the same attributes is a faithful stand-in
    and the test needs neither torch nor a 1.2 GB engine.
    """

    def __init__(self, **fields):
        self.__dict__.update(fields)


def _metadata_blob(**fields):
    import base64
    import pickle

    return base64.b64encode(pickle.dumps({"settings": _Settings(**fields)}))


def test_the_build_settings_are_read_out_of_the_metadata_blob():
    """`hardware_compatible` is the field the whole portability question turns on.

    Measured on the real checkpoint it is **True**, which is why the engine opened on a
    laptop it was not built on -- and it is a deliberate build choice rather than a default,
    so a future checkpoint may well differ. Reading it is how the probe says so rather than
    the reader assuming this one is typical.
    """
    settings = decode_engine_metadata(
        _metadata_blob(hardware_compatible=True, immutable_weights=True, workspace_size=2**33)
    )
    assert settings["hardware_compatible"] is True
    assert settings["immutable_weights"] is True
    assert settings["workspace_size"] == 2**33


def test_an_undecodable_metadata_blob_is_reported_as_nothing_rather_than_raising():
    """This runs *after* the load and the warm-up have already succeeded.

    A checkpoint built by another torch-tensorrt may pickle a class this one cannot import,
    and turning a probe that worked into a traceback over a diagnostic line would report
    failure for a run that did not fail.
    """
    assert decode_engine_metadata(b"not base64 at all !!") == {}
    assert decode_engine_metadata(b"") == {}
    assert decode_engine_metadata(None) == {}


def test_the_field_names_are_the_order_torch_tensorrt_pickles_them_in():
    """`runtime.h`'s `SerializedInfoIndex`, which `_engine_state` zips against by position.

    Getting one wrong reads a real value under the wrong label -- `HW_COMPATIBLE` is at 6 and
    `SERIALIZED_METADATA` at 7, so an off-by-one turns a metadata blob into the portability
    answer and prints something confident and false.
    """
    assert _ENGINE_FIELDS.index("HW_COMPATIBLE") == 6
    assert _ENGINE_FIELDS.index("SERIALIZED_METADATA") == 7
    assert _ENGINE_FIELDS.index("ENGINE") == 3
    assert len(_ENGINE_FIELDS) == 10


def test_the_field_names_match_the_runtime_header_torch_tensorrt_ships():
    """Checked against the C++ enum in the installed headers, not against this file's comment.

    `torch_tensorrt/include/.../runtime.h` declares the order the engine pickles itself in, so
    a version that inserts a field is caught here rather than by a wrong diagnostic line.
    """
    header = (
        REPO
        / ".venv/lib/python3.10/site-packages/torch_tensorrt/include/torch_tensorrt"
        / "core/runtime/runtime.h"
    )
    if not header.is_file():
        pytest.skip(f"torch-tensorrt headers are the `model` group; none at {header}")

    # `[^{}]*` rather than `.*?`: runtime.h declares an earlier `typedef enum` and a
    # non-greedy `.*?` still starts at *that* one, swallowing its body into the match.
    body = re.search(r"typedef enum \{([^{}]*)\} SerializedInfoIndex;", header.read_text(), re.S)
    assert body, "runtime.h no longer declares SerializedInfoIndex as a typedef enum"
    # Comments go before the split, not after: the `SERIALIZATION_LEN` line carries
    # `// NEVER USED FOR DATA, USED TO ...`, whose comma splits into two more entries that
    # pass every later filter.
    without_comments = re.sub(r"//[^\n]*", "", body.group(1))
    declared = [
        entry.split("=")[0].strip().removesuffix("_IDX")
        for entry in without_comments.split(",")
        if entry.strip() and "SERIALIZATION_LEN" not in entry
    ]
    assert declared == list(_ENGINE_FIELDS)
