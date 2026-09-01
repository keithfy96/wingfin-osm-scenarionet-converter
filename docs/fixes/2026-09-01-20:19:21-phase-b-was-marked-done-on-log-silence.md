# Phase B was marked done on log silence, and is not done

**Status: the plumbing works. The picture is half looked at.** Keith caught the overclaim — the
answers describing Phase B did not match the claim that it was complete.

**Update 2026-09-02:** Keith ran the viewer and reported *"the route looks like the route i have"*
— the first real visual confirmation this stage has had, and it also exposed that the route only
ever drew because playback was looping. Items 3 and 4 are done; the screenshot, the bags, and the
by-eye check of the rviz config remain.

---

## What this folder is for

`docs/fixes/` holds work that is **known to be wrong or unfinished, with the fix written down and
not yet done**. One file per thing. It is not a session log, not a design doc, and not a place for
findings that are already fixed — those go to `docs/reference/`, and corrected mapping-algorithm
mistakes go to `docs/mapping-algo-changes/` under the rules in `CLAUDE.md` §B.

A file leaves this folder by being deleted when its fix lands.

---

## The overclaim

The Stage 10 plan's Phase B box was ticked, and `docs/reference/ros-bags.md` and
`docs/testing-ros.md` were written as though a bag had been visually verified. What actually
happened is narrower.

| what was claimed | what was actually done |
|---|---|
| "rviz2 shows the car, the route, the TF tree and the object boxes" | rviz2 **started**, and its log contained no errors |
| "the boxes sit on the road and move with the people" | never observed by anyone — this is the sentence a reader would rely on, and it is unearned |
| "0 warnings after the covariance fix" | true, and it measures warnings, not correctness |
| "all 11 topics play" | true — verified by the absence of `Ignoring a topic` |

**Reading a log for the absence of errors is not looking at a picture.** Every failure Phase B
exists to catch — a box in the wrong place, at the wrong size, a frame behind, under the road
instead of on it — produces a clean log. That is the whole reason the tier is not numeric, and
it is exactly the property the verification threw away.

### What *was* genuinely established

Worth keeping, because it is real and it is more than log silence:

- rviz2 opens through XWayland with hardware GL (`OpenGl version: 4.6`).
- `ros2 bag play` replays all 11 topics with no `Ignoring a topic` warnings, which needs both
  `vision_msgs` and the generated `wingfin_msgs` package.
- **rviz2 actually subscribed**, measured with `ros2 topic info` against a live viewer:

  | topic | subscribers |
  |---|---|
  | `/tf`, `/localization/odometry`, `/planning/route`, `/perception/objects` | 1 each |
  | `/perception/traffic_lights` | **0** |

  Four displays are connected to real publishers, the `Detection3DArray` plugin among them. That
  rules out a whole class of silent config faults — a mistyped topic, a plugin that failed to
  load, a QoS mismatch. It does **not** say a single pixel is in the right place.

---

## What needs to change

### 1. Untick the box, and say what is actually true

`docs/implementation-plan/stage-10-ros-bags-out-of-a-drive.md` — Phase B goes back to `[ ]`, with
the honest split: the viewer is built and connected, the visual check has not been made.

Same in `docs/reference/ros-bags.md` and `docs/testing-ros.md` tier 6: everything phrased as
"what it shows" becomes "what to check", because nobody has checked it.

### 2. A screenshot, which is the actual deliverable

The plan called for one and it was not produced. **A screenshot is the only artefact that makes
Phase B checkable by someone who was not there.** Without it the tier is a set of instructions
nobody has followed.

It belongs in `docs/reference/ros-bags.md`, and it needs to show the car, the route and boxes on
pedestrians at the same moment.

### 3. ~~`/perception/traffic_lights` has no display~~ — DONE 2026-09-02

Keith converted the lights in the same day, so this went live immediately.
`docker/ros-viewer/light_markers.py` republishes them as `visualization_msgs/MarkerArray` for
display only; the bag keeps the real typed topic. Subscribers on
`/perception/traffic_lights` went **0 → 1**.

### 4. ~~`scripts/ros-view.sh` mangles its passthrough arguments~~ — DONE 2026-09-01

Arguments now go in as arguments, after a `_` placeholder for `$0`, rather than interpolated into
the `bash -c` string.

### 5. Two of the three bags on disk have the invalid covariance

`bags/j1-001` and `bags/j1-container` were recorded before the covariance correction, so every
pose in them still claims `covariance[0] = -1` — "this publisher does not produce this quantity",
on data that is exact ground truth. `bags/j1-fixed` is right but predates the traffic lights and
the latched-QoS fix; **`bags/j1-lights` is the only current one.**

`docs/testing-ros.md` points at `bags/j1-001` in tiers 2 and 4. Either re-record it or the doc
sends a reader to a bag with a known defect in it.

### 6. The rviz config has never been checked by eye

`config/rviz/bag.rviz` was written by hand from the plugin's declared class names. **rviz2
ignores properties it does not recognise, without saying so** — so a wrong property name looks
identical to a correct one in every log, and the subscription check above cannot see it either.
Colours, box alpha, arrow scale and the orbit view's starting position are all unverified.

---

## How to test Phase B yourself

Two commands. The first is only needed if `bags/j1-lights` is not there.

```bash
METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh junction-1 -- --out bags/j1-lights
./scripts/ros-view.sh bags/j1-lights
```

Use `bags/j1-lights` — it is the only bag with the corrected covariances, the traffic lights and
the latched-route QoS all in it.

The first build of the viewer image pulls `osrf/ros:jazzy-desktop` and takes a few minutes; after
that it is cached and the viewer opens in about ten seconds. **It plays once**, 36 seconds, after
a 4-second head start that lets rviz2 subscribe before the first message goes out. Add `--loop`
to repeat. Close the window to end it.

### What you should see

- **A car** — an orange arrow — moving smoothly along a road, not jumping between positions.
- **Boxes**, up to 132 of them, standing on the road surface and **moving with** the people they
  label. They are pedestrians, cyclists and barriers.
- **A green line** running along the route the car drives.
- **Eight coloured spheres** at the junction with their state written above them: six run
  green → yellow → red together while the other two run opposite. **If any two of them are green
  at the same moment the signal plan is wrong**, and no numeric check anywhere can see it,
  because the bag does not carry which movements conflict.
- **Axes** at the car marked `base_link`, under a fixed `map`.

### What tells you something is wrong

| what you see | what it means |
|---|---|
| a completely empty screen | the clock. `use_sim_time` and `--clock` must both be on; a bag's stamps start at epoch zero, so a viewer on the wall clock discards everything and says nothing |
| boxes but no car, or the reverse | `/tf` — one of the two is not reaching the fixed frame |
| boxes **hovering above** or **sunk into** the road | a z-offset fault, which no numeric check looks for |
| boxes **trailing** the pedestrians by a constant lag | a stamp fault — the per-frame stamp is shared, and this is what would show it broken |
| the green route line missing | the route is one message at t=0 and the player's `--delay` is what lets rviz2 subscribe first. Raise it with `PLAY_DELAY=8` |
| the spheres missing | `light_markers.py` is not running, or the dataset has no lights (`mosque` has none) |
| boxes drawn but nothing under them | the Detection3DArray plugin is present but the geometry is wrong |

**`TF_OLD_DATA` / `Detected jump back in time` should not appear on a single pass.** Under
`--loop` it is one per lap and is the clock restarting, not a fault.

### The one thing worth doing while it is open

Set the view's **Target Frame** to `base_link` so the camera follows the car. Riding along is
where a lag or a z-offset becomes obvious; from a fixed viewpoint both look fine.

---

## Why this happened, so it does not again

The whole ladder in `docs/testing-ros.md` is automatable and was automated, and Phase B was
treated the same way — run it, read the output, call it green. **Tier 6 is the one rung that
cannot be passed by a process with no eyes**, and it was the one where "no errors in the log" was
accepted as the pass.

The rule that follows: a visual check is not complete without an artefact a person can look at
afterwards. That is what item 2 is for.
