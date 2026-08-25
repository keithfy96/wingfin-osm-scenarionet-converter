Where the container looks for the AV3 model when `MODEL_DIR` is not set.

`compose.yaml` mounts `${MODEL_DIR:-./docker/model}` read-only at `/models` and points
`MODEL_CHECKPOINT` inside it. The fallback is this directory rather than nothing, so an unset
`MODEL_DIR` cannot make docker create a root-owned directory in `$HOME` — the same reason
`docker/rig/` exists.

**One file, and it is the big one:**

| file | size | where it comes from |
|---|---|---|
| `step_440000_trt_direct_full.ep` | 1.26 GB | copied by hand. `MODEL_DIR` points here |
| `model_dev.yml` | 4 KB | **tracked**, at `config/model_dev.yml`. `MODEL_CONFIG` defaults to `/work/config/model_dev.yml`, through the repo mount |

The yml pins the settings those weights were trained with, and it is **not optional and not
defaulted**: `av3_model.load_config` requires every field it reads and supplies none, deliberately
(a silently-defaulted `frame_stride_s` gives a model that runs on history spaced differently to
how it was trained, and scores anyway). It used to have to be copied here beside the `.ep`, and
that is exactly how it went missing: 4 KB next to 1.26 GB, "copy the model over" means the
checkpoint, and a directory without the yml fails at load with nothing in the message naming it.
Tracking it removes the choice.

**Prefer `MODEL_DIR` to putting anything here.** On this machine the checkpoint is at
`/home/keith/Desktop/work/wingfin/metadrive-complete/models/`, beside the repo worktree. Set that
in `.env`:

```
MODEL_DIR=/home/keith/Desktop/work/wingfin/metadrive-complete/models
```

The `.gitignore` here keeps whatever lands in this directory untracked — which matters more than
it does for `docker/rig/`, because a 1.26 GB checkpoint that belongs to the openpilot fork must
never reach a commit.
