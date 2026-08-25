Where the container looks for the AV3 model when `MODEL_DIR` is not set.

`compose.yaml` mounts `${MODEL_DIR:-./docker/model}` read-only at `/models` and points
`MODEL_CHECKPOINT` and `MODEL_CONFIG` inside it. The fallback is this directory rather than
nothing, so an unset `MODEL_DIR` cannot make docker create a root-owned directory in `$HOME` —
the same reason `docker/rig/` exists.

**Two files are needed, not one.** Both must be in whichever directory `MODEL_DIR` names:

| file | size | what it is |
|---|---|---|
| `step_440000_trt_direct_full.ep` | 1.26 GB | the checkpoint — a `pt2` archive holding a serialized TensorRT engine |
| `model_dev.yml` | 4 KB | the settings those weights were trained with |

The yml is **not optional and not defaulted**: `av3_model.load_config` requires every field it
reads and supplies none, deliberately (a silently-defaulted `frame_stride_s` gives a model that
runs on history spaced differently to how it was trained and scores anyway). A directory holding
only the `.ep` fails at load, and nothing in the message points at the missing file.

**Prefer `MODEL_DIR` to putting anything here.** On this machine both files are at
`/home/keith/Desktop/work/wingfin/metadrive-complete/models/`, which sits beside the repo
worktree so the pair travels to another machine together. Set that in `.env`:

```
MODEL_DIR=/home/keith/Desktop/work/wingfin/metadrive-complete/models
```

The `.gitignore` here keeps whatever lands in this directory untracked — which matters more than
it does for `docker/rig/`, because a 1.26 GB checkpoint that belongs to the openpilot fork must
never reach a commit.
