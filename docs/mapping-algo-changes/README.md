# Mapping algorithm changes

A countable record of every corrected mistake in the lane-mapping algorithm: how
many there have been, why each happened, and what fixed it.

One file per correction. Nothing else belongs here.

## Filename

```
YYYY-MM-DD-HH:MM:SS-<algo-change-desc>.md
```

Take the timestamp from `date +"%Y-%m-%d-%H:%M:%S"` at the moment the entry is
written — do not invent or back-date one. The description is a short kebab-case
phrase naming the defect, not the file that changed:
`turn-lanes-must-not-strand-a-lane`, not `fix-generation-py`.

## When to write an entry

Claude writes an entry **automatically**, without being asked, when **all three**
of these hold:

1. **Keith identified the mistake** — he pointed at a lane, connector or mapping
   that is wrong. Not something Claude noticed and decided to change.
2. **The fix changed algorithm code** in `src/osm_scenario/` — generation,
   topology, lane mapping. Not docs, config, or tests.
3. **The fix was verified** — the workspace was regenerated, before/after counts
   compared, no existing connector regressed, and `uv run pytest` passes.

## When *not* to write an entry

- an investigation that ended without a code fix
- doc-only, config-only or test-only changes
- refactors or cleanups Keith did not flag as wrong
- an attempt that was reverted or superseded before it was verified
- anything uncertain — **ask first**, do not create the file speculatively

One correction, one file. Do not amend an old entry to cover a new defect; do not
split one defect across several entries.

## Template

```markdown
# <short title of the defect>

- **Date:** YYYY-MM-DD HH:MM:SS
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/<file>.py (<function>)
- **Generator version:** <before> → <after>

## Symptom

What Keith saw. Concrete IDs — lane, way, node. Expected vs actual. Include a
plan-view diagram if the defect is a lane-mapping one (format in `CLAUDE.md`
section A).

## Fundamental cause

*Why the algorithm produced that result.* This is the point of the record. Name
the rule, threshold or formula at fault and explain the reasoning error behind it
— not merely which line was edited. If two defects stacked, say so and separate
them.

## Fix

The change, named by file and function. What the new behaviour is, and what it
deliberately does not do.

## Verification

Before/after numbers proving the fix worked and nothing else moved. Connector
counts, dead-end counts, finding counts, status breakdown, test results.
```
