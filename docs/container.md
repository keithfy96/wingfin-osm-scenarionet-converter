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
```

Everything after `--` goes to `step_timing.py`, exactly as for `step-timing.sh` — `rigs/cams.txt`
included, which is the same string inside the container and out because the spec is in the repo.
For one kept outside the repo, set `RIG_DIR` in `.env` and it is `/rig/<name>.txt` in there.

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

The image is 2.88 GB and 1.24 GiB compressed, so that transfer is measured in minutes, not
seconds. Building on the rig is usually quicker if it has network.

Either way the rig also needs this repo (for `tools/`, `scripts/` and the workspace) and the
camera-rig spec.

## `.env`

```bash
WORKSPACE=mosque
RIG_DIR=/some/where                             # only for a rig spec kept outside the repo
STEP_TIMING_LABEL=laptop                        # names the machine in the CSV
```

`METADRIVE_PYTHON` must stay commented — it would override the container's interpreter.

## Two things to know

**Check `gl_renderer` in the CSV names the GPU.** If it says `llvmpipe` or `Mesa`, the run was on
the CPU: about 4x too slow, and it does not fail while doing it.

**Row 7 needs a display**, so it is the one row that does not run in the container.

## If it breaks

| | |
|---|---|
| `gl_renderer` is `llvmpipe` | rebuild — the NVIDIA EGL manifest is missing |
| CSVs owned by root | use `step-timing-docker.sh`, not a bare `docker compose run` |
| `no such file: rigs/cams.txt` | the spec is in the repo; check it is not a `/rig/...` path from an older note |
| `could not select device driver` | `nvidia-ctk runtime configure --runtime=docker`, restart docker |
| the sweep dies on a missing interpreter | `METADRIVE_PYTHON` is uncommented in `.env` |
