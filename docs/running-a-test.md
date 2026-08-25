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

## 1. The mount is measured, not guessed (~20 s)

```bash
uv run python tools/camera_rig.py rigs/av3.txt --check-frame
```

Prints where each of the six cameras sits and **where it aims in words** — front_middle
straight ahead, front_right 54° right, rear_right 117° right, and so on — so a swapped pair
is readable rather than a number to be trusted. Every name should agree with its aim; that
they do is a property of `rigs/av3.txt` being generated from wing-sim's own spec, where the
`y` and `yaw` columns cross-check each other.

`--check-frame` probes MetaDrive's own `NodePath` for `+y`, `+x`, heading and pitch. That
is how the pitch sign was settled rather than assumed — and it has to be read against the
**car's own attitude**, because a car under throttle sits nose-up on its suspension (read
against the world the same probe returns 9.89° for a 10° mount).

## 2. The conversions, without loading the model (~40 s, no GPU pass)

```bash
cd scripts && ./av3-probe.sh junction-1 -- --no-model
```

Checks conversions 2, 4 and 5 in seconds. The lines to read are the two `ok` verdicts: the
ego-state pair against raw speed, and the navigation block against `policy_client`'s own
`route` sensor **over the whole 20-point window**.

Those are two independent projections of the same `PointLane` by different code, one of
them mirrored — so **a metre of disagreement there is a sign error, not rounding**.
Measured 0.0000 m over 320 points on `junction-1` and 460 on `mosque`.

## 3. The model predicts, while nothing steers (~4 min)

```bash
uv sync --group sim --group gpu --group model      # all three, or it removes the others
cd scripts && ./av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20
```

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

Three terminals. The bridge container first, if it is not already running:

```bash
docker build -t wing-sim-openpilot:prod \
  -f /home/keith/Desktop/work/wingfin/wing-sim/docker/Dockerfile.openpilot \
  /home/keith/Desktop/work/wingfin/wingfin-openpilot-temp/openpilot
docker run -d --name openpilot-bridge --network host \
  -e SIMULATION=1 -e NOBOARD=1 -e SKIP_FW_QUERY=1 -e "FINGERPRINT=TESLA MODEL 3" \
  -e OPENPILOT_TRAJECTORY_TYPE=0 -e BRIDGE_PORT=5558 \
  -e PYTHONPATH=/opt/bridge:/opt/openpilot:/opt/project/common \
  -w /opt/project wing-sim-openpilot:prod python3 -m zapeta.server
```

Then the translating server, and the drive:

```bash
uv run python examples/openpilot_server.py --backend bridge --longitudinal table --port 8642
```

```bash
cd scripts && METADRIVE_PYTHON=../.venv/bin/python ./drive.sh junction-1 -- \
    --agent-policy remote --policy-url http://127.0.0.1:8642 \
    --model-checkpoint /home/keith/Desktop/work/wingfin/wingfin-openpilot-temp/assets/models/step_440000_trt_direct_full.ep \
    --sensors imu,route --step-hz 100 --decision-hz 20 --render offscreen
```

`METADRIVE_PYTHON` matters — `drive.sh` defaults to the 3.8 checkout venv, and torch 2.8 has
no 3.8 wheel (nor needs one, MetaDrive itself running on 3.10). `--model-checkpoint` implies
`--camera-rig rigs/av3.txt`, which is the rig the weights were trained on.

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

```bash
uv run python examples/openpilot_server.py --backend stub --port 8643
```

Then the same `drive.sh` line **without** `--model-checkpoint`, pointed at port 8643. Expect
**3788 steps, `arrive_dest=True`, completion 0.950** — unchanged from before any of this
landed, so a regression in the wire stays distinguishable from a regression in the model.

`--waypoints derive` puts the bridge back on the pre-C.2 path (`waypoints_from_route`), so
every measurement taken before the model existed stays reproducible. Against `--backend
stub` the two `--waypoints` modes are **identical**, and that is the control rather than the
flag failing: `StubBridge.control` is pure pursuit over `msg["waypoints"]` and never reads
`modelv2`. Only the real bridge branches on it.

---

## If a tier fails

- **Tier 0 or 1** — a conversion or the rig. The failing assertion names which.
- **Tier 2** disagrees in metres — a sign, almost certainly conversion 5's mirror. `y`,
  `sin θ`, `yaw`, `yaw_rate`, `v_y` and curvature negate **together**; `x`, `cos θ`, `v_x`
  and `a_x` do not. Half of it right is the failure that steers into oncoming traffic.
- **Tier 3 refuses to load** — check `uv sync --group sim --group gpu --group model` named
  all three groups. `uv sync --group model` alone *removes* `sim` and `gpu`.
- **`cudaErrorUnknown(999)` at env construction** — the GL context and CUDA landed on
  different GPUs. `scripts/_common.sh:exec_with_gpu` sets the PRIME variables, so run
  through the scripts rather than a bare `uv run python tools/...`.
- **A traceback while the model loads is not a failure** — see tier 3. Look for
  `loaded  20 waypoints x 8`; if it is there, the load worked.
- **Tier 4 hangs at connect** — the bridge container. `docker ps` should show
  `openpilot-bridge`, and something should be listening on 5558.
