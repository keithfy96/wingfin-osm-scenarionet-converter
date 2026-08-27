# docs/reference — what runs

Operational reference for this repo: the measurements, sweeps and before/after counts
behind the traps listed in Section D of `CLAUDE.md`. **Read the file for an area before
changing anything in that area.**

These describe **what runs**, and are deliberately distinct from the other doc folders:

| folder | holds |
|---|---|
| `docs/reference/` | what runs today — this folder |
| `docs/implementation-plan/` | what was *planned*, per stage |
| `docs/mapping-algo-changes/` | the countable record of corrected mapping mistakes (Section B) |
| `docs/ai-action-logs/` | session records |
| `docs/policies/` | Stage 2 algorithm policies and the finding reference |

| file | covers |
|---|---|
| [ego-route-and-signals.md](ego-route-and-signals.md) | The ego route, the geometry a route drives through a junction, waiting at a red, and where signal timing comes from. |
| [running-the-simulator.md](running-the-simulator.md) | `--step-hz` / `--decision-hz` / `--physics-hz`, the two clocks, what a step costs, why 3D needs its own runner, the container. |
| [sensors-and-observations.md](sensors-and-observations.md) | What the observation contains, the four sensor modalities, the policy socket, keeping a frame on the GPU, what a recording holds. |
| [openpilot-and-the-model.md](openpilot-and-the-model.md) | The openpilot bridge, the pedal map measured on MetaDrive's car, the AV3 checkpoint, the six conversions to the model. |
| [live-traffic.md](live-traffic.md) | Placing other cars, why they looked aimless, giving way where routes cross, slowing for the corner. |
| [lane-markings-and-surfaces.md](lane-markings-and-surfaces.md) | Junction kerbs, sealing holes in the tarmac, clipping paint off the road beside it. Export-time; no fingerprint moves. |
| [lane-model-algorithm.md](lane-model-algorithm.md) | Lane allocation, turn restrictions, off-ramps, where a block sits across its way (v21–v26), and what is still starved. |

## Where this came from

Split out of `CLAUDE.md` on 2026-08-27. That file is loaded into context in full at the
start of every session and had reached **223 KB** — 51 commits over 18 days, +3,350 lines
against −222, never once shrinking, because each session appended its findings and no
session was ever the one that removed any.

2,875 lines moved here **byte-for-byte**; `CLAUDE.md` came down to ~25 KB and kept a short
block of traps per file. Section B2 of `CLAUDE.md` is the rule that stops it refilling:
the trap goes there, the measurement goes here, and that file stays under 30 KB.
