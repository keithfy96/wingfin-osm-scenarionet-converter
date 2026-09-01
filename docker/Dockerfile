# One environment for the whole repo: the converter, MetaDrive, and the AV3 model.
#
# The point of this image is the step-timing sweep: every row of its CSV carries the host, CPU,
# GPU, GL ceiling and library versions so two machines' files concatenate into one spreadsheet,
# and that comparison only means something if both machines are provably running the same
# simulator. Reproducing a 3.4 GB venv and a git checkout by hand on each box is not provably
# anything; `uv.lock` plus a pinned MetaDrive commit is.
#
# It carries the Stage 9 model stack for the same reason one step further on: an AV3 forward pass
# is ~1 s on this laptop's power-capped RTX 4050, which is the whole argument for running it
# somewhere else -- and a figure from another machine is only worth having if the stack that
# produced it is pinned rather than reassembled by hand. The checkpoint itself is NOT in here --
# 1.2 GB, and `compose.yaml` mounts it at /models the way it mounts a camera-rig spec at /rig.
# The `model_dev.yml` beside it is tracked, at `config/model_dev.yml`, and reached through the
# repo's own /work mount, so a rig is handed one file rather than two.
#
# Two things about it are worth knowing before changing anything:
#
# * **There is one interpreter, not two.** The repo has always run MetaDrive on its own 3.8 venv
#   (numpy 1.24) beside this repo's 3.10 (numpy 2.2), which is why `drive.sh`, `sensor-survey.sh`
#   and `step-timing.sh` each shell out through `METADRIVE_PYTHON`. MetaDrive turns out to run on
#   3.10: no `python_requires` cap, no `ext_modules`, no numpy-2-removed aliases, and its ten
#   `copy=False` call sites are all `.astype()` / `np.nan_to_num()`, neither of which numpy 2
#   changed. So here `METADRIVE_PYTHON` points at the same venv as everything else and every
#   script in `scripts/` works, not just the sweep.
#
# * **It renders on the GPU with no X server.** panda3d ships `libp3headlessgl.so` (EGL) and its
#   own `Config.prc` already lists it as the fallback after `pandagl` (GLX). Measured with DISPLAY
#   unset: GLX lands on the integrated card at 16384 max texture, EGL on the discrete one at
#   32768. The `sed` below makes that the deliberate first choice rather than the result of a
#   failed GLX attempt, and leaves `pandagl` as the fallback so mounting an X socket still gets
#   the 3D row.
FROM ubuntu:22.04

# 22.04 and not 24.04: 24.04 ships an `ubuntu` user at UID 1000, which collides with running the
# container as the host user -- which compose does, so a CSV written into the mounted workspace
# belongs to Keith rather than to root.

ENV DEBIAN_FRONTEND=noninteractive

# panda3d links the X client libraries even for an offscreen context, so they are needed with no
# X server anywhere. libglvnd/libegl are the loaders the NVIDIA container toolkit injects its ICD
# behind; libglib2.0-0 and libsm6 are cv2's.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        libegl1 \
        libgl1 \
        libgles2 \
        libglib2.0-0 \
        libglvnd0 \
        libgomp1 \
        libsm6 \
        libx11-6 \
        libxcursor1 \
        libxext6 \
        libxi6 \
        libxrandr2 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# The venv lives outside /work on purpose: /work is bind-mounted over at runtime, and the host's
# own .venv is in it. UV_PROJECT_ENVIRONMENT points `uv run` here instead, so a `uv run pytest`
# inside the container cannot reach into -- or resync -- the host's environment.
# UV_PYTHON_INSTALL_DIR is not tidiness. uv downloads its managed CPython into
# $HOME/.local/share/uv/python, which at build time is /root -- mode 700 -- and the venv's
# `python` is a symlink into it. The container runs as the host's uid so that reports written
# into the mounted workspace are not owned by root, and that user cannot read /root: the
# interpreter dies before it starts, with `sys.executable = ''` and
# `ModuleNotFoundError: No module named 'encodings'`, which reads as a broken image rather than
# a permissions problem. /opt is world-readable.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    UV_NO_SYNC=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /work

# Built in /work rather than a staging directory so the editable install of `osm_scenario` points
# at /work/src -- which the bind mount then replaces with the live source, rather than leaving a
# stale copy baked into the image.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# All three groups, in one environment, because the AV3 model has to run where MetaDrive runs:
# `tools/av3_probe.py` builds an env and a forward pass in the same process, so a second image
# would only move the problem. It is why the image is **13.2 GB** rather than 2.88 -- measured
# in the host venv, `gpu` and `model` are 10.5 GB of it: tensorrt_libs 4.4 G, nvidia 4.1 G,
# torch 1.7 G, cupy 236 M. A sweep that never touches the model carries them; that was the trade
# taken deliberately, against a second image, because this is built once and then used.
#
# Two things that would otherwise be looked for and are already here. **CUDA needs nothing
# added**: NVIDIA_DRIVER_CAPABILITIES below already carries `compute`, and torch/TensorRT/CuPy
# bring their own libraries as pip wheels rather than wanting a system toolkit. And **--frozen is
# satisfied without touching the lock** -- all three groups are already resolved in uv.lock
# (`uv lock --check` passes), because the host has been running them since Phase C.
#
# `uv cache clean` is in the same RUN and is not tidiness -- it is 16 GB. UV_LINK_MODE=copy above
# means uv copies each wheel out of its download cache into the venv rather than hardlinking, so
# both exist; committed together this layer measured **28.7 GB against a 13 GB venv**. Same layer
# or it saves nothing, and `uv cache clean` rather than `rm -rf` a guessed path, because uv knows
# where its own cache is. It was there before the model groups too -- `sim` alone is small enough
# that half a gigabyte of it went unnoticed in a 2.88 GB image.
RUN uv venv --python 3.10 /opt/venv \
    && uv sync --frozen --group sim --group gpu --group model \
    && uv cache clean \
    && chmod -R a+rX /opt/python /opt/venv

# The 168 MB asset pack is not in MetaDrive's git tree (`git ls-files metadrive/assets` is empty);
# it is fetched from the MetaDrive-0.4.3 release. Doing it at build time means the runtime needs
# no network, which matters for a benchmark that should not be waiting on a download.
RUN python -m metadrive.pull_asset

# EGL first, GLX second. See the header.
RUN PRC="$(python -c 'import panda3d, os; print(os.path.join(os.path.dirname(panda3d.__file__), "etc", "Config.prc"))')" \
    && sed -i 's/^load-display pandagl$/load-display p3headlessgl/; s/^aux-display p3headlessgl$/aux-display pandagl/' "$PRC" \
    && grep -E '^(load|aux)-display' "$PRC"

# `utility` is not optional: it is what injects nvidia-smi, and step_timing.py reads the CSV's
# `gpu_name` column out of it. `graphics` is what injects libEGL_nvidia.so.
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=graphics,compute,utility

# The one thing the container toolkit does *not* inject, and without it the whole image is a
# software renderer wearing a GPU's clothes. libglvnd picks a driver by reading the manifests in
# this directory; the toolkit installs libEGL_nvidia.so.0 but leaves the directory holding nothing
# but Mesa's 50_mesa.json, so every EGL context lands on llvmpipe -- which does not fail, it just
# quietly runs the benchmark on the CPU. Measured before this file existed: `getDriverRenderer()`
# reported "llvmpipe (LLVM 15.0.7, 256 bits)" at 16384 max texture, against the RTX's 32768.
# `10_` so it sorts ahead of Mesa. The same file is what nvidia/opengl base images ship.
RUN mkdir -p /usr/share/glvnd/egl_vendor.d \
    && printf '%s\n' \
        '{' \
        '    "file_format_version" : "1.0.0",' \
        '    "ICD" : {' \
        '        "library_path" : "libEGL_nvidia.so.0"' \
        '    }' \
        '}' > /usr/share/glvnd/egl_vendor.d/10_nvidia.json

# `ros` (rosbags, apsw, lz4, ruamel-yaml, zstandard -- 39 MB) in its **own layer, deliberately
# below the big one**. Adding it to the `uv sync` above would invalidate that layer, and that
# layer ends in `uv cache clean`: rebuilding it re-downloads torch, TensorRT and CuPy and re-runs
# the 168 MB `metadrive.pull_asset`, so four small pure-Python wheels would cost most of a 13 GB
# re-sync. Here, every layer above stays cached.
#
# **Why the image needs it at all.** `tools/drive.py --ros-bag` runs on METADRIVE_PYTHON, which on
# the host is the MetaDrive checkout's 3.8 and has no `rosbags` wheel; the documented way out --
# in `ros-bag.sh`'s header, in `docs/reference/ros-bags.md`, and in the refusal
# `ros_frame.refuse_if_unsupported` prints -- is "run it in the container, one 3.10 interpreter".
# That was true of the interpreter and false of the library until this line existed, so the
# container refused every bag it was pointed at.
#
# All four groups named, never `--group ros` alone: syncing one group removes the others, which
# is the same trap `uv sync --group model` sets on the host.
#
# **And no `chmod -R` here, unlike the layer above.** chmod modifies every file it touches, and on
# overlayfs modifying a file in a lower layer copies it up into this one -- so `chmod -R a+rX
# /opt/venv` would write a fresh 13 GB copy of the venv into a layer whose actual content is
# 39 MB. Measured: the sync installs its 5 packages in 0.8 s and the chmod was still running two
# and a half minutes later. It is not needed either: uv writes as root under the build's 022
# umask, so the files land 644 and the directories 755, which the non-root runtime uid reads. The
# layer above needs its chmod for /opt/python, whose managed CPython arrives mode 700; nothing
# here touches that.
RUN uv sync --frozen --group sim --group gpu --group model --group ros \
    && uv cache clean

# The container runs as the host's uid, which has no /etc/passwd entry and so no home. panda3d
# caches compiled shaders and matplotlib writes a font list; both need somewhere to go.
ENV HOME=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib \
    METADRIVE_PYTHON=/opt/venv/bin/python

CMD ["bash"]

# The four groups from the two `uv sync` lines above, where a caller can read them back without
# starting a container. `sim.sh` unions the `--group` flags across every uv sync line, so a group
# added in its own layer is picked up here without the check being told about the split.
# **Last in the file on purpose**: a LABEL invalidates the build cache for everything after it,
# and editing this line should never cost a re-download of the asset pack or a re-sync of a
# 13 GB venv. Nothing may be added below it.
# `scripts/sim.sh` compares this against the --group flags above and says so when the running
# image is missing one, because nothing else does: `docker compose run` reuses whatever holds the
# `wingfin-sim` tag and never rebuilds, and a `docker compose build` run before the `git pull`
# rebuilds the *old* recipe out of BuildKit's cache in seconds. Both look like success. A rig
# spent a morning on `--model-checkpoint needs torch` for exactly that reason, against an image
# built before `--group gpu --group model` was added to the line above.
#
# An image with **no** such label is not thereby stale. This line was added after the groups were,
# so an image built between the two carries all three and no label to say so -- and reading that
# silence as "all three missing" told the rig its working image was missing everything, moments
# before that same run loaded torch, TensorRT and the checkpoint. `sim.sh` says nothing when the
# label is absent, on purpose.
#
# A label and not `docker history`, and above all not image size: this laptop is on the classic
# overlay2 store and prints one SIZE, while the rig is on the containerd snapshotter and prints
# CONTENT SIZE and DISK USAGE, which are different measurements and not comparable to it. Labels
# read the same on both, through `docker image inspect`, in milliseconds. **Keep in step with the
# line above.**
LABEL wingfin.groups="sim gpu model ros"
