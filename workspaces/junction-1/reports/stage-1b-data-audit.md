# Stage 1B Data Audit

- Stage 1B status: **passed**
- Downstream readiness: **review_required**
- Selected OSM ways: 41
- Graph nodes: 139
- Directed graph edges: 196

## Lane Counts

- Ways missing lane counts: 14
- Missing counts by highway: `{"residential": 14}`
- Missing-count inference enabled: true
- See `stage-1b-data-audit.json` for every affected OSM way ID.

## Connectivity

- Weakly connected components: 2
- Component node counts: 129, 10
- Components are observations only; no missing connections are invented or discarded.

## Widths

- Ways with an explicit `width`: 0
- Ways missing `width`: 41
- Configured default lane width: 3.5 m

## Traffic Signals

- Source signal nodes: 1
- Signals on retained graph nodes: 1
- Signals outside retained graph nodes: 0
- Retention does not by itself prove an unambiguous lanelet association.

## Turn Restrictions

- Source restriction relations: 8
- Relations whose way members are all retained: 5
- Partially or non-retained relations: 3
- A later conversion stage must still validate `from`, `via`, and `to` roles.

## Stop-Line Geometry

- Candidate source ways: 0
- Detection matches normalized tag keys or values exactly equal to `stop_line`; bus stops and transit relation roles are excluded.

## Readiness Risks

- Missing lane counts require an explicit inference or source correction before lanes are generated.
- Disconnected components prevent routes from crossing between them.
- This report audits source evidence; it does not generate lane geometry.
