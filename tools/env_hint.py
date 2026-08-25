"""How to install a missing dependency group, phrased for where the run actually is.

Every refusal in `tools/` that names a missing package used to end in the same two lines:

    uv sync --group sim --group gpu --group model
    and point METADRIVE_PYTHON at this repo's .venv.

Both are true on a development machine and both are wrong inside the `sim` container, where
`python3` already **is** `/opt/venv/bin/python3`, `METADRIVE_PYTHON` already points at it, and
there is no repo `.venv` to point anything at. Worse, `uv sync` in there would patch the image's
own venv and lose it on the next `--rm`, so following the advice appears to work once and then
stops.

That is not a hypothetical. A rig with no `uv` on it -- and no way to install one -- read exactly
that sentence out of a torch refusal and went looking for `uv`. The cause was an image built
before `--group gpu --group model` was added to `docker/Dockerfile`, and the fix was
`docker compose build`, which the message never mentioned.

`/.dockerenv` is the same test `scripts/_common.sh:170`, `scripts/container-check.sh:47` and
`tools/step_timing.py:358` already use, so this introduces no new convention. Stdlib only, and
parses on 3.8: `drive.py` imports it and runs on MetaDrive's interpreter.
"""

import os


def in_container():
    """True inside the `sim` image. See the module docstring for why this file is the test."""
    return os.path.exists("/.dockerenv")


def install_hint(groups):
    """The tail of a refusal: how to get `groups` installed from here.

    `groups` is the dependency-group names as `pyproject.toml` spells them, in the order
    `docker/Dockerfile` installs them -- ("sim", "gpu", "model").
    """
    if in_container():
        return (
            "This is the `sim` container, and its /opt/venv was built without "
            + ("that group" if len(groups) == 1 else "those groups")
            + " -- the image predates them. Rebuild it on the machine holding the repo:\n"
            "  docker compose build\n"
            "`docker compose run` never rebuilds on its own, and a build made before the pull "
            "that changed docker/Dockerfile rebuilds the old recipe from cache. `uv sync` in "
            "here would patch the image's venv and lose it on the next run."
        )
    return (
        "  uv sync " + " ".join("--group " + name for name in groups) + "\n"
        "and run with that environment's interpreter (METADRIVE_PYTHON for scripts/drive.sh)."
    )
