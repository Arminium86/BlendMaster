# 05 - AMT Stockpile Reconstruction and Chunking

## Physical model

AMT gives spatial distribution within a stockpile; the inventory build ledger provides its opening-total reference. Preparation is evaluated at the scenario timestamp and correct physical build. Grade-block attribution provides modelled properties and provenance, while native hex assays remain the insitu chemistry baseline.

## Evidence and reconstruction

| Source object | Responsibility |
| --- | --- |
| INVENTORY_STOCKPILE_TRANSACTIONS | Active build and opening balance |
| SLN_AMT.AMT | Deduplicated inbound/outbound trips and target/source hex allocations |
| AMT_HEX_GRADES | Native hex assays and coordinates |
| AMT_STOCKPILE_HEX_TRUCK_LIST | Time/hex/stockpile candidate linkage and grade-block attributes |
| INVENTORY_EXPIT_REHANDLE_TRANSACTIONS | Preferred exact trip identity, source grade block and product/feed properties |
| STG_GRADECONTROL.GRADE_BLOCKS | Current grade-block physical/product properties |
| INVENTORY_GRADE_BLOCKS | Explicit historical property fallback |

Use the actual SQL contracts in the repository for full schema and filtering. Deduplicate trip identities before summing signed movements. Filter to the opening time/build and non-deleted applicable movements. Do not multiply tonnes by joining a trip to several property candidates.

Spatial reconciliation addresses negative source-hex deficits using geometry and donor material. The current guards run before chunking: a non-positive raw signed footprint is zeroed even if inventory is positive; a non-positive known inventory balance also zeroes an otherwise positive footprint. Preserve signed totals, adjustments and reasons in the audit. Missing inventory is distinct from a known zero; the retained positive spatial mass must carry its warning.

## Grade-block lineage

Prefer exact AMT INTERNALID to EXPIT INTERNAL_ID linkage. Truck-list matching uses footprint/location, target hex and dump timestamp, with deterministic candidate selection. Keep match method and coverage instead of presenting uncertain attribution as exact.

When reclaim lineage within a mixed hex is unavailable, remaining grade-block composition uses the declared proportional-depletion assumption:

remaining WMT for block g = final reconciled hex WMT × inbound share for g.

This is not physical-layer tracking or FIFO within an AMT hex. Unmatched shares stay visible. Compute each modelled property's value using its own valid denominator and retain coverage. Missing chemistry for one property must not suppress all other properties.

## Chunk sizing and reclaim sequence

The Chunk Settings table is a sizing control. Its nine fields cover footprint inclusion, raw/reconciled/inventory tonnes, reclaim rate, target hours, computed count and computed WMT per chunk. Grade vectors, lineage summaries and detailed inventory provenance are no longer displayed there; they remain in the data model and audit views.

Defaults are 2,000 t/h and 72 target hours. Target chunk WMT is rate × hours. The algorithm considers the neighbouring whole counts around total WMT / target chunk WMT, chooses the count whose resulting hours is closest to the target, and prefers fewer chunks on a tie. Positive stock uses at least one chunk; zero stock produces zero chunks. The UI rejects non-positive rate/hours for included footprints.

Example: 300,000 WMT at 2,000 t/h and 72 hours targets 144,000 WMT. Two chunks produce 150,000 WMT and 75 hours each; three produce 50 hours. The two-chunk option is closer.

Map direction, dig path and member-hex order govern spatial chunk generation. Partial hex use scales additive quantities and lineage without duplicating physical mass. Do not rebuild chunk chemistry from different raw fields after a canonical chunk has been accepted. Cloudbreak configured lump/fines transformations are applied at the appropriate chunk stage.

## Exclusion and audit experience

Footprint exclusion is reversible and recorded with reason/time. Excluded footprints remain visible for restoration but cannot enter preparation or scheduling. Rechecking may require data refresh; it must not silently resurrect an obsolete cached footprint. Hex exclusion and footprint exclusion are different actions.

Database View must retain all stored streams, coverage and source identities. Hiding grades from Chunk Settings must never remove fields from report exports or change field mappings. Preserve explicit user field selections as schemas expand.

## Acceptance and handover

Verify deduplication, build/time matching, positive/negative guards, geometry outliers, missing inventory, partial lineage, chunk mass conservation, sequence continuity, exclusion restore and save/reopen. For the presentation change, verify rate edits still update count/size and exclusion still works.

**Evidence:** [spatial algorithm](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/setup/AMTSpatialReconciliation.py), [lineage](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/setup/AMTGradeBlockLineage.py), [chunk count](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/AMTChunking.py), [exclusions](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/AMTFootprintExclusions.py), [detailed AMT specification](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/docs/AMT_OPENING_HEXES_AND_GRADE_BLOCK_LINEAGE.md).

