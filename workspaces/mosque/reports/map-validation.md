# Stage 5 Map Validation

- Status: **passed**
- Validated lane model: `lane-model/reviewed.json`
- Model checksum: `fd2e2a31b397549033b6a03ef530feb34a8c76cc1176d0b0ced7108be03facaf`
- Generation fingerprint: `b1fe3e0cc9bc58ec0e57cd4baeb81593f49752d50ac9b432823fcd495b7e1ab7`
- Errors: 0
- Warnings: 0
- Checked: 405 lanes, 200 connectors, 33 restrictions, 4 signals, 14 stop lines, across 6 checks (`geometry`, `references`, `connectors`, `restrictions`, `signals`, `boundary`)

## Errors

- None

- An error means the map is not fit to convert; Stage 6 stays blocked until it is gone.

## Warnings

- None

- A warning is a condition a reviewer judged not applicable. It is still true and still listed; it does not fail the map because someone said why it need not.

## Network Edges

- Lanes nothing leaves: 16
- Lanes nothing enters: 16
- Of those, lanes at the edge of the extract: 32
- Routing components: 3 (sizes `[349, 36, 20]`)

- A lane that stops where the source road also stops is the edge of the extract, not a missing link; only a lane stopping mid-road is reported as an error above.
