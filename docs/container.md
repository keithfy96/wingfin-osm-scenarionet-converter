# Running the container

One image holding the whole environment: Python 3.10, the converter, and MetaDrive. It exists so
two machines' step-timing CSVs are comparable — same interpreter, same locked packages, same
MetaDrive commit — leaving the machine columns as the only difference.

**Run all of this from your own terminal.** Nothing here is typed inside the container — each
command starts one, does its job, and exits.

**`scripts/sim.sh` is the way in**, and the one to use rather than `docker compose run` typed out:
`compose.yaml` runs the container as `${DOCKER_UID:-1000}`, a shell does not export that, and a
bare `docker compose run` is therefore uid 1000 whoever you are. `sim.sh` reads it from `id -u`.
`./sim.sh id` should print your own name; if it does not, everything the container writes into the
mounted workspace will have the wrong owner and the model will fail to import. The `docker compose
build` and `docker compose config` lines below are fine as they are — they start nothing.

The stage scripts are normally run from inside `scripts/`, and `sim.sh` and the `-docker.sh`
wrappers work from either: they `cd` to the repo root themselves, which is where `compose.yaml`
is.

## 1. Test everything

```bash
./scripts/container-check.sh mosque
```

That is the whole thing, and it is what to run on a new rig. Nothing goes before it — it builds
the image itself. Four steps, stopping at the first failure:

| | |
|---|---|
| **build** | `docker compose build`. Minutes the first time, seconds after |
| **gpu** | opens an offscreen context and reads the renderer back. **Fails on `llvmpipe`** |
| **tests** | `uv run pytest -q` |
| **sweep** | the full step-timing sweep, every row at every rate, ending in a CSV |

Exits non-zero if any step fails. Takes about 5 min 40 s on this laptop after the first build.

One test is deselected by name and the run says so: the map-geometry issue in
`test_ego_route`, which fails on the real map and has nothing to do with the container.

**It does not exercise the AV3 model**, deliberately — its job is to prove the simulator and the
GPU, and a forward pass costs about a second each. Run `av3-probe.sh` (§3b) after it for that;
that is the check which says the model works in here.

Add the real cameras, or shorten it:

```bash
./scripts/container-check.sh mosque -- --camera-rig rigs/cams.txt
./scripts/container-check.sh mosque -- --max-steps 200
```

## 2. The sweep on its own

```bash
./scripts/step-timing-docker.sh mosque                                # rows 1-6, every rate
./scripts/step-timing-docker.sh mosque -- --rows 2,6                  # what the camera costs
./scripts/step-timing-docker.sh mosque -- --rows 5                    # one row on its own
./scripts/step-timing-docker.sh mosque -- --camera-rig rigs/cams.txt  # the real 7 cameras

# every row at each of several whole configurations, with the real cameras, into one CSV
./scripts/step-timing-docker.sh mosque -- --rate-sets scripts/rate-sets.csv \
    --camera-rig rigs/cams.txt
```

Everything after `--` goes to `step_timing.py`, exactly as for `step-timing.sh` — `rigs/cams.txt`
and `scripts/rate-sets.csv` included, which are the same strings inside the container and out
because both files are in the repo. For a rig spec kept outside the repo, set `RIG_DIR` in `.env`
and it is `/rig/<name>.txt` in there.

**`--rate-sets` is the way to run a batch in here.** The file is
`name,step_hz,decision_hz,physics_hz`, one whole configuration a row — `world tick / decision +
camera / physics` — and the sweep drives them one after another in **one process**, which is what
keeps them comparable: `prime` is paid once and every machine column is identical by construction
rather than by two runs happening to agree. One CSV comes out, with a `rate_set` column to pivot
on. Each set drives only the dataset written at its own `step_hz`, so
`uv run osm-scenario convert … --step-hz 100` has to have been run for a 100 Hz set to have
anything to drive. Full reference: `docs/step-timing-rows.md`.

**Nothing needs rebuilding when `tools/` or `scripts/` change.** The image copies in only
`pyproject.toml`, `uv.lock`, `README.md` and `src/`; everything else — `tools/`, `scripts/`,
`rigs/`, the workspaces — is live through the `.:/work` bind mount, and the editable install
points at `/work/src`. Only a dependency change needs `docker compose build`.

**But a pull can be a dependency change, and nothing tells you.** If `git pull` touched
`pyproject.toml`, `uv.lock` or `docker/Dockerfile`, the image is now behind the code and
**compose will not notice**: `docker compose run` reuses whatever holds the
`metadrive-wingfin-sim` tag and
never builds. Neither will a rebuild you ran *before* the pull — it rebuilds the old recipe out of
BuildKit's cache in seconds and looks exactly like a successful build.

That is not theoretical. A rig spent a morning on

```
result       FAILED: --model-checkpoint needs torch, which is not installed in /opt/venv/bin/python
```

against an image built before `--group gpu --group model` was added to `docker/Dockerfile`.
`./sim.sh` now says so in the first second instead — it reads the `wingfin.groups` label off the
image and compares it with the `uv sync` line — but the habit is what matters: **after a pull that
changes the lock or the Dockerfile, `docker compose build`.**

**Silence from that check is not a clean bill of health.** It says nothing about an image with no
`wingfin.groups` label at all, because the label was added *after* the groups were: an image built
between the two carries all three and no label to prove it. Reading that silence as "everything is
missing" is exactly what it used to do, and it told a working rig it was missing torch moments
before the same run loaded torch. Absent means unknown.

To ask the image directly, on any machine:

```bash
docker compose run --rm sim python3 -c "import torch, tensorrt; print(torch.__version__)"
```

**Not the image size.** `docker images` measures different things on different machines: the
classic overlay2 store prints one `SIZE` column (13.2 GB here), the containerd snapshotter prints
`CONTENT SIZE` (compressed blobs) and `DISK USAGE` (those plus unpacked snapshots). A number from
one machine proves nothing about the other's image, so read what is installed instead.

CSVs land in `workspaces/<ws>/reports/` on the host, owned by you, named
`step-timing-<label>-<stamp>.csv` — `<label>` is `STEP_TIMING_LABEL` or the hostname, and
`<stamp>` is when the run started, in **your** local time. The sweep prints the full path on
its last line; that is the one to read. Rows are written as they are measured, so a run you
stop partway still leaves the rows it got through.

**Rows are the sweep's alone**, and `--camera-rig` is the sweep's and `sensor-survey.sh`'s. `drive.sh` has neither.

## 3. The other tools

```bash
cd scripts
./sim.sh scripts/drive.sh mosque -- --render offscreen
./sim.sh scripts/sensor-survey.sh mosque -- --camera-rig rigs/cams.txt
./sim.sh bash    # the one command that does drop you inside; exit to leave
```

`drive.sh` drives **one** scenario end to end and reports on it — the `scenario 0` in its output
is a scenario index, not a row, and it has no `--camera-rig`. `sensor-survey.sh` records what the
sensors see.

## 3b. The AV3 model

**Same container.** `sim` carries the model stack (`torch`, `tensorrt`, `cupy`) beside MetaDrive —
`tools/av3_probe.py` builds an environment and runs a forward pass in one process, so there was
nothing for a second image to separate. `container-check.sh` is unchanged and does not touch it.

Set `MODEL_DIR` in `.env` first, to the directory holding the `.ep`:

```bash
MODEL_DIR=/home/keith/Desktop/work/wingfin/metadrive-complete/models
```

```bash
cd scripts
./sim.sh scripts/av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20
```

Three things that are different in here, and one that is not:

- **The checkpoint is mounted, not built in.** `compose.yaml` mounts `MODEL_DIR` read-only at
  `/models` and sets `MODEL_CHECKPOINT` inside it, so it needs no argument.
  `docker/model/README.md` has the detail. **The `model_dev.yml` beside it does not come from
  there**: it is tracked at `config/model_dev.yml` and reached through the repo's own `/work`
  mount, which is what `MODEL_CONFIG` defaults to. So `MODEL_DIR` holds one file, and the yml
  travels with `git`.
- **`METADRIVE_PYTHON` is already right.** On the host the model path needs
  `METADRIVE_PYTHON=../.venv/bin/python` in front of `drive.sh`, because that script defaults to
  the 3.8 checkout venv and torch 2.8 has no 3.8 wheel. The image sets it to `/opt/venv/bin/python`
  already, so **do not pass it in here.**
- **The openpilot bridge is a second, separate container** — ubuntu 20.04 / python 3.8, which is
  why it cannot be this one. `network_mode: host` is what lets this container reach it on
  `127.0.0.1:5558`, and `examples/openpilot_server.py` can sit on either side of the boundary for
  the same reason. It is built by `scripts/bridge.sh` out of `docker/openpilot/`, **not** by
  `docker compose build` — see tier 4 of `docs/running-a-test.md`, and note that
  `--bridge HOST:PORT` can point at one on another machine as a stopgap. That directory carries
  the openpilot fork itself, so building it needs only this repo.
- **`/etc/passwd` is mounted, and the model needs it.** The container runs as your uid so that
  files it writes are not root's, and the image has no `/etc/passwd` entry for that uid —
  `torch_tensorrt` reads the user's *name* at module scope (`dynamo/_defaults.py:64`), only to
  name a temp log directory, so `import torch_tensorrt` dies with `KeyError: 'getpwuid(): uid not
  found: 1000'`. `HOME=/tmp` in the image solves the home-directory half of that; this is the
  name half.
- **Not different: the answer.** `av3-probe.sh` must end `result  every checked conversion agrees`
  and exit 0 in here exactly as it does on the host. That is the check that says the container
  changed the environment and not the result.

**`--image-on-cuda` in the container is untested.** It needs the GL and the CUDA context on the
same card; on the host that is the PRIME offload, and in here EGL picks from the image's ICD
manifest instead. Plausible, unverified — measure before relying on it.

**Do not carry a forward-pass timing across machines.** The 947–1002 ms of Phase C.1 was measured
on this laptop's RTX 4050 at a 35 W cap, compute-bound with the clock at a third of its rating.
Re-measuring somewhere else is the point of the container, not something it lets you skip.

## 4. Onto the rig

Either build it there:

```bash
# on the rig, with the repo present
docker compose build
```

or carry the built image, which needs no network on the far end:

```bash
# on this machine, pushing to the rig over ssh
docker save metadrive-wingfin-sim | gzip | ssh <rig> 'gunzip | docker load'
```

**A rig needs four things, and two of them come from neither the image nor `git`:**

| | where it comes from |
|---|---|
| the `sim` image | `docker compose build` there, or `docker save \| ssh` |
| this repo | `git` — `tools/`, `scripts/`, `rigs/`, the workspace |
| the AV3 checkpoint | **copied**, by hand — 1.2 GB, the one thing `git` cannot carry |
| the **openpilot bridge image** | `scripts/bridge.sh build` there, or a saved copy |

The bridge is the one that gets missed, because `docker compose build` does not build or pull it
and no error mentions it until a drive is a minute in. It needs nothing from outside the repo now
— `docker/openpilot/` holds the Dockerfile, the bridge sources **and the openpilot fork itself**,
vendored at a pinned commit:

```bash
cd scripts
./bridge.sh status      # not built / not running / up
./bridge.sh build       # budget half an hour, and it needs only this repo
./bridge.sh start
```

**Or carry a built image**, for a rig with no network:

```bash
cd scripts && ./bridge.sh save /tmp/bridge-image.tar.gz   # or the one-pipe form:
docker save metadrive-wingfin-openpilot:prod | gzip | ssh <rig> 'gunzip | docker load'
```

5.5 GB over the wire against a clone that already carries the 309 MB — so build unless the rig
has no network. Tier 4 of `docs/running-a-test.md` has the states and what each one means.

**Or skip it for now.** `--backend stub` needs no bridge at all and drives the whole chain — it
is the right first thing to run on a new rig, and it is what separates "the container is wrong"
from "openpilot is not here".

And the checkpoint — **one file, not two**:

```bash
# on the rig, wherever MODEL_DIR will point
scp <this-machine>:/…/metadrive-complete/models/step_440000_trt_direct_full.ep .   # 1.26 GB
```

On this machine it sits at `metadrive-complete/models/`, beside the repo worktree.
`model_dev.yml` used to have to be copied beside it and no longer does — it is tracked at
`config/model_dev.yml`. That is worth knowing because of how it used to fail: the yml is 4 KB
next to a 1.2 GB checkpoint, "copy the model over" naturally means the `.ep`, and a directory
missing the yml fails at load with nothing in the message naming it.

**Building on the rig is usually the better half of that choice**, and more so now than it was:
the image carries the model stack, so it is large, and `docker save | ssh` moves every byte
while a build pulls the same wheels from PyPI in parallel. Carry the image when the rig has no
network; build when it has.

## `.env`

**On a fresh machine there are two keys to set, and one of them is optional.** Copy
`.env.example` to `.env` (it is gitignored) and edit:

```bash
WORKSPACE=mosque                                # the scripts also take it as an argument
MODEL_DIR=/abs/path/to/models                   # the directory holding the AV3 .ep
STEP_TIMING_LABEL=rig                           # optional; names the machine in the CSV
```

**`MODEL_DIR` is the only one that is not optional**, and only for what actually loads the model —
§3b's probe, and a tier-4 drive with the checkpoint. `compose.yaml` mounts it read-only at
`/models` and points `MODEL_CHECKPOINT` inside it. **The `.ep` alone**: `model_dev.yml` is tracked
at `config/model_dev.yml` and `MODEL_CONFIG` defaults there through the `/work` mount, so it is
not something this directory has to hold. Tier 5 of `docs/running-a-test.md` (`--backend stub`,
`./sim.sh --no-model`) needs none of it.

Four that do **not** need setting, and one that must not be:

- **`METADRIVE_PYTHON` must stay commented.** `_common.sh` sources this file with `set -a`, so an
  uncommented host path would override the container's own interpreter and the run dies on a
  missing venv.
- `MODEL_CHECKPOINT` and `MODEL_CONFIG` — `compose.yaml` points them inside `MODEL_DIR` already.
  Setting either here overrides that, which is what a second checkpoint is for.
- `RIG_DIR` — only for a camera-rig spec kept **outside** the repo. `rigs/av3.txt` and
  `rigs/cams.txt` are in the repo and the repo is mounted at `/work`, so `--camera-rig
  rigs/av3.txt` is the same string inside the container and out.
- `DOCKER_UID` / `DOCKER_GID` — deliberately not here. `sim.sh` reads them from `id -u` / `id -g`
  so they cannot go stale against the account actually running.
- `BRIDGE_PORT`, `BRIDGE_IMAGE`, `BRIDGE_NAME` are `scripts/bridge.sh`'s, and all three default to
  what every doc and error message names — set one only to run a second bridge beside a working
  one.

## Three things to know

**Check `gl_renderer` in the CSV names the GPU.** If it says `llvmpipe` or `Mesa`, the run was on
the CPU: about 4x too slow, and it does not fail while doing it. It is blank on any row that
renders nothing — row 6 — where `gl_max_texture` is the column to read instead: 32768 on the RTX
against 16384 on the iGPU or a software renderer.

**The `gpu` line the scripts print reads differently in here, and that is not a fault.** It says
`picked by EGL in the container, not by GPU=auto`. On the host the card is chosen by two GLX
environment variables; in the container panda3d loads EGL first and libglvnd picks the card from
the image's own ICD manifest, so `GPU=` has nothing to act on. The card that rendered is above
either way — the printed line is a label, those columns are the measurement.

**Row 7 needs a display**, so it is the one row that does not run in the container.

## If it breaks

| | |
|---|---|
| `gl_renderer` is `llvmpipe` | rebuild — the NVIDIA EGL manifest is missing |
| CSVs owned by root, or by a user that is not you | you ran `docker compose run` directly. `compose.yaml` takes the uid from `DOCKER_UID` and a shell does not export it, so it falls back to 1000. `scripts/sim.sh` (or `step-timing-docker.sh`) is what passes yours through — `./sim.sh id` should print your own name |
| `KeyError: 'getpwuid(): uid not found'` when the model loads | the same fault, one step further on: the container is running as a uid with no entry in the mounted `/etc/passwd`, and `torch_tensorrt` reads the user's *name* at import. Run it through `./sim.sh` |
| `no such file: rigs/cams.txt` | the spec is in the repo; check it is not a `/rig/...` path from an older note |
| `could not select device driver` | `nvidia-ctk runtime configure --runtime=docker`, restart docker |
| the sweep dies on a missing interpreter | `METADRIVE_PYTHON` is uncommented in `.env` |
| the model fails at load, no file named | the `model_dev.yml` it wants is missing. It is tracked at `config/model_dev.yml` and read through `/work`, so this means `MODEL_CONFIG` is set in `.env` and points somewhere else — unset it |
| `no checkpoint at /models/...` | `MODEL_DIR` is unset, so the empty `docker/model/` fallback got mounted |
| `KeyError: 'getpwuid(): uid not found'` | `/etc/passwd` is not mounted. The container runs as your uid and the image has no entry for it; `torch_tensorrt` reads the user's *name* at import. `compose.yaml` mounts it read-only |
| a drive loads the model when you did not ask | `MODEL_CHECKPOINT` is set for you in here. `./sim.sh --no-model …` leaves it out; emptying the variable in your own shell will not, because `${VAR:-default}` treats empty as unset |
| `ConnectionRefusedError` on 5558 | the bridge. `cd scripts && ./bridge.sh status` — it says whether the image exists, whether the container is up, and whether anything is listening |
| `docker compose build` sends a huge context | `docker/openpilot/deps/` is in `.dockerignore` — 309 MB of vendored openpilot that the `sim` image has no use for. If it reappears, that line was lost |
| `Missing SConscript 'rednose/SConscript'` during `bridge.sh build` | the repo reached this machine by something that flattened symlinks. `git clone` it instead; `bridge.sh build` checks all ten before starting |
