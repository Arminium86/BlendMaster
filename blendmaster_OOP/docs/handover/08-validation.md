# 08 - Validation Strategy and Production Acceptance

## Acceptance principle

A green test suite establishes bounded software behaviour, not production fitness. Evaluate calculation status, source freshness, physical balances, equipment limits, destination allocation, product-volume attainment and quality policy separately.

The current retained combined-OPF project is a soft-target validation case. Historical product shortfalls and soft breaches remain open business-review findings. It must not be promoted as a hard-target production baseline without a separately approved acceptance case.

## Evidence available

The September 13 native acceptance recorded both OPFs and OPF01_PC, HAL_PC and OPF02_PC; 636 optimised and 636 manual feed rows over 211 steady states; equipment checks; transport conservation error at most 6.9e-7 WMT; 2,000 independently checked haulage assignments; chart rendering; manual copy; selective input merge; save and read-back.

Observed manual-copy working-set growth was about 371 MB, with peak process memory about 6.63 GB and completion within a 23.225-second observation window. Historical reopening took roughly 20 minutes. These are workload-specific observations, not service objectives.

The v0.3.326 presentation change is checked with the repository regression suite and a focused AMT widget/render exercise. See the local handover verification receipt for the exact current run result. Earlier counts in task notes refer to earlier revisions.

## Required validation matrix

| Area | Minimum cases | Evidence |
| --- | --- | --- |
| Source acquisition | Late/missing/invalid replacement; retained accepted file; duplicate rows | Input receipt, revision, validation errors |
| Field semantics | Empty mapping, missing weight, partial chemistry, product versus ROM mass | Expected canonical values and coverage |
| AMT | Negative/zero totals, missing inventory, exclusion/restore, partial lineage, chunks | Mass reconciliation and map/source audit |
| Reconciliation | Global/advanced, two OPFs, revised assay, duplicate sample, lag and bounds | Factor/estimator audit and replay |
| Planning modes | Single, multi-point, combined; shared stock exhausted | No duplicate depletion; per-point limits |
| Targets | Hard infeasibility, soft breaches, incomplete builds and repair | Target/quality status with actual values |
| Routing | Exact/fallback route, truck preference, destination change | Frozen route row and recomputed ETA |
| Transport | Opening contents, stops, COS turnover, horizon closing | Tipped/arrived/closing conservation |
| Manual | Copy, edit, rounding, late direct tip, changed input revision | Recalculated limits and stale-result rejection |
| Persistence | Save/load, schema migration, interrupted publication, conflict merge | No lost edits or partial accepted results |
| Web operations | Cross-site authorization, duplicate jobs, worker loss, concurrency | Isolation, retry and recovery receipts |
| UI/UX | Keyboard paths, errors, large tables, loading/cancel and comparison | User acceptance findings and resolution |

## Conservation and comparison oracles

For each physical point and relevant interval:

opening transport WMT + tipped WMT = OPF-arrived WMT + closing conveyor WMT + closing COS WMT.

For each source/build, account for opening mass, inbound allocation, reclaim/direct tip and closing mass exactly once. Product mass uses mapped yields/quantity fields; grade metal uses each configured denominator. Do not compare product WMT directly with ROM stock depletion.

Use the same frozen inputs, solver policy, timezone and model revision for desktop/web comparisons. Keep expected outputs and tolerances in version-controlled fixtures. For non-unique optimisation choices, compare feasibility, objective/ranking and physical/quality outcomes under agreed tolerances.

## Release gates and owners

1. **Domain parity:** planning/reconciliation owner signs off numerical and rule equivalence.
2. **Data integration:** source owners approve identity, timestamps, freshness and correction/revision semantics.
3. **Software quality:** engineering demonstrates automated tests, reproducible builds, schema compatibility and error handling.
4. **Platform readiness:** platform/security owners approve authentication, isolation, secrets, deployment, monitoring and recovery.
5. **Usability:** planners and UI/UX approve representative task flows and understandable review states.
6. **Operational pilot:** production owner approves shadow operation against actual outcomes, support arrangements and rollback.
7. **Cutover:** named owner records acceptance and publishes the supported production version.

No gate is implicitly passed by this rewrite. Record owner, acceptance criterion, evidence link, outcome and outstanding defect for each.

## Run receipt

Each validation run should include software commit/version and dirty status, environment/solver versions, project checksum, source revisions, scenario/timezone/horizon, settings, test command, elapsed time, result counts, numerical tolerances, resource measurements, warnings and approval state. Store portable fixtures and receipts in the repository/artifact store; workstation-only Playground logs are not a durable production evidence system.

**Evidence:** [latest completed historical acceptance](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/docs/CONTINUOUS_OPERATIONS_IMPLEMENTATION.md), [readiness evaluation](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/PlanReadiness.py), [equipment checks](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/EquipmentLimits.py), [regression tests](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/tests).

