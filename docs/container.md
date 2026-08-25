# Running the container

One image holding the whole environment: Python 3.10, the converter, and MetaDrive. It exists so
two machines' step-timing CSVs are comparable — same interpreter, same locked packages, same
MetaDrive commit — leaving the machine columns as the only difference.

**Run all of this from your own terminal, at the repo root.** Nothing here is typed inside the
container — each command starts one, does its job, and exits. The root rather than `scripts/`,
which is where the other stage scripts are normally run from, because the `docker compose` lines
below need to find `compose.yaml`; the scripts themselves work from either.

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

CSVs land in `workspaces/<ws>/reports/` on the host, owned by you, named
`step-timing-<label>-<stamp>.csv` — `<label>` is `STEP_TIMING_LABEL` or the hostname, and
`<stamp>` is when the run started, in **your** local time. The sweep prints the full path on
its last line; that is the one to read. Rows are written as they are measured, so a run you
stop partway still leaves the rows it got through.

**Rows are the sweep's alone**, and `--camera-rig` is the sweep's and `sensor-survey.sh`'s. `drive.sh` has neither.

## 3. The other tools

```bash
docker compose run --rm sim scripts/drive.sh mosque -- --render offscreen
docker compose run --rm sim scripts/sensor-survey.sh mosque -- --camera-rig rigs/cams.txt
docker compose run --rm sim bash    # the one command that does drop you inside; exit to leave
```

`drive.sh` drives **one** scenario end to end and reports on it — the `scenario 0` in its output
is a scenario index, not a row, and it has no `--camera-rig`. `sensor-survey.sh` records what the
sensors see.

## 3b. The AV3 model

**Same container.** `sim` carries the model stack (`torch`, `tensorrt`, `cupy`) beside MetaDrive —
`tools/av3_probe.py` builds an environment and runs a forward pass in one process, so there was
nothing for a second image to separate. `container-check.sh` is unchanged and does not touch it.

Set `MODEL_DIR` in `.env` first, to a directory holding **both** model files:

```bash
MODEL_DIR=/home/keith/Desktop/work/wingfin/metadrive-complete/models
```

```bash
docker compose run --rm sim scripts/av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20
```

Three things that are different in here, and one that is not:

- **The two files are mounted, not built in.** `compose.yaml` mounts `MODEL_DIR` read-only at
  `/models` and sets `MODEL_CHECKPOINT` and `MODEL_CONFIG` to paths inside it, so neither needs to
  be passed. `docker/model/README.md` has the detail; the short version is that
  **`model_dev.yml` is required and is not defaulted**, so a directory holding only the `.ep`
  fails at load with nothing in the message naming the missing file.
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
docker save wingfin-sim | gzip | ssh <rig> 'gunzip | docker load'
```

**A rig needs four things, and two of them come from neither the image nor `git`:**

| | where it comes from |
|---|---|
| the `sim` image | `docker compose build` there, or `docker save \| ssh` |
| this repo | `git` — `tools/`, `scripts/`, `rigs/`, the workspace |
| the two model files | **copied**, by hand |
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
docker save wing-sim-openpilot:prod | gzip | ssh <rig> 'gunzip | docker load'
```

5.5 GB over the wire against a clone that already carries the 309 MB — so build unless the rig
has no network. Tier 4 of `docs/running-a-test.md` has the states and what each one means.

**Or skip it for now.** `--backend stub` needs no bridge at all and drives the whole chain — it
is the right first thing to run on a new rig, and it is what separates "the container is wrong"
from "openpilot is not here".

And the model files:

```bash
# on the rig, wherever MODEL_DIR will point
scp <this-machine>:/…/metadrive-complete/models/step_440000_trt_direct_full.ep .   # 1.26 GB
scp <this-machine>:/…/metadrive-complete/models/model_dev.yml .                    # 4 KB
```

On this machine they sit at `metadrive-complete/models/`, beside the repo worktree, so the pair
travels together. **Copy both** — the 4 KB one is required and its absence is not diagnosed.

**Building on the rig is usually the better half of that choice**, and more so now than it was:
the image carries the model stack, so it is large, and `docker save | ssh` moves every byte
while a build pulls the same wheels from PyPI in parallel. Carry the image when the rig has no
network; build when it has.

## `.env`

```bash
WORKSPACE=mosque
RIG_DIR=/some/where                             # only for a rig spec kept outside the repo
MODEL_DIR=/some/where/models                    # the AV3 .ep and model_dev.yml, both
STEP_TIMING_LABEL=laptop                        # names the machine in the CSV
```

`BRIDGE_PORT`, `BRIDGE_IMAGE` and `BRIDGE_NAME` are `scripts/bridge.sh`'s, and all three default
to what every doc and error message names — set one only to run a second bridge beside a working
one.

`METADRIVE_PYTHON` must stay commented — it would override the container's interpreter.

`MODEL_CHECKPOINT` and `MODEL_CONFIG` are **not** set for a container run: `compose.yaml` points
them inside `MODEL_DIR` already. Setting either in `.env` overrides that, which is what a second
checkpoint is for.

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
| CSVs owned by root | use `step-timing-docker.sh`, not a bare `docker compose run` |
| `no such file: rigs/cams.txt` | the spec is in the repo; check it is not a `/rig/...` path from an older note |
| `could not select device driver` | `nvidia-ctk runtime configure --runtime=docker`, restart docker |
| the sweep dies on a missing interpreter | `METADRIVE_PYTHON` is uncommented in `.env` |
| the model fails at load, no file named | `model_dev.yml` is missing from `MODEL_DIR` — both files are needed, not just the `.ep` |
| `no checkpoint at /models/...` | `MODEL_DIR` is unset, so the empty `docker/model/` fallback got mounted |
| `KeyError: 'getpwuid(): uid not found'` | `/etc/passwd` is not mounted. The container runs as your uid and the image has no entry for it; `torch_tensorrt` reads the user's *name* at import. `compose.yaml` mounts it read-only |
| a drive loads the model when you did not ask | `MODEL_CHECKPOINT` is set for you in here. `docker compose run --rm -e MODEL_CHECKPOINT= sim …` to leave it out |
| `ConnectionRefusedError` on 5558 | the bridge. `cd scripts && ./bridge.sh status` — it says whether the image exists, whether the container is up, and whether anything is listening |
| `docker compose build` sends a huge context | `docker/openpilot/deps/` is in `.dockerignore` — 309 MB of vendored openpilot that the `sim` image has no use for. If it reappears, that line was lost |
| `Missing SConscript 'rednose/SConscript'` during `bridge.sh build` | the repo reached this machine by something that flattened symlinks. `git clone` it instead; `bridge.sh build` checks all ten before starting |
