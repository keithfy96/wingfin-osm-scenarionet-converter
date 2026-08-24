"""One place that brings a frame back from the card, and says why it had to.

`image_on_cuda=True` (`engine_core.py:615` hands the key to every registered camera) keeps a
rendered frame in GPU memory as a **CuPy** array instead of copying it to the host, and turns
the offscreen observation stack into a CuPy array too (`image_obs.py:55-65,85-88`). That is
the whole of Phase B and it pays only where the consumer is also on the card.

**Everything in `tools/` that writes bytes is not.** A socket needs host bytes, an `.npz`
needs host bytes, and `numpy.asarray` on a CuPy array does not quietly copy - CuPy raises
`TypeError: Implicit conversion to a NumPy array is not allowed` on purpose, so that a
round trip across the PCIe bus can never be an accident. So the copy is written out here,
once, where it can be named in a comment rather than discovered in a traceback.

**It is a real cost and it is the reason `--image-on-cuda` buys nothing on a socket.** The
frame is rendered into GPU memory, copied back, base64'd and posted; against the CPU path it
has done strictly more work. `image_on_cuda` pays in Phase C, where the model is in the same
process and reads `__cuda_array_interface__` without the frame ever leaving the card.

**There is a fourth `numpy.asarray` on a frame and it is deliberately not converted.**
`camera_rig.CameraRig.read` builds `{name: numpy.asarray(sensor.perceive(to_float))}`, which
would raise the moment `image_on_cuda` reached it. It cannot today: only `drive.py` sets the
key, and the rig's two callers are `sensor_survey.py` and `step_timing.py`, neither of which
does. Converting it would put a `to_host` on a path no test can reach and no drive can
exercise. The `ActionRecorder` call site was in exactly that state until the recorder learned
to split an offscreen observation; it is live and tested now, and this one is not. Named here
so the next person who wires `--image-on-cuda` into the sweep knows it is one line, and where.
"""

from __future__ import annotations


def is_device_array(value) -> bool:
    """True for an array that lives in GPU memory rather than in this process's heap.

    Tested by the interface rather than by `isinstance(value, cupy.ndarray)`, so that it does
    not import CuPy - `tools/` has to keep running in an environment without the `gpu` group,
    which is the default one - and so that any other producer of a device array (torch, a
    DLPack capsule holder) is recognised on the same terms.
    """
    return hasattr(value, "__cuda_array_interface__") and not hasattr(value, "__array_interface__")


def to_host(value):
    """A device array copied to host memory; anything else returned untouched.

    `.get()` is CuPy's own name for the copy. Guarded rather than assumed so that a torch CUDA
    tensor - which carries `__cuda_array_interface__` and `.cpu()` instead - is not silently
    passed on as something a `tobytes()` can read.
    """
    if not is_device_array(value):
        return value
    getter = getattr(value, "get", None)
    if callable(getter):
        return getter()
    to_cpu = getattr(value, "cpu", None)
    if callable(to_cpu):
        return to_cpu().numpy()
    raise TypeError(
        f"{type(value)} is on the GPU and offers neither .get() nor .cpu(); it cannot be "
        "written to a socket or a file without a copy this module knows how to make."
    )
