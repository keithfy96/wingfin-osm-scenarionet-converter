The escape hatch for a camera-rig spec kept **outside** the repo.

The specs this project uses are in `rigs/`, which the container already sees — the repo is mounted
at `/work` and worked from, so `--camera-rig rigs/cams.txt` needs no second mount and is written
the same way inside and out. Prefer that.

This exists for a spec that cannot go in the repo. `compose.yaml` mounts `${RIG_DIR:-./docker/rig}`
at `/rig` read-only, so setting `RIG_DIR` in `.env` makes that directory `--camera-rig
/rig/<name>.txt` in the container. The fallback is this directory rather than nothing, so an unset
`RIG_DIR` cannot make docker create a root-owned directory in `$HOME`; the `.gitignore` here keeps
whatever lands in it untracked, which is also why a spec that *should* travel to another machine
belongs in `rigs/` instead.

The spec format is the CARLA-shaped one `tools/camera_rig.py` reads — its module docstring is the
reference, and `rigs/README.md` is the short version.
