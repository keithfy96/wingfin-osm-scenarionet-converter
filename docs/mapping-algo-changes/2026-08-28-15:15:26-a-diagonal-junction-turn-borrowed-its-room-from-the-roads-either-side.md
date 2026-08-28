# A diagonal junction turn borrowed its room from the roads either side

## Symptom

After the staggered-box fix, Keith drove `junction-1` with live traffic and was still met
head-on: *"that literally, didn't do anything ... the vehicle is STILL driving into oncoming
traffic, and running on the grass."* The 60-pair traffic metric agreed: 285 m of wrong-way
driving and 84 m off the road remained on `junction-1`, in runs up to 10 m — short enough to
survive the previous fix's summary as "residual", long enough to put a truck in the ego's
lane.

## Fundamental cause

Clustering every remaining ≥2 m bad run and attributing each to the construction that draws
that stretch put all five big clusters (281 of 285 wrong-way metres, all 84 off-road metres)
on **`_turn`**, not on boxes or lane changes. The turns are ~80–97° junction movements whose
exit lane sits 3–8 m off the approach line — the junction is crossed diagonally. `_wanted_trim`
asks `TURN_RADIUS_M·tan(θ/2)` ≈ 9 m of trim per side for such a turn, and both roads could
spare it, so the arc began drifting across the carriageway up to 9 m before the junction. In
left-hand traffic the inside of a right turn is the oncoming corner of both roads (10 m runs
*along* oncoming lanes) and the inside of a left turn is the kerb corner (15–17 m off-road
per corner). The comfort radius was paid for with road that does not belong to the turn.

## Fix

In `_turn`: a crossing whose `sideways` exceeds `TURN_SIDEWAYS_MAX_M` (2.0 m) has its trim
capped at `TURN_TRIM_SPANS · gap` (0.4 of the junction span). Both new constants carry their
sweeps. Rejected alternatives, each measured: capping every crossing regardless of sideways
(tighter-than-car routes 18 → 32 on `junction-1`, one `mosque` route folds outright and is
refused); capping right turns only (leaves all the kerb-corner off-road: 15/37 m); a flat or
angle-scaled floor under the cap (the Bézier undershoots the arc estimate, so the floor
protects nothing); factors below 0.4 (`mosque`'s tightest capped turn falls 1.70 → 1.25 m —
folds forming — for 2 m of wrong-way bought).

Two tests updated with the reasoning in their docstrings: the corner fixture's radius bound
drops from a 5 m comfort figure to 1.2 × the car's 2.9424 m lock (the fixture's exact shape
on `mosque`, lane `02462c6a`, drew 17 m off-road at the comfort radius — the map does not
have room for it), and the two-rate gate comparison becomes relative (the 0.1 s window at
10 Hz sits where the steps fall; at 100 Hz it slides and finds the honest worst alignment).

## Verification

- 60 traffic pairs, `junction-1`: wrong-way 285 → **12 m** (largest single run 2 m — the arc
  *crossing* a perpendicular oncoming lane at the junction mouth, which a right turn does),
  off-road 84 → **0 m**, routes with a ≥3 m bad run 24 → **0**, tighter-than-car 18 → 18.
- `mosque`: wrong-way 30 → 17 m, off-road 41 → **0 m**, bad routes 13 → **2** (one shared
  6 m run at (−357.6, −105.3), both routes out of lane `81dbf1ca` — open, one junction).
- Blast radius: 25 of 61 `junction-1` and 19 of 61 `mosque` lines moved, every one carrying
  a cap-eligible turn; **both ego routes byte-identical**, so no reconversion was needed.
- `uv run pytest` 707 passed, including the 396-route gate sweep (0 refused, 0 over 30°).
- `traffic.json` regenerated on both workspaces with `--seed 1` (the crash video's pool) and
  re-measured to exactly the figures above.
