# Running a test

How to check the Stage 9 model-at-the-wheel path works, cheapest first. Every tier is a
real check rather than a smoke test — **stop at whichever one answers your question.**

The thing being checked is the six conversions between the simulator and the AV3 model:
pixels, camera order, frame history, ego speed, route, and the sign of the waypoints coming
back. **Not one of them raises when it is wrong.** A mirrored route gives a model that
loads, runs, returns twenty plausible waypoints and drives smoothly into the oncoming
carriageway. That is the whole reason this ladder exists and why the cheap tiers are worth
running before the expensive ones.

Background and measurements: `docs/implementation-plan/stage-9-a-model-at-the-wheel.md`,
and the *"The model is at the wheel now"* section of `CLAUDE.md`.

## What each tier needs

The rungs are not equally cheap to *set up*, and the expensive prerequisite is at the top. Check
this before starting on a machine that has not run this before — a rig, or a fresh clone.

| tier | needs, beyond this repo |
|---|---|
| **0–1** | `uv sync`. 1b wants a converted dataset, and the repo has one |
| **2–3** | an NVIDIA GPU, the two model files (the `.ep` and `model_dev.yml`), and either `uv sync --group sim --group gpu --group model` on the host **or** the `sim` image |
| **4** | tier 3's, **plus the openpilot bridge image, running** — a second container, which `scripts/bridge.sh build` makes out of this repo alone |
| **5** | tier 2's. Deliberately no bridge: that is what it is for |

**On a rig, nothing needs `uv` on the host.** Tiers 2–5 all run inside the `sim` container, which
carries it — `./sim.sh <command>`, and `docs/container.md` §3b for the model probe. The `uv sync`
lines above are for a development machine driving from the host. Setting up a rig is
`docker compose build`, `./bridge.sh build`, and two keys in `.env` (`docs/container.md`).

**The bridge is the one that catches people out**, because `docker compose build` does not build
or pull it and no error mentions it until a drive is a minute in — at which point it says
`ConnectionRefusedError` from a component you were never told you needed.

It no longer needs anything you do not have. The openpilot fork is **vendored** at
`docker/openpilot/deps/openpilot`, 309 MB of tracked files, so `git clone` + `./bridge.sh build`
is the whole prerequisite: no fork to fetch, no SSH keys, no submodules, no LFS.

If you are setting up a new machine, **run tier 5 first** anyway: it exercises the whole chain,
needs no bridge, and takes seconds — so it separates "my container is wrong" from "the bridge is
not up" before you spend half an hour on a build.

---

## 0. The translation tests (~1 s)

```bash
uv run pytest tests/unit/test_av3_model.py tests/unit/test_camera_rig.py \
              tests/unit/test_openpilot_policy.py tests/unit/test_policy_client.py
```

**Expect `83 passed` in about half a second.**

**What it is doing.** Nothing here starts the simulator, loads the model or touches the GPU.
It checks the code in the middle — the part that turns what MetaDrive knows into the numbers
the model expects, and turns the model's answer back into something the bridge can steer to.

**Why that gets a tier of its own.** Get one of those translations backwards and nothing
breaks. The model still loads, still runs, still returns twenty sensible-looking waypoints —
and the car drives smoothly into the oncoming lane. You would find out fifteen minutes into a
drive, having spent the GPU time to get there. These tests find it in half a second.

**What is being checked:**

- **the picture** — the camera image is resized and colour-swapped exactly the way the model's
  own training code does it. Checked by running both and comparing every pixel.
- **the cameras** — six of them, in the order the model expects, and each one really points
  where its name says. A left and right camera swapped is otherwise invisible.
- **the memory** — the model is shown five frames, half a second apart. Checked that it still
  is at any decision rate.
- **left and right** — MetaDrive counts sideways distance as positive to the *left*; the model
  counts it positive to the *right*. Everywhere the two meet has to flip. The tests try a
  left-hand bend **and** a right-hand one, because flipping only half of it is the mistake
  that looks fine and steers into traffic.
- **the answer coming back** — the model's waypoints are already in the bridge's own
  convention, so this one must **not** flip. Pinned here so nobody "fixes" it later.
- **the settings** — every value comes from the model's own config file, and nothing is
  quietly given a default.

Several of these read files straight out of the openpilot fork and compare against them. So if
that code changes upstream, a test here fails — instead of the change quietly becoming a bad
drive nobody can explain.

**The full suite** is `uv run pytest`: **672 passed, 1 failed**, about three minutes. The one
failure is expected and nothing to do with this work —
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows`, where 3 of 396 routes turn
too sharply at a corner on two very short lanes. It was failing before any of this started;
`CLAUDE.md` records it under *"`ego_route` still turns over the gate on two 2 m clamped
lanes"*.

## 1. Do the cameras point where the rig file says? (~30 s)

**First: 6 cameras or 7?** Both — there are two rig files, describing different cars.

- **`rigs/av3.txt` — 6 cameras.** The rig the AV3 model was trained on. This is the one the
  model uses.
- **`rigs/cams.txt` — 7 cameras.** The older one, used for the speed benchmarks. The model never
  sees it.

### a. What the rig file says

```bash
uv run python tools/camera_rig.py rigs/av3.txt
```

Reads the file and says it back in English, one line per camera:

```
front_right  512x384  fov 70  mount x+0.47 y+0.18 z+1.39  H -53.7 P-10.0  aims 54 deg to the right, i.e. front-right
```

Taken apart:

```
front_right  512x384  fov 70  mount x+0.47 y+0.18 z+1.39  H -53.7 P-10.0  aims 54 deg to the right
  |            |        |       |                           |               |
  |            |        |       |                           |               `- in words
  |            |        |       |                           `- which way it faces
  |            |        |       `- where it is bolted to the car
  |            |        `- how wide an angle it sees
  |            `- picture size in pixels
  `- the camera's name
```

**`mount x y z` — where the camera is bolted to the car**, in metres:

| | means | sign |
|---|---|---|
| `x` | **sideways** | `+` right, `−` left |
| `y` | **forwards / back** | `+` forward, `−` behind |
| `z` | **height above the road** | always `+` |

So `x+0.47 y+0.18 z+1.39` is 47 cm to the right, 18 cm forward, 1.39 m up.

**`H` and `P` — which way it is aimed**, in degrees:

- **`H` is Heading — turned left or right.** `0` is straight ahead. **Positive turns left,
  negative turns right.** So `-53.7` is turned 53.7° to the right.
- **`P` is Pitch — tilted up or down.** `0` is level. **Positive tilts up, negative tilts
  down.** So `-10.0` is looking 10° down at the road.

**`fov 70`** is how wide an angle it sees — 70° across. The two centre cameras are narrower
(42° and 45°); the four corners are 70°.

**Two things that make the line hard to compare against the sentence, and both are real:**

**1. `H` and the sentence use opposite conventions.** `H -53.7` is negative while the sentence
says *"to the right"*. Not a contradiction: `H` is MetaDrive's number, where **+ is left**, and
the sentence is written in CARLA's convention, where **+ is right**. Same fact stated twice
through different systems — deliberately, because the two are worked out independently, so if
the conversion between them broke they would disagree and you would see it.

**2. `x` and `y` here are not the `x` and `y` in the rig file.** `rigs/av3.txt` uses CARLA's
naming (x forward, y right); the printed line uses MetaDrive's (x right, y forward). Same
camera, same spot, swapped labels:

```
in rigs/av3.txt:   x: 0.1844   y: 0.468      <- x is forward, y is right
printed:           x+0.47      y+0.18        <- x is right,   y is forward
```

**All six, drawn:**

```
                              FRONT
                                A
        front_left         front_middle         front_right
         H +53.7             H +0.0              H -53.7
        (54 deg left)        (ahead)            (54 deg right)
       x-0.47 y+0.18      x+0.00 y+0.22        x+0.47 y+0.18
              \                  |                    /
                \                |                  /
   LEFT <-----------------  T H E   C A R  -----------------> RIGHT
    (x-)        /                |                  \         (x+)
              /                  |                    \
        rear_left           rear_middle           rear_right
         H +116.9             H -180.0             H -116.9
       (117 deg left)        (behind)            (117 deg right)
      x-0.41 y-1.30        x+0.00 y-1.41        x+0.41 y-1.30
                                V
                               BACK
```

Read across: every left-hand camera has **negative x** (left side of the car) and **positive
H** (turned left); every right-hand one is the reverse. That mirror symmetry is the quickest
way to eyeball that nothing is swapped, and it is invisible in a list. The four corners are all
tilted `P -10.0`, the two centres `P -5.0`, and the rear three sit slightly higher — `z 1.46`
against `1.39`.

**The part to read is the end of the line.** A camera called `front_right` should aim
front-right. Go down the six lines and check each name against its sentence. You are looking for
one where the two disagree.

That is not hypothetical — run the same command on the other rig file and you will find one:

```
cam_back_left    H -125.0   aims 125 deg to the right, i.e. rear-right
cam_back_right   H +125.0   aims 125 deg to the left,  i.e. rear-left
```

A camera named `back_left` pointing rear-**right**. `rigs/cams.txt` disagrees with itself, which
is why `rigs/av3.txt` was generated from the model's own spec rather than mapped onto it.

### b. Does the simulator agree about which way "right" is?

```bash
uv run --group sim python tools/camera_rig.py --check-frame workspaces/junction-1/scenarionet-10hz
```

The flag goes **first**, and it takes a **dataset** — this command does not read the rig file.

**What is that dataset?** The converted map MetaDrive drives —
`workspaces/junction-1/scenarionet-10hz/` is three files, 792 KB: the junction-1 map with the
route called `test`, plus two small index files. It comes out of Stage 6 (`convert`), and
`-10hz` is the rate it was baked at.

**Why does a directions check need a map at all?** You cannot ask MetaDrive which way is forward
without starting MetaDrive, and MetaDrive will not start without a map to load. You need a car
sitting on a road before there is a car whose "forward" you can measure. Any dataset works — the
answer is the same on every map; this one is just the smallest lying around.

**What it is checking.** That when our code says *"put the camera on the right"*, MetaDrive puts
it on the right. That is the whole job.

**Why that needs checking.** The rig file describes cameras in words — *"on the right of the
car, pointing 54° right, tilted 10° down"*. MetaDrive does not take words, it takes numbers, and
someone had to write the code that turns one into the other. If that code has left and right
backwards, every camera ends up on the wrong side of the car and **nothing goes wrong**: you get
six sharp pictures of the wrong things, and the model quietly drives badly.

**What it prints.** Six lines — each one a thing it tried, and what happened.

```
ok   local +y is 1 m forward   ahead +1.000 m, right -0.000 m
```
I pushed a point 1 metre forward. It moved 1 metre forward and zero to the side. Correct.

```
ok   local +x is 1 m right     ahead +0.000 m, right +1.000 m
```
I pushed it 1 metre right. It moved 1 metre right and zero forward. Correct.

```
ok   H=+55 turns left          +55.00 deg from the car's heading
```
I turned it 55° left. It is now 55° left of the car. Correct.

```
ok   P=+10 tilts up            +10.00 deg from the car's own attitude (which is -0.11 deg)
```
I tilted it 10° up. It is now 10° above the car's own line. Correct.

Two more do the same in the opposite direction — turn right, tilt down.

**How you know it is right.** Compare what it asked for against what it got. Both are printed on
the same line.

- Asked for **1 m forward**, got **1.000 forward, 0.000 sideways**. Match.
- `ahead -1.000` would mean "forward" is going backwards.
- The `1.000` appearing under `right` instead of `ahead` would mean forward and sideways are
  swapped.
- Asked to turn **left**, got `+55.00`. A `-55.00` would mean it turned right.

You do not have to trust the word `ok` — the numbers are beside the request.

**Expect six `ok` lines and nothing else.** That means every camera in the rig file will land
where the file says.

**Why "the car's own attitude" on the last two.** The car sits slightly nose-down on its springs
— −0.11° above. Measured against the ground instead, a 10° tilt reads 9.89°, which looks like a
bug and is not one.

**What it cannot tell you:** whether the front camera is really showing road ahead. It checks
directions, never pictures. Four of the six cameras are tilted, so they rest entirely on the last
two lines — if "tilt up" were backwards they would point at the sky, and this is what says so.

## 2. The inputs the model will be handed (~40 s, no GPU pass)

```bash
cd scripts && ./av3-probe.sh junction-1 -- --no-model --step-hz 100 --decision-hz 20
```

**The two rate flags are not optional.** Without them the script picks the 10 Hz dataset, and
`rigs/av3.txt` says its cameras must be read at 20 Hz — so the rig refuses to load rather than
quietly handing the model frames at half the rate it expects. Nothing here resamples.

**What it does.** Replays a recorded drive on junction-1 and, at 39 moments spread across it,
builds exactly the inputs the model would be handed — the six pictures, the car's speed, the
road ahead — and checks each one against a source that already knows the answer. `--no-model`
means the checkpoint is never loaded, so **nothing is predicted and nothing steers**. This tier
checks the wiring, not the driver.

**Why that is worth a tier of its own.** None of these inputs raises when it is wrong. Hand the
model the six cameras in the wrong order, or the road mirrored left-for-right, and it loads,
runs, and returns twenty perfectly plausible waypoints — and then drives into oncoming traffic.
So they are checked here first, on a car whose real path is already recorded.

### What it prints, block by block

**`camera map`** — which of your rig's cameras fills each of the model's six input slots.

```
camera map   model slot        rig camera        aims
             [0] front_middle  front_middle      straight ahead
             [1] front_right   front_right       54 deg to the right, i.e. front-right
```

Left column is the slot the weights expect, middle is the camera being put in it, right is where
that camera actually points. **Read across each row and check all three agree.** Six rows, no
`-- MISSING --`, and no "in the rig and read by nothing" note underneath.

**`history`** — `5 frames 0.5 s apart (stride 10, ring 41 deep, 108.8 MB of uint8)`. The model
does not see one picture, it sees five spaced half a second apart; that is how it perceives
motion. The arithmetic is all on the line: decisions come 0.05 s apart, so a 0.5 s gap is every
**10th** one (`stride 10`), and holding 5 frames 10 apart needs `(5−1)×10+1 = 41` slots. **And no
`note:` line** — one appears when the spacing does not divide evenly, which is a case that never
stops a run and is flagged nowhere else.

**`drove 3516 of 3695 steps, 39 decision(s) sampled`** — the recorded car reached its destination
at step 3516 of a 3695-step budget. 95%, which is the normal ending for this route, not a fault.
39 rather than 40 because the 40th sample was scheduled for step 3600, past the end.

**`frames`** — mean and spread of pixel brightness per camera, read a few decisions in. **The
only automatic test is that the spread is above 1.0.** A camera whose buffer never fills renders
a flat grey frame and the model happily returns waypoints from it. That all six differ from each
other is itself the evidence they are six views rather than six copies.

**`ego state`** — the speed pair the model is fed, beside MetaDrive's own reading.

```
step   speed m/s   v_fwd norm   v_lat norm   v_fwd m/s   v_lat m/s
0         13.889       1.7168       0.0000      13.889       0.000
```

The model is not fed metres per second. It is fed the speed divided by a fixed number out of
`model_dev.yml` (`ego_velocity_scale: [8.09, 0.27]`) — so **1.7168 × 8.09 = 13.889**, which is
column 2. And 13.889 m/s is **exactly 50 km/h**, junction-1's posted limit, which the recorded
drive holds along the straight.

**All six printed rows being identical is not a stuck reading** — they are the first six of the
39, steps 0 to 460 out of 3516, which is the straight opening of the route at a constant speed.
The verdict line under them is computed over all 39.

**`navigation`** — the block worth understanding, and the one that needs the most unpacking.

**The problem it solves.** Two different things need to be told where the road goes: the
openpilot bridge (tier 4), which is told by `policy_client`'s `route` sensor, and the AV3 model,
which is told by its own navigation block. Two separate pieces of code, written at different
times, describing the same road. This puts them side by side.

**What "where the road goes" means here.** Not a description in words — **20 dots laid along the
road ahead, 2 metres apart**, at 0 m, 2 m, 4 m … 38 m. Each dot is two numbers: how far ahead,
and how far to the side. Dot 0 is where the car is; dot 19 is the last, 38 m out.

**Where the two disagree.** They measure "to the side" in **opposite directions** — the route
sensor counts **left** as positive, the model's block counts **right** as positive. Same dot,
same road, opposite words for it. Somebody had to write the code that flips one into the other,
and **if that flip is backwards nothing complains**: the model is told the road bends right when
it bends left, and drives accordingly.

```
    the road bends left                       dot 19, 38 m ahead
                                             *
                                         . '
                                     . '        and 3 m to the LEFT
                                 . '
                             . '
                         . '
                     . '
                 . '
             . '
         . '
     . '
  [CAR] dot 0

  route sensor says    left  = +3.0     (left is its positive direction)
  model block  says    right = -3.0     (right is its positive direction)

  flip the model's:    -(-3.0) = +3.0   -> same number. That is the whole check.
```

**The table.**

```
step   point   ahead sensor / model      left sensor / -model
0      19        38.000 /   38.000       -0.000 /   -0.000
```

Each column holds **two numbers separated by a slash — what the sensor said / what the model
said.** The `-` in `-model` means the model's has already been flipped for you. So that row
reads: *at step 0, dot 19 — sensor says 38.000 m ahead, model says 38.000 m ahead; sensor says
0.000 m left, model flipped says 0.000 m left.* The same number twice, in both columns. 38 m
because dot 19 is 19 × 2 m out; zero sideways because the car is on a straight at that moment.

The line above the table, `the sensor is (ahead, LEFT) m; the model's is (fwd, RIGHT)/H`, says
that in shorthand — plus `/H`, because the model is not fed metres at all. It is fed **fractions
of the 40 m window** (0.95 rather than 38 m), and the tool multiplies back by 40 so the two can
be compared.

**Why zero, and not "close to zero".** These are not two measurements of the world that could
differ by a bit. They are **two calculations from the same stored road**. Get the flip right and
they are identical to the last decimal; get it backwards and the sideways number comes out
**doubled** — 3 m becomes −3 m, a 6 m gap. There is no middle ground, so zero means right and
anything you can see means a sign is backwards.

**Why every printed row is zeros, and the line that saves the check.** On a straight road this
test proves nothing: sideways is 0, and flipping the sign of zero gives zero, so it passes
whether the flip is right or wrong. Every row printed in a `junction-1` run is from the straight
opening (steps 0–645) — the boring ones. The verdict is computed over all 780 dots, bends
included. Which is what this line is for:

```
24 of 39 sampled decisions have the far end of the route more than 0.8 m to one side
```

**24 of the 39 sampled moments were on a genuine bend**, where the far dot really was off to one
side and a backwards flip would show up as metres. That is what earns the zero. If it ever read
`0 of 39`, the `ok` above it would be worth nothing.


### How you verify it

Five things, all on screen:

1. six camera rows where slot, camera and aim agree;
2. the history arithmetic — 10 and 41 — and no `note:` line;
3. every camera's spread above 1.0;
4. **both `ok` lines, both reading 0.0000**;
5. the turning count above zero, `result  every checked conversion agrees`, and exit 0.

**What it does not check.** No model is loaded, so nothing here says the model predicts anything
sensible — that is tier 3. And nobody looks at the pictures: this confirms the cameras point
correctly and render *something*, never that the front camera is showing road.

## 3. The model predicts, while nothing steers (~4 min)

```bash
uv sync --group sim --group gpu --group model      # all three, or it removes the others
cd scripts && ./av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20
```

**Or in the container**, which is how this reaches a machine that is not this laptop. Set
`MODEL_DIR` in `.env` to a directory holding both the `.ep` and the `model_dev.yml` first; the
image already has all three dependency groups, so there is no `uv sync` step:

```bash
cd scripts
./sim.sh scripts/av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20
```

It must reach the same verdict as the host — that is what says the container changed the
environment and not the answer. `docs/container.md` §3b has the rest.

**The load prints a traceback and it is not a failure.** `torch_tensorrt.load` tries three
ways of opening the checkpoint and logs the first two failing before the third works: the
`.pt2` loader (*"must be a buffer or a file ending in .pt2"*), then `torch.jit.load`
(*"PytorchStreamReader failed locating file constants.pkl"*, with a full stack trace). The line
that says it worked is the one after them, `loaded  20 waypoints x 8`. The warnings above are
optional extras this checkpoint does not use — `torchvision`, `modelopt`, TensorRT-LLM — and
the `cuda.cudart` `FutureWarning`, which is a version pin we set on purpose.

The ego is replayed from the tape, so **the drive is the tape whatever the model says**.
This tier adds: per-camera frame statistics, the forward-pass median (~1 s), the predicted
waypoints against where the recorded car really went, and the **nav sweep** — the controlled
experiment that settles conversion 6 by holding the pictures and the ego state fixed and
replacing only the navigation with a synthetic 30 m arc.

**Read the nav sweep, not the waypoint table, for the sign.** On `junction-1` the
drive-based statistic points the *wrong way* — 27% sign agreement — because the model
carries a standing **+1.6 m rightward bias** on that map, and a constant bias reads exactly
like a mirror at any sample size. The sweep gives right-hand bend **+2.172 m** against
left-hand **+1.062 m**, so +y is RIGHT and nothing flips.

`mosque` corroborates the *mechanism* rather than just repeating the answer: its bias is
smaller (+1.041 m) and it has more corners, and there the drive statistic recovers the right
answer by itself (72% agreement).

Ends with `result  every checked conversion agrees` and exits 0.

## 4. It drives (~15 min a route)

### First: is the bridge there?

**This tier needs a second container, and everything to build it is in this repo.**
`docker/openpilot/` holds the Dockerfile, the bridge server, and the openpilot fork itself —
vendored at a pinned commit rather than fetched, so no part of this needs access to the private
zapetaai org. `scripts/bridge.sh` builds, starts and checks it.


### If there is no image: build one

```bash
cd scripts
./bridge.sh build
./bridge.sh start
```

That is the whole thing on a machine that has never had it. **Budget half an hour** — the apt +
pyenv + poetry base dominates and is the part not timed here; the scons compile of cereal, boardd
and the two MPC libraries follows it, and the acados solver prebuild took under 5 s. The image is
**5.5 GB**. A later build that touches only `docker/openpilot/bridge/` takes seconds, because the
Dockerfile copies that in last on purpose.

`build` prints what it is building from before it starts — the vendored tree's size and commit,
and a check that all ten of the fork's symlinks are present:

```
== fork checkout
  vendored   309M at c767ace88
  symlinks   all 10 present
```

**That symlink check is not decoration.** `rednose`, `laika`, `tinygrad`, `selfdrive/hardware` and
six paths under `third_party` are mode 120000 in git. A transport that flattens them — an rsync
without `-l`, a zip, git with `core.symlinks=false` — makes scons die on
`Missing SConscript 'rednose/SConscript'`, which reads like a broken Dockerfile and is not. Get
the repo here by `git clone` and it cannot happen.


### What is actually running: three processes

```
  the drive                 the translator             the controller
  MetaDrive + AV3 model  →  openpilot_server.py     →  bridge container
  (this repo)               :8642, HTTP in front       :5558, raw TCP
```

- **The controller** is openpilot itself — Ubuntu 20.04 / Python 3.8. Give it a path plus your
  speed and steering angle, it returns steer/throttle/brake. It can never share the `sim`
  container because torch has no 3.8 wheel.
- **The translator** converts between the two protocols and mirrors every sideways quantity in
  both directions. Only the first hop is HTTP; the bridge hop is a raw socket.
- **The drive** renders six cameras, asks the model for twenty waypoints, sends them, applies the
  pedals — once every 0.05 s.

**All three are containers, and `network_mode: host` is why they still find each other on
`127.0.0.1`.** Nothing here runs on the host, and nothing needs installing there — not even `uv`.

### The commands

Two terminals, both in `scripts/`.

```bash
cd scripts
./sim.sh python3 examples/openpilot_server.py --backend bridge --longitudinal table --port 8642
```

```bash
cd scripts
./sim.sh scripts/drive.sh junction-1 -- \
    --agent-policy remote --policy-url http://127.0.0.1:8642 \
    --sensors imu,route --step-hz 100 --decision-hz 20 --render offscreen
```

**`sim.sh` is not decoration — it is the only thing that runs the container as you.**
`compose.yaml` takes the uid from `DOCKER_UID`, a shell does not export that, and a bare
`docker compose run` therefore runs as uid 1000 whoever you are. On a machine where you are not
1000 that means reports written into the workspace with the wrong owner, and `import
torch_tensorrt` dying on `KeyError: 'getpwuid(): uid not found'` the moment a checkpoint loads.
`./sim.sh id` should print your own name.

Three things it settles that a host run would not:

- **the interpreter** — `python3` in there *is* `/opt/venv/bin/python3` (3.10.21), and
  `METADRIVE_PYTHON` is already pointed at it, so neither `uv run` nor a `METADRIVE_PYTHON=`
  prefix belongs on these lines;
- **the checkpoint** — `compose.yaml` sets `MODEL_CHECKPOINT` to the mounted `/models` path and
  `drive.py` reads it, so there is no `--model-checkpoint` to pass. Set `MODEL_DIR` in `.env`
  first (§3b of `docs/container.md`). It implies `--camera-rig rigs/av3.txt`, the rig the weights
  were trained on;
- **`--render offscreen`, never `3D`** — there is no display.

**A drive with no checkpoint is not the model driving.** `./sim.sh --no-model` leaves it out, and
the waypoints then come from the recorded route at constant speed — the `route_gt` path, which is
a controller test by construction and the thing Phase 0 measured. The run looks the same.

**On that path, `--longitudinal table` does not finish `junction-1`, and that is expected.**
Measured through the container, both `--no-model`:

| `--longitudinal` | result |
|---|---|
| `table` | 7751 steps, `arrive_dest=False`, completion **0.810**, `out_of_road` at lateral −4.00 m |
| `accel` | 8668 steps, `arrive_dest=True`, completion **0.950** |

Same 0.815 that the *What to expect* table below records for `route_gt` + `table`, so it is the
pedal map and not your setup: those tables are a CARLA Town10HD calibration whose zero crossing is
the CARLA Tesla's −1.582 m/s² drag, against MetaDrive's −0.364, so most requests to slow gently
come back as throttle. **Use `--longitudinal accel` if you want the no-model drive to arrive**;
`table` is what to use with the model, which is what the numbers below were taken on.

### When it says `Connection refused`

```
policy returned HTTP 500 for /episode:
  {"error": "cannot reach the bridge at 127.0.0.1:5558 - ConnectionRefusedError"}
```

Refused means the path worked and **nothing is listening**. Two causes produce it identically:
the bridge is not running, or `network_mode: host` is not in effect so the container's
`127.0.0.1` is its own loopback.

**`cd scripts && ./bridge.sh status` first** — it settles the common case in a second, because it
reports the container state and whether anything is listening on 5558 as two separate lines. If it
says the bridge is up *and* something is listening, and a drive still gets refused, then it is the
second cause and this separates them:

```bash
# terminal A, on the machine itself
python3 -c "import socket;s=socket.socket();s.setsockopt(1,2,1);s.bind(('127.0.0.1',5558));s.listen();print('listening');s.accept()"

# terminal B
cd scripts && ./sim.sh python3 -c "import socket;s=socket.socket();s.settimeout(2);print(s.connect_ex(('127.0.0.1',5558)))"
```

`0` → host networking is fine and the bridge is simply absent. `111` → the namespaces are
separate, which is a different problem with a different fix.

The server also says so at startup now, on the line under `backend` — so you find out in a second
rather than after a terrain build. It is a **warning**, not a refusal: starting the server before
the bridge is a legitimate order to do it in.

**And `--bridge HOST:PORT` can point at a bridge on another machine**, which `network_mode: host`
makes work. A stopgap with a real cost — the round trip is 3.5–3.8 ms on loopback and a LAN hop
adds to every decision — not the arrangement.

### What to expect

**Read the speed, not the sign of `accel_cmd`.** What Phase 0 diagnosed was a car crawling
at 4.19 m/s under a 36 km/h cruise, because `route_gt`'s constant-speed path carried no
speed *intent*. With the model in:

| `junction-1`, real bridge, `--longitudinal table` | `route_gt` trajectory | the model |
|---|---|---|
| mean `v_ego` | 4.19 m/s | **8.92** (max 13.89, target 10) |
| median `accel_cmd` | −0.30 m/s² | −0.504 |
| completion | 0.815 | 0.163, `out_of_road` |

The median request going *more* negative is not a contradiction: a car at its target speed
correctly asks to hold, and that reads negative. What ends the drive is the lateral, which
is a domain-gap reading — four of the six cameras are 105.4° fisheyes standing in as
rectilinear at wing-sim's own unwarped 70°, on a Kuala Lumpur OSM extract rather than
Town10HD.

**A quarter of an hour is the forward pass, not a fault.** About a second each (Phase C.1
measured 947–1002 ms), one per decision, and a full-length `junction-1` route at
`--decision-hz 20` is 758 of them. `env.step` is the tick, so a slow policy makes a slow
drive and never a wrong one.

## 5. The control — that the wire did not regress (~1 min)

**On a machine that has never run this, do tier 5 before tier 4.** `--backend stub` is a real
socket speaking the real protocol with a pure-pursuit controller behind it — **no bridge, no
openpilot, nothing to install** — so it exercises the whole chain in seconds and tells you
whether a tier-4 failure is your setup or a missing bridge. That is the question a
`Connection refused` on its own cannot answer.

Two terminals, both in `scripts/`, and neither needs anything installed on the host:

```bash
cd scripts
./sim.sh python3 examples/openpilot_server.py --backend stub --port 8643
```

```bash
cd scripts
./sim.sh --no-model scripts/drive.sh junction-1 -- \
    --agent-policy remote --policy-url http://127.0.0.1:8643 \
    --sensors imu,route --step-hz 100 --decision-hz 20 --render offscreen
```

Expect **3788 steps, `arrive_dest=True`, completion 0.950** — unchanged from before any of this
landed, so a regression in the wire stays distinguishable from a regression in the model.
Measured in the container at **41 s** wall-clock, `result  OK`.

**`--no-model` is not optional here.** `compose.yaml` sets `MODEL_CHECKPOINT` and `drive.py` reads
it, so without it the drive loads the model and takes a quarter of an hour instead of a minute.
Emptying the variable in your own shell will not do it — `${VAR:-default}` treats empty as unset —
so it has to be passed on the run, which is all the flag does.

**`--longitudinal table` is refused here**, by name: the stub answers in pedals and carries no
`accel_cmd`, so there is nothing for a pedal map to convert. Leave the flag off.

`--waypoints derive` puts the bridge back on the pre-C.2 path (`waypoints_from_route`), so
every measurement taken before the model existed stays reproducible. Against `--backend
stub` the two `--waypoints` modes are **identical**, and that is the control rather than the
flag failing: `StubBridge.control` is pure pursuit over `msg["waypoints"]` and never reads
`modelv2`. Only the real bridge branches on it.

---

## If a tier fails

- **Tier 0 or 1** — a conversion or the rig. The failing assertion names which.
- **Tier 2's `navigation` block disagrees in metres** — that is the left-right mirror, not
  rounding. Everything sideways flips **together**: `y`, `sin θ`, `yaw`, `yaw_rate`, `v_y` and
  curvature. Everything forward does not: `x`, `cos θ`, `v_x`, `a_x`. Half of it right is the
  failure that steers into oncoming traffic.
- **Tier 2's `ego state` disagrees** — the speed scale. The two numbers the model is fed are
  the speed divided by `ego_velocity_scale` in `model_dev.yml`; check they multiply back.
- **Tier 3 refuses to load** — check `uv sync --group sim --group gpu --group model` named
  all three groups. `uv sync --group model` alone *removes* `sim` and `gpu`.
- **`cudaErrorUnknown(999)` at env construction** — the GL context and CUDA landed on
  different GPUs. `scripts/_common.sh:exec_with_gpu` sets the PRIME variables, so run
  through the scripts rather than a bare `uv run python tools/...`.
- **A traceback while the model loads is not a failure** — see tier 3. Look for
  `loaded  20 waypoints x 8`; if it is there, the load worked.
- **Tier 4 says `Connection refused` on 5558** — the bridge. `cd scripts && ./bridge.sh status`
  answers it in one line: not built, not running, or up-with-a-dead-server. On a machine that has
  never had the image, it does not exist. Run tier 5 to confirm everything else is fine.
- **Tier 4's `start` says the container name is in use** — the bridge is already running from an
  earlier session, which `./bridge.sh status` says plainly. Skip both commands.

---

## What is still not checked

**Nobody has looked at the pictures.** Every check above is numeric. Tier 1 proves the simulator
agrees which way is which, and tier 3 proves the six cameras differ from one another — but no
command in this repo writes the camera frames to disk, so *"is the front camera really showing
the road ahead"* has never been confirmed by eye. A `--save-frames` option on `av3-probe` would
settle it in one run.
