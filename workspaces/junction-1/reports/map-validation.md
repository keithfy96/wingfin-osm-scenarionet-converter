# Stage 5 Map Validation

- Status: **passed**
- Validated lane model: `lane-model/reviewed.json`
- Model checksum: `fc205bca2305a0cc3cb68e7a7ee83b5c8d8a200622d6a1749dde0b258cab65fb`
- Generation fingerprint: `57dcd345d17e5a862f9f38c97916fefe1799873c72b1e280407bb69b2e1da42c`
- Errors: 0
- Warnings: 0
- Checked: 285 lanes, 116 connectors, 8 restrictions, 1 signals, 0 stop lines, across 6 checks (`geometry`, `references`, `connectors`, `restrictions`, `signals`, `boundary`)

## Errors

- None

- An error means the map is not fit to convert; Stage 6 stays blocked until it is gone.

## Warnings

- None

- A warning is a condition a reviewer judged not applicable. It is still true and still listed; it does not fail the map because someone said why it need not.

## Network Edges

- Lanes nothing leaves: 18
- Lanes nothing enters: 19
- Of those, lanes at the edge of the extract: 37
- Routing components: 7 (sizes `[121, 63, 26, 25, 22, 19, 9]`)

- A lane that stops where the source road also stops is the edge of the extract, not a missing link; only a lane stopping mid-road is reported as an error above.
