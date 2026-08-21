# One environment for the whole repo, including MetaDrive.
#
# The point of this image is the step-timing sweep: every row of its CSV carries the host, CPU,
# GPU, GL ceiling and library versions so two machines' files concatenate into one spreadsheet,
# and that comparison only means something if both machines are provably running the same
# simulator. Reproducing a 3.4 GB venv and a git checkout by hand on each box is not provably
# anything; `uv.lock` plus a pinned MetaDrive commit is.
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

RUN uv venv --python 3.10 /opt/venv \
    && uv sync --frozen --group sim \
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

# The container runs as the host's uid, which has no /etc/passwd entry and so no home. panda3d
# caches compiled shaders and matplotlib writes a font list; both need somewhere to go.
ENV HOME=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib \
    METADRIVE_PYTHON=/opt/venv/bin/python

CMD ["bash"]
