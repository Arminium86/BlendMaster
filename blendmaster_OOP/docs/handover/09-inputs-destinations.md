# 09 - Input Operations Destinations and Haulage

## Input contract and preparation

The site contract defines timezone, delivery paths, required/optional sources, cadence, arrival window, retain-previous policy, refresh interval, retries and the prepare/plan endpoint. Defaults are Australia/Perth, weekly Wednesday haul-cycle/2WP/closing-balance inputs and daily 24HR delivery between 14:00 and 15:00. These are configurable expectations, not proof that a file is current.

Validate a replacement before accepting it. Preserve matching equipment selections, dig circuits and movement rules; report identities removed by the accepted replacement. A missed delivery may retain the previous accepted input under policy, with its age and reason visible. File modification time is currently the desktop delivery-freshness proxy; production ingestion should carry explicit source revision and event/arrival timestamps.

Preparation runs imports → inventory → guidance → reconciliation → AMT → expit → destination → database → validation. The plan endpoint additionally runs optimisation and reports. One run captures a single planning start and context.

## Scheduling and publication

The current desktop batch attempts **every enabled site contract** and publishes one stable shared project atomically only when all required site outcomes succeed. Retries and cancellation are explicit. The schedule operates while an authorized Support/Owner/Agent desktop session is open; it is not an unattended cloud scheduler.

The default shared directory is `C:\BlendMaster\SharedProjects`. Publication and Planner local saves are separate. The Planner watches its subscription for replacement, reviews a three-way group comparison and chooses imports. Local conflicts default unselected; dependent results become stale. Removed historical shared files are not required copies for the retained validation model.

The production scheduler must use durable job state, site leases, input versioning and an atomic publication pointer. A failed site must not publish a partly refreshed batch as complete. Define cadence, late-arrival handling and retention with source owners.

## Destination selection and capacity

Destination guidance, physical remaining capacity and route travel time solve different problems. Keep their rule sets separate.

DestinationRules resolves explicit candidates and fallback evidence. Its current ladder includes same-material pit/stage/bench/flitch, bench, stage and pit levels, a non-waste flitch match, and pit-area/material history. Fallback ranking uses the documented guidance and route evidence; do not substitute the haul-timing hierarchy.

PrimaryDestinationAllocator consumes only final non-direct-tipped WMT. Explicit zero capacity differs from blank/estimated capacity. At an allowed capacity boundary, keep the final non-direct-tip remainder of a payload whole at the current destination, record the overrun and advance the next payload. Preserve build instance, before/after capacity, selection basis and unresolved/outside-window tonnes in the ledger.

Material Destination Plan aggregates operational rows by parent grade block and assigned destination while the audit retains payload/build identities. Review Destinations is the operational activity view; technical build and movement IDs belong in detailed evidence.

## Destination-sensitive haulage

Loading remains payload WMT / 24HR loader rate. For the assigned destination, use an exact valid 2WP source route first; otherwise search same material through flitch → blast → bench → stage → pit → mine, then another ore material through the same hierarchy, then valid 24HR fallback values. Mine fallback requires a full mine address.

At each match prefer the 24HR truck model; otherwise use the first matching valid 2WP row. Freeze row number, truck, source/destination and LoadedTravel, SpotAtDump and Dumping components. Components are minutes; loading is converted consistently.

A destination change recomputes ETA. Capacity allocation is replayed against the revised ETA order and horizon membership. If routing/order does not converge, stop publication with an explicit reason. Do not publish a route assignment with a stale delivery time.

## Production adapters and acceptance

Use immutable accepted files/snapshots, not arbitrary server file paths supplied by a browser. Authenticate source acquisition with a managed service identity. Acknowledge successful input acceptance separately from successful preparation and successful plan publication.

Validate exact matches, every fallback level, waste exclusion, truck preference, zero/blank capacity, destination reroute, horizon crossings, duplicate payloads, changed selections, failed replacements, stale workers and all-sites batch failure. Preserve traceability to the accepted source row even when operational displays hide it.

**Evidence:** [site contract](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/SiteWorkflow.py), [scheduled batch](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/GUI/ScheduledSiteBatch.py), [destination rules](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/DestinationRules.py), [capacity allocation](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/PrimaryDestinationAllocator.py), [route timing](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/HaulageRouteTiming.py), [selective imports](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/SharedProjects.py).

