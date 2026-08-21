# Stage 1B Data Audit

- Stage 1B status: **passed**
- Downstream readiness: **review_required**
- Selected OSM ways: 69
- Graph nodes: 170
- Directed graph edges: 217

## Lane Counts

- Ways missing lane counts: 9
- Missing counts by highway: `{"residential": 9}`
- Missing-count inference enabled: true
- See `stage-1b-data-audit.json` for every affected OSM way ID.

## Connectivity

- Weakly connected components: 1
- Component node counts: 170
- Components are observations only; no missing connections are invented or discarded.

## Widths

- Ways with an explicit `width`: 0
- Ways missing `width`: 69
- Configured default lane width: 3.5 m

## Traffic Signals

- Source signal nodes: 4
- Signals on retained graph nodes: 4
- Signals outside retained graph nodes: 0
- Retention does not by itself prove an unambiguous lanelet association.

## Turn Restrictions

- Source restriction relations: 33
- Relations whose way members are all retained: 26
- Partially or non-retained relations: 7
- A later conversion stage must still validate `from`, `via`, and `to` roles.

## Stop-Line Geometry

- Candidate source ways: 0
- Detection matches normalized tag keys or values exactly equal to `stop_line`; bus stops and transit relation roles are excluded.

## Readiness Risks

- Missing lane counts require an explicit inference or source correction before lanes are generated.
- Disconnected components prevent routes from crossing between them.
- This report audits source evidence; it does not generate lane geometry.
