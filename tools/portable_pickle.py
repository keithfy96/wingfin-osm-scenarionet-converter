"""Pickle a scenario so a numpy-1 MetaDrive can open what a numpy-2 process wrote.

numpy 2 pickles an ndarray as a reference to `numpy._core`, a module numpy 1 does not have,
so the file fails to *open* with `ModuleNotFoundError: No module named 'numpy._core'` --
which names the pickle machinery rather than the version skew that caused it. That skew is
permanent here rather than something to wait out: MetaDrive's own checkout runs Python 3.8,
3.8 cannot have numpy 2 at all, and the container this repo ships runs 3.10 with numpy 2.2.
A drive exported in the container and watched on the laptop crosses that line every time.

`np.array` and `dtype.str` exist unchanged in both major versions, so rebuilding an array
through them puts no version-specific name in the stream. What comes back is a real
`ndarray` with the original dtype and shape -- not a list, which matters because MetaDrive
indexes these with tuples (`positions[:, :2]` in `parse_full_trajectory`).

**There is a second copy of this, in `src/osm_scenario/conversion.py:120`, and that is
deliberate.** The two files are on different interpreters by design -- this repo's 3.10
against MetaDrive's 3.8, the reason `tools/` imports nothing from `src/osm_scenario/`
(`tools/traffic.py:8`) -- so a shared module would have to be importable from both, which is
the arrangement the split exists to avoid. Ten lines of reducer is the cheaper of the two.
Keep them in step; a fix to one belongs in the other.
"""

import io
import pickle

import numpy as np

# Protocol 4 rather than the interpreter default, for the same reason as the reducer: the
# file is written to be opened somewhere else, by a Python chosen by MetaDrive's constraints
# rather than ours.
PICKLE_PROTOCOL = 4


class PortablePickler(pickle.Pickler):
    """`pickle.Pickler`, with every ndarray rebuilt through `np.array`. See the module docstring."""

    def reducer_override(self, obj):
        if isinstance(obj, np.ndarray):
            return np.array, (obj.tolist(), obj.dtype.str)
        return NotImplemented


def dump(payload, path):
    """Write `payload` to `path`. Buffered first, so a failed reduce leaves no partial file."""
    buffer = io.BytesIO()
    PortablePickler(buffer, protocol=PICKLE_PROTOCOL).dump(payload)
    with open(path, "wb") as handle:
        handle.write(buffer.getvalue())
    return len(buffer.getvalue())
