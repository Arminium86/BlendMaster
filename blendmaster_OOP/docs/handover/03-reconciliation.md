# 03 - Grade Streams and Reconciliation Design

## Calculation layers

Grade preparation has three separate layers: mapped source baselines, historical reconciliation, and optional continuous assay corrections. Preserve them independently so a reviewer can reproduce an adjusted result and distinguish measured evidence from inferred chemistry.

| Source | Baseline and adjustment |
| --- | --- |
| Inventory stockpile | Modelled ROM follows mapped inventory insitu; modelled product follows the applicable mapped product channel; historical blend and regression factors adjust ROM and product respectively |
| AMT | Hex insitu supplies ROM chemistry; grade-block lineage supplies modelled product properties; historical reconciliation applies separately |
| APS | Explicit brand/stream mappings supply authoritative schedule chemistry; do not add stockpile OPF factors a second time |

Every OPF receives its own source profiles and reconciliation evidence even when physical stock is shared. Product channel aliases differ between source systems; for example, the CC OPF02 inventory channel is physically PROD3 but is projected into canonical Product 2 names. Support must validate these mappings for each site. Dry-plant and unconfirmed-channel behaviour must be explicit; do not infer a wet-plant yield or silently certify an unconfirmed product mapping.

## Historical reconciliation

Standard global and advanced spatial/compositional methods coexist. Advanced settings support lookback-based evidence, source relevance and automatic evidence-match search. Retain the chosen time window, spatial level, contributing history, source composition, factors, coverage, overrides and rejection reasons.

The spatial factor ladder uses pit/stage/bench/blast/flitch/material, then successively broader levels through pit/material and the global OPF/brand/analyte factor. This is a different ladder from destination selection and from haul-route timing. Follow the relevant module rather than sharing one generic hierarchy.

A useful factor is still model-dependent. The evidence match score describes similarity of source evidence; it is not a calibrated probability or a confidence interval. Missing lineage, partial property coverage and fallbacks remain review evidence. Do not turn absent chemistry into zero or apply a factor repeatedly to an already adjusted baseline.

## Continuous assays

The current estimator uses a linear Kalman measurement update on source-grade offsets with a full covariance matrix. A mixed assay constrains a blend: a fixed mixture cannot uniquely identify each source's true chemistry. Preserve uncertainty and correlation between contributing sources.

The observation must match OPF, brand, physical source/build identities, feed contribution mass, product dry yield, timing and laboratory sample identity. Collapse repeated production rows to the laboratory-sample grain. A repeated warehouse refresh is not a new observation; a substantive sample revision replaces and replays its prior update.

Support configures enablement, polling, age/mass checks, prior and observation uncertainty, innovation gates, per-update/total bounds and per-OPF feed-to-assay alignment lags. Five minutes is the desktop polling default, not a guaranteed source SLA. With transport enabled, measured alignment lags are required; the application does not invent residence times.

Reject or withhold unknown sources/direct tips, ambiguous AMT chunks, unmatched brands, incomplete/future or overlapping samples, missing quantities and mass/bound violations. The result should say what was withheld and why, while preserving the last accepted evidence where policy allows.

## Application and invalidation

Corrections affect future adjusted-product calculation records and declared aliases. Raw baselines and completed saved reports keep their original values. A fresh inventory build, changed prior chemistry or new inbound stockpile material invalidates an old source correction. A change of context while a worker is running prevents its stale result from being applied.

Optimised states split at assay availability times. A fixed manual copy may need to be recreated from a fresh solve when an update lands inside its stored state. Do not relabel old grades in a saved report as if the new estimate had been used.

## Engineering handover

Extract reconciliation into a deterministic service accepting evidence, source profiles and policy, returning adjusted profiles plus a complete audit. Persist accepted observations and estimator revision together; make retry/replay idempotent. Keep warehouse acquisition and UI polling outside numerical code.

Acceptance must cover repeated samples, revised samples, partial lineage, two OPFs sharing a stockpile, source-build changes, unavailable warehouse data, timing lags, bounded offsets and stale callbacks. Validate against measured operating evidence before enabling automatic correction in a production pilot.

**Evidence:** [historical application](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/ReconciliationApplication.py), [factor resolver](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/ReconciliationFactorResolver.py), [evidence search](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/ReconciliationConfidenceSearch.py), [continuous estimator](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/ContinuousAssays.py), [combined OPFs](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/CombinedOPFReconciliation.py).

