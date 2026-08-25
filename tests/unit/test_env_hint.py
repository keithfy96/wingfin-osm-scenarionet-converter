"""`env_hint.install_hint`, which decides whether a refusal names `uv sync` or `docker compose`.

This exists because the wrong half of that choice cost a rig a morning. `drive.py` refused a
`--model-checkpoint` drive with

    result       FAILED: --model-checkpoint needs torch, ... Run:
      uv sync --group sim --group gpu --group model
    and point METADRIVE_PYTHON at this repo's .venv.

read inside the `sim` container on a machine with no `uv` on it and no way to install one. Both
halves of that advice are wrong in there: `python3` already **is** `/opt/venv/bin/python3`, there
is no repo `.venv`, and a `uv sync` would patch the image's venv and lose it on the next `--rm`.
The real fix was `docker compose build`, which the message never mentioned.

So what is pinned here is the property that mattered: **the container branch must never send
anyone to `uv`,** and the host branch must still say what it always said.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import env_hint  # noqa: E402

GROUPS = ("sim", "gpu", "model")


@pytest.fixture
def in_container(monkeypatch):
    """Make `/.dockerenv` look present, whichever side of it the test is actually running on."""
    monkeypatch.setattr(env_hint.os.path, "exists", lambda path: path == "/.dockerenv")


@pytest.fixture
def on_host(monkeypatch):
    monkeypatch.setattr(env_hint.os.path, "exists", lambda path: False)


def test_container_offers_no_command_but_the_rebuild(in_container):
    """The whole point: nothing here may read as "go and run uv".

    The text is allowed to *mention* `uv sync` -- and does, to say why it is the wrong move in
    here, because `uv` does exist in the image and someone will otherwise reach for it. What it
    must not do is offer it. Commands are the indented lines, so that is what is checked, rather
    than the substring: a rig with no `uv` on it must find exactly one thing to type.
    """
    hint = env_hint.install_hint(GROUPS)
    commands = [line.strip() for line in hint.splitlines() if line.startswith("  ")]
    assert commands == ["docker compose build"]
    assert "METADRIVE_PYTHON" not in hint


def test_container_says_why_a_rebuild_is_needed_rather_than_a_sync(in_container):
    """A `uv sync` in the container appears to work once, then vanishes with the container."""
    hint = env_hint.install_hint(GROUPS)
    assert "never rebuilds" in hint
    assert "lose it on the next run" in hint


def test_host_keeps_the_advice_that_is_right_on_a_development_machine(on_host):
    hint = env_hint.install_hint(GROUPS)
    assert "uv sync --group sim --group gpu --group model" in hint
    assert "METADRIVE_PYTHON" in hint
    assert "docker compose" not in hint


def test_host_names_only_the_groups_it_was_given(on_host):
    assert "uv sync --group model\n" in env_hint.install_hint(("model",))


def test_container_agrees_with_itself_about_singular_and_plural(in_container):
    assert "that group" in env_hint.install_hint(("model",))
    assert "those groups" in env_hint.install_hint(GROUPS)


def test_the_test_is_the_file_the_rest_of_the_repo_already_uses():
    """`_common.sh:170`, `container-check.sh:47` and `step_timing.py:358` all read /.dockerenv.

    A second convention for "am I in the container" is how the two drift apart.
    """
    import inspect

    assert "/.dockerenv" in inspect.getsource(env_hint.in_container)
