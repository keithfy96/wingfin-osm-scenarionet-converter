"""Load the AV3 checkpoint and say whether this machine can run it at all.

    uv run --group model python tools/model_probe.py

    # or, from inside scripts/
    ./model-probe.sh
    ./model-probe.sh -- --with-simulator

Stage 9, Phase C.1. It settles the two questions the rest of Phase C is built on, and it
answers nothing else: it does not drive, does not steer and produces no file. `--with-simulator`
is the only part that touches MetaDrive.

**Three things did not need this tool**, and are read straight out of the checkpoint archive
by `read_archive` below with no torch installed at all.
`step_440000_trt_direct_full.ep` is a `pt2` zip holding a 3,287-byte `models/model.json`
graph, a 1,261-byte weights blob, and a **1,275,435,821-byte serialized TensorRT engine** as
a constant. From `model.json`:

* `torch_version` is **`2.8.0+cu128`** - so the `model` dependency group's pins are evidence
  from the file rather than a comment repeated from `wing-sim/evaluation/pyproject.toml`.
* the input shapes are static: `images (1, 5, 6, 3, 288, 512)`, `navigation (1, 20, 7)`,
  `ego_state (1, 5, 2)`, all bfloat16 - matching `model_dev.yml`'s `t_frames: 5`, six
  cameras, 512x288, `n_route: 20` and `routes.route.ROUTE_FEATURE_DIM = 7`.
* the output is **`(1, 20, 8)`**. That is **20 waypoints, not the 4** the Stage 9 plan
  assumed from `av3_base.N_WAYPOINTS`, and **8 wide**, which is
  `av3_base.MODELV2_OUTPUT_WIDTH` - `[x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y]`. So the
  bridge's richer `msg["modelv2"]` / `from_predicted` path is reachable rather than the
  3-wide `derive` one we send today, and 20 is already in its prebuilt `AV3_MPC_MENU`
  (`"4 16 20 32"`), so there is no connect-time code generation and no slow first tick.

**What genuinely needs running, and why it is sharper than "does a torch program load".**
The 1.275 GB constant is a *serialized TensorRT engine*, not weights - the 1,261-byte
`data/weights/model.pt` beside it is the giveaway. A TRT engine is built against one SM
architecture and one TensorRT version, so where a plain `.pt` would load anywhere,
this either deserialises on this card or does not:

1. **Does it deserialise on an RTX 4050 (Ada, sm_89)?** It was compiled elsewhere. A "no"
   here invalidates the whole of Phase C.2 and C.3, which is why the plan runs this first.
2. **Does it fit?** 6141 MiB of VRAM total. ~1.3 GB of engine plus activations on its own is
   one question; sharing the card with MetaDrive's renderer - and later with CuPy in the same
   context under `--image-on-cuda` - is the one C.2 actually needs, which is `--with-simulator`.

**The archive is read before torch is imported, deliberately.** A failure to deserialise is
the expected outcome to design for, and when it comes it should print what the file wanted
beside what is installed - not a bare `RuntimeError` from inside TensorRT. That is the same
reason `drive.py` checks `_cuda_enable` itself rather than letting MetaDrive assert minutes
into a terrain build.

**This is the one file in `tools/` that is 3.10-only by construction.** Everything else there
is parsed with MetaDrive's own 3.8 interpreter before being believed; torch 2.8 has no 3.8
wheel, so that check does not apply here and its absence is not an oversight.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The fork checkout is the only copy on this machine: `wing-sim/evaluation/models/` is a
# gitignore-everything directory, so the checkpoint travels with the openpilot fork rather
# than with the evaluation harness. Overridable with --checkpoint / MODEL_CHECKPOINT.
DEFAULT_CHECKPOINT = (
    "/home/keith/Desktop/work/wingfin/wingfin-openpilot-temp/assets/models/"
    "step_440000_trt_direct_full.ep"
)

# `torch/_export/serde/schema.py`'s **own** ScalarType, which is NOT `torch.ScalarType` - the
# two disagree from index 1 onwards, and reading the serialized graph with the runtime enum
# turns bfloat16 into quint8 without raising. Baked here so the archive stays readable with no
# torch installed, and `tests/unit/test_model_probe.py` asserts it against torch's own copy
# whenever torch is importable, so a drift in either is a failing test rather than a wrong
# label in a report.
_SERDE_SCALAR_TYPES = {
    0: "unknown",
    1: "uint8",
    2: "int8",
    3: "int16",
    4: "int32",
    5: "int64",
    6: "float16",
    7: "float32",
    8: "float64",
    9: "complex32",
    10: "complex64",
    11: "complex128",
    12: "bool",
    13: "bfloat16",
}


class ProbeError(RuntimeError):
    """Something about the checkpoint or the installed stack, reported rather than raised raw."""


def _sizes(tensor_value) -> tuple[int, ...]:
    return tuple(int(entry["as_int"]) for entry in tensor_value["sizes"])


def read_archive(path) -> dict:
    """What the checkpoint declares about itself, with no torch and no CUDA.

    Returns the graph's user inputs and its output as `{name: {"shape", "dtype", "dtype_code"}}`,
    the `torch_version` it was exported by, and the size of the serialized TensorRT engine.

    The exported program's input list starts with the engine as a *custom object* rather than a
    tensor, so the user inputs are taken from `signature.input_specs` - reading `graph.inputs`
    positionally would offset every name by one. The `execute_engine` node then lists its own
    tensors in a **different order** (`images, ego_state, navigation`) from the module signature
    (`images, navigation, ego_state`); torch-tensorrt maps them by name, so the signature is the
    order a caller passes, and the node order is not a second opinion about it.
    """
    path = os.fspath(path)
    if not os.path.isfile(path):
        raise ProbeError(f"checkpoint not found: {path}")

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        graphs = [name for name in names if name.endswith("/models/model.json")]
        if len(graphs) != 1:
            raise ProbeError(
                f"{path}: expected exactly one models/model.json in the pt2 archive, "
                f"found {len(graphs)} of {len(names)} entries"
            )
        document = json.loads(archive.read(graphs[0]))
        engine_bytes = max(
            (archive.getinfo(name).file_size for name in names if "/data/constants/" in name),
            default=0,
        )

    module = document["graph_module"]
    values = module["graph"]["tensor_values"]

    def described(name):
        value = values[name]
        code = int(value["dtype"])
        return {
            "shape": _sizes(value),
            "dtype": _SERDE_SCALAR_TYPES.get(code, f"serde-scalar-type-{code}"),
            "dtype_code": code,
        }

    inputs = {}
    for spec in module["signature"]["input_specs"]:
        user = spec.get("user_input")
        if user is None:  # the engine itself, a custom object rather than a tensor
            continue
        name = user["arg"]["as_tensor"]["name"]
        inputs[name] = described(name)

    outputs = {}
    for spec in module["signature"]["output_specs"]:
        name = spec["user_output"]["arg"]["as_tensor"]["name"]
        outputs[name] = described(name)

    return {
        "path": path,
        "torch_version": document.get("torch_version", ""),
        "inputs": inputs,
        "outputs": outputs,
        "engine_bytes": engine_bytes,
        "archive_bytes": os.path.getsize(path),
    }


def _line(label: str, text: str) -> None:
    print(f"{label:<13}{text}")


def _shape_text(described) -> str:
    return f"{tuple(described['shape'])} {described['dtype']}"


def _nvidia_smi_used_mib() -> int | None:
    """VRAM this process holds, as the driver sees it.

    `torch.cuda.memory_allocated` cannot answer this on its own: a TensorRT engine allocates
    its weights and its execution context outside the caching allocator, so torch reports a
    fraction of what the card is really holding. Both are printed for that reason.
    """
    try:
        output = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for row in output.splitlines():
        parts = [part.strip() for part in row.split(",")]
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) == os.getpid():
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def _card_free_mib() -> tuple[int, int] | None:
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    if not output:
        return None
    parts = [part.strip() for part in output[0].split(",")]
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def _report_memory(stage: str, torch) -> None:
    allocated = torch.cuda.memory_allocated() / 2**20
    reserved = torch.cuda.max_memory_reserved() / 2**20
    process = _nvidia_smi_used_mib()
    card = _card_free_mib()
    process_text = "n/a" if process is None else f"{process} MiB"
    card_text = "" if card is None else f", card {card[0]}/{card[1]} MiB"
    _line(
        "vram",
        f"{stage:<12} torch {allocated:7.1f} MiB allocated, {reserved:7.1f} peak reserved; "
        f"process {process_text}{card_text}",
    )


# `runtime.h`'s `SerializedInfoIndex`, which is the order `torch.classes.tensorrt.Engine`
# pickles itself in. Named rather than indexed by number so a torch-tensorrt that inserts a
# field is a wrong *label* on a diagnostic line rather than a wrong value read confidently -
# and `_engine_state` checks the length before trusting any of it.
_ENGINE_FIELDS = (
    "ABI_TARGET",
    "NAME",
    "DEVICE",
    "ENGINE",
    "INPUT_BINDING_NAMES",
    "OUTPUT_BINDING_NAMES",
    "HW_COMPATIBLE",
    "SERIALIZED_METADATA",
    "TARGET_PLATFORM",
    "REQUIRES_OUTPUT_ALLOCATOR",
)


def _engine_state(module) -> dict:
    """The engine's own ten-field state, off the already-loaded module.

    **Read here rather than by deserialising the plan a second time**, which is the obvious
    alternative and the wrong one: `trt.Runtime.deserialize_cuda_engine` would put a second
    ~1.5 GB copy on the card, and under `--with-simulator` there are 1151 MiB spare. This
    costs nothing - the module is already loaded and `__getstate__` returns strings.

    Cross-checked once against the direct route, which is why the cheap one is trusted:
    `HW_COMPATIBLE == "1"` here and
    `deserialize_cuda_engine(...).hardware_compatibility_level == AMPERE_PLUS` there, on the
    same engine. Only `hardware_compatibility_level` distinguishes `AMPERE_PLUS` from
    `SAME_COMPUTE_CAPABILITY`; the flag alone says only "not NONE", and that is what the
    report claims.

    Returns `{}` rather than raising for anything unexpected. This is a diagnostic line on a
    run whose real work has already succeeded, and a torch-tensorrt that renames its custom
    class must not turn a working probe into a traceback.
    """
    engines = [name for name in dir(module) if name.endswith("_engine")]
    if len(engines) != 1:
        return {}
    try:
        state, _ = getattr(module, engines[0]).__getstate__()
    except Exception:  # noqa: BLE001 - diagnostic only, never fatal to a probe that ran
        return {}
    if not isinstance(state, (list, tuple)) or len(state) != len(_ENGINE_FIELDS):
        return {"_unexpected": f"{len(state) if hasattr(state, '__len__') else '?'} fields"}
    # `strict=` is available here where it is not elsewhere in `tools/`: this file is
    # 3.10-only because torch is, so B905 has a real fix rather than the indexed loop
    # `policy_client.py` had to use for MetaDrive's 3.8.
    return dict(zip(_ENGINE_FIELDS, state, strict=True))


def decode_engine_metadata(blob) -> dict:
    """The compilation settings torch-tensorrt pickles into `SERIALIZED_METADATA`.

    base64 of a plain pickle holding `{"settings": CompilationSettings, "weight_name_map":
    ...}`. The settings object cannot be reconstructed without torch-tensorrt importable, and
    by the time this is called it is - but a checkpoint built by a different version may
    reference a class this one does not have, so every failure returns `{}`.

    **Unpickling here is reading our own toolchain's output on a file the user pointed at**,
    which is the same trust already extended to `torch_tensorrt.load` two lines earlier; it
    is not a general-purpose loader.
    """
    import base64
    import pickle

    if not blob:
        return {}
    try:
        payload = base64.b64decode(blob)
        settings = pickle.loads(payload).get("settings")
    except Exception:  # noqa: BLE001 - a diagnostic, and a foreign checkpoint may not decode
        return {}
    if settings is None or not hasattr(settings, "__dict__"):
        return {}
    return dict(vars(settings))


def _report_engine(module) -> None:
    """Three lines saying what this engine will and will not run on, and what it is made of.

    It exists because "it loaded" does not answer "will it load anywhere else", and the answer
    is not guessable: an engine built at `HardwareCompatibilityLevel.NONE` is locked to one
    architecture, and one built `AMPERE_PLUS` runs on sm_80 and up at a documented cost in
    speed. This checkpoint is the second, deliberately - its own settings say
    `hardware_compatible: True`.
    """
    state = _engine_state(module)
    if not state:
        _line("engine", "the engine's own state could not be read; skipping its report")
        return
    if "_unexpected" in state:
        _line("engine", f"unexpected engine state ({state['_unexpected']}); skipping")
        return

    portable = state.get("HW_COMPATIBLE") == "1"
    if portable:
        _line("portable", "yes (HW_COMPATIBLE=1) - runs on any Ampere-or-newer card, sm_80+")
        print(
            "             below that it refuses rather than running slowly, and NVIDIA "
            "documents\n             the portability itself as costing speed."
        )
    else:
        _line("portable", "no (HW_COMPATIBLE=0) - locked to the architecture it was built for")
        print("             it will not deserialise on any other.")

    settings = decode_engine_metadata(state.get("SERIALIZED_METADATA"))
    precisions = settings.get("enabled_precisions")
    described = (
        "/".join(sorted(getattr(entry, "name", str(entry)) for entry in precisions))
        if precisions
        else "unstated"
    )
    refit = settings.get("immutable_weights")
    _line(
        "plan",
        f"{described} precision, weights {'sealed' if refit else 'refittable'}"
        + (
            f", workspace budget {settings['workspace_size'] / 2**30:.0f} GiB at build"
            if settings.get("workspace_size")
            else ""
        ),
    )

    # DEVICE is re-derived when the plan is deserialised, so it names *this* card and is not
    # evidence of the machine that built it. Printed with that said, because read as a build
    # record it is exactly the kind of wrong that looks like information.
    _line(
        "built for",
        f"{state.get('TARGET_PLATFORM', '?')}, torch-tensorrt ABI {state.get('ABI_TARGET', '?')}",
    )
    print(
        "             no build GPU is recorded: the DEVICE field reads "
        f"{state.get('DEVICE', '?')!r},\n             which is this machine, rewritten on load."
    )



def _build_simulator(dataset, rig_path, verbose=False):
    """A MetaDrive env holding a real camera rig, so the model is loaded beside a renderer.

    Offscreen rather than `--render none`, because with no graphics there are no camera
    buffers and no terrain - which is exactly the VRAM this is here to measure. It is the
    reason this mode needs `exec_with_gpu`: a GL context on the iGPU beside a CUDA context on
    the RTX is `cudaErrorUnknown(999)` under `--image-on-cuda`, and even without that flag a
    figure measured against the wrong card describes nothing.
    """
    sys.path.insert(0, str(REPO / "tools"))
    from agent_env import make_env
    from camera_rig import load_rig

    rig = load_rig(rig_path)
    return rig, make_env(
        dataset,
        render="offscreen",
        verbose=verbose,
        sensors=rig.sensors(),
        image_observation=True,
        vehicle_config={"image_source": rig.image_source()},
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load the AV3 checkpoint and report whether it runs on this machine.",
    )
    parser.add_argument(
        "--checkpoint",
        default=os.environ.get("MODEL_CHECKPOINT", DEFAULT_CHECKPOINT),
        help="the .ep to load. Defaults to MODEL_CHECKPOINT, then the fork checkout's copy.",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=50,
        help="forward passes to time after the warm-up. The median is reported, so one cold "
        "call cannot be the number (default: 50).",
    )
    parser.add_argument(
        "--with-simulator",
        action="store_true",
        help="build a MetaDrive env with rigs/cams.txt FIRST, then load the model beside it in "
        "the same process. This is the VRAM question Phase C.2 actually has to answer; the "
        "plain run answers only whether the engine deserialises at all. Needs --group sim.",
    )
    parser.add_argument(
        "--dataset",
        default="workspaces/junction-1/scenarionet-10hz",
        help="which dataset --with-simulator builds its env over (default: %(default)s).",
    )
    parser.add_argument(
        "--camera-rig",
        default="rigs/cams.txt",
        help="the rig --with-simulator mounts (default: %(default)s).",
    )
    parser.add_argument("--verbose", action="store_true", help="MetaDrive's own logging.")
    arguments = parser.parse_args()

    # ---- what the file says, before anything heavy is imported -------------------------
    try:
        info = read_archive(arguments.checkpoint)
    except ProbeError as error:
        print(f"result       FAILED: {error}")
        return 1

    _line("checkpoint", f"{info['path']}")
    _line(
        "archive",
        f"{info['archive_bytes'] / 2**20:.1f} MiB, of which a "
        f"{info['engine_bytes'] / 2**20:.1f} MiB serialized TensorRT engine",
    )
    _line("exported by", f"torch {info['torch_version'] or 'unstated'}")
    for name, described in info["inputs"].items():
        _line("input", f"{name:<11} {_shape_text(described)}")
    for name, described in info["outputs"].items():
        _line("output", f"{name:<11} {_shape_text(described)}")

    # ---- the stack that has to match it -------------------------------------------------
    _line("interpreter", sys.executable)
    try:
        import torch
    except ImportError as error:
        print(
            f"result       FAILED: {error}. The model stack is an opt-in dependency group: "
            "`uv sync --group model`, and run through `uv run --group model` or "
            "scripts/model-probe.sh."
        )
        return 1

    installed = torch.__version__
    _line("torch", installed)
    if info["torch_version"] and installed != info["torch_version"]:
        print(
            f"result       FAILED: this checkpoint was exported by torch "
            f"{info['torch_version']} and {installed} is installed. The 1.275 GB constant is a "
            "serialized TensorRT engine, so a version difference is not a compatibility "
            "gradient - it either deserialises or it does not. Pin the `model` group to the "
            "version the archive names."
        )
        return 1

    try:
        import tensorrt
        import torch_tensorrt
    except ImportError as error:
        print(f"result       FAILED: {error}. `uv sync --group model` installs all three.")
        return 1
    _line("torch-trt", torch_tensorrt.__version__)
    _line("tensorrt", tensorrt.__version__)

    if not torch.cuda.is_available():
        print(
            "result       FAILED: torch.cuda.is_available() is False, so nothing here can be "
            f"measured. Interpreter {sys.executable}; torch {installed}. A CUDA-less torch "
            "wheel resolving in place of the cu128 one is the usual cause - check the "
            "pytorch-cu128 index in pyproject.toml is being used."
        )
        return 1

    capability = torch.cuda.get_device_capability(0)
    _line(
        "device",
        f"{torch.cuda.get_device_name(0)}, compute capability sm_{capability[0]}{capability[1]}, "
        f"CUDA {torch.version.cuda}",
    )
    _report_memory("at import", torch)

    # ---- optionally, a simulator in the same process ------------------------------------
    env = None
    if arguments.with_simulator:
        try:
            rig, env = _build_simulator(arguments.dataset, arguments.camera_rig, arguments.verbose)
        except Exception as error:  # noqa: BLE001 - reported with its type, never a bare trace
            print(f"result       FAILED building the simulator: {type(error).__name__}: {error}")
            return 1
        env.reset(seed=0)
        _line(
            "simulator",
            f"{len(rig)} cameras from {arguments.camera_rig}, {rig.megabytes:.2f} MB of image "
            f"a step, over {arguments.dataset}",
        )
        _report_memory("+ simulator", torch)

    # ---- the two unknowns ---------------------------------------------------------------
    status = 0
    try:
        started = time.perf_counter()
        module = torch_tensorrt.load(info["path"]).module()
        load_seconds = time.perf_counter() - started
        _line("load", f"{load_seconds:.1f} s")
        _report_memory("+ model", torch)
        _report_engine(module)

        # Built from the archive's own declared shapes rather than from model_dev.yml, so a
        # swapped checkpoint is met with its real requirements instead of last week's.
        dtypes = {
            "bfloat16": torch.bfloat16,
            "float16": torch.float16,
            "float32": torch.float32,
        }
        arguments_in = []
        for name, described in info["inputs"].items():
            dtype = dtypes.get(described["dtype"])
            if dtype is None:
                print(
                    f"result       FAILED: input {name} declares dtype {described['dtype']} "
                    f"(serde code {described['dtype_code']}), which this probe does not build."
                )
                return 1
            arguments_in.append(
                torch.zeros(described["shape"], dtype=dtype, device="cuda")
            )

        with torch.no_grad():
            output = module(*arguments_in)
        torch.cuda.synchronize()
        _report_memory("+ warm-up", torch)

        shape = tuple(output.shape)
        width = int(shape[-1])
        layout = {
            2: "waypoints [x, y]",
            8: "full modelv2 [x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y]",
        }.get(width, "UNRECOGNISED - av3_base._set_output_shape rejects anything but 2 or 8")
        _line("prediction", f"{shape} {output.dtype} -> {shape[-2]} waypoints, {width} wide")
        _line("layout", layout)

        timings = []
        for _ in range(max(1, arguments.passes)):
            torch.cuda.synchronize()
            started = time.perf_counter()
            with torch.no_grad():
                module(*arguments_in)
            torch.cuda.synchronize()
            timings.append((time.perf_counter() - started) * 1000.0)
        timings.sort()
        _line(
            "forward",
            f"{statistics.median(timings):.2f} ms median over {len(timings)}, "
            f"{timings[0]:.2f} best, {timings[int(len(timings) * 0.95)]:.2f} p95",
        )

        card = _card_free_mib()
        headroom = "" if card is None else f", {card[1] - card[0]} MiB still free on the card"
        print(
            f"result       OK: the engine deserialised on sm_{capability[0]}{capability[1]} and "
            f"ran{' beside a MetaDrive renderer' if env is not None else ''}{headroom}"
        )
    except Exception as error:  # noqa: BLE001 - the expected outcome, reported not raised
        print(
            f"result       FAILED loading or running the engine: {type(error).__name__}: {error}"
        )
        print(
            "             A serialized TensorRT engine is built for one SM architecture and one "
            f"TensorRT version. This card is sm_{capability[0]}{capability[1]} running TensorRT "
            f"{tensorrt.__version__}; the checkpoint was exported by torch "
            f"{info['torch_version']}. If the message mentions serialization, deserialization or "
            "a plan file, it was compiled for a different GPU and Phase C needs it rebuilt."
        )
        status = 1
    finally:
        if env is not None:
            env.close()

    return status


if __name__ == "__main__":
    raise SystemExit(main())
