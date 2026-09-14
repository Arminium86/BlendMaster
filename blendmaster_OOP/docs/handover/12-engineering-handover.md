# 12 - Engineering Delivery Plan and Operational Handover

## Handover objective

Deliver a supported web planning application whose domain behaviour is traceable to the validated desktop PoC. The engineering team owns production implementation and operability; the planning/reconciliation owner owns business semantics and acceptance. Keep this distinction visible throughout migration.

The remaining local model is CC_Combined_OPF_Validation.prj. Earlier project-copy paths in historical notes are obsolete. The v0.3.326 work consists of a compact AMT sizing table, consistent release branding and this handover collection. It does not deploy a cloud service.

## Delivery phases

| Phase | Engineering deliverables | Exit evidence |
| --- | --- | --- |
| 1. Discovery and baseline | Confirm Nova/platform owner; inventory source contracts; checksum retained model; agree pilot site and target policy | Signed scope, owners, open-decision register and reproducible baseline |
| 2. Domain extraction | Plain preparation/run requests; injected dependencies; no Qt in worker core; controlled solver lifecycle | Desktop versus extracted-engine parity fixtures |
| 3. Platform foundation | Identity/site authorization, job queue, durable metadata/artifacts, observability, CI/CD and secret management | Isolation, retry, failure recovery and deployment tests |
| 4. Planner web journey | Site/input review, chunk sizing/map, targets/Calendar, async runs, comparison and conflict imports | UI/UX and domain acceptance on representative scenarios |
| 5. Reports and operations | Per-point plans, consistent audits, exports, readiness and controlled publication | End-to-end conservation, freshness and report parity |
| 6. Shadow pilot | Run alongside current planning workflow; compare decisions and actual outcomes; resolve findings | Production owner acceptance and documented limits |
| 7. Cutover and support | Release manifest, runbook, recovery rehearsal, support rota and rollback path | Named go-live approval |

Do not set dates or staffing estimates without a team and platform assessment. Avoid combining numerical algorithm redesign with the first platform migration; establish parity before optimization changes.

## Required ownership

| Responsibility | Accountable role to assign |
| --- | --- |
| Domain rules, quality/volume policy and acceptance | Planning/product owner |
| Warehouse grain, identity, corrections and latency | Source-system/data owners |
| Service/API design, testing and migration | Software engineering lead |
| Interaction design and planner usability | UI/UX lead |
| Hosting, deployment, capacity and recovery | Platform/SRE owner |
| Identity, access and data handling | Security/identity owner |
| Pilot operation and issued-plan use | Production operations owner |

Record named people and escalation routes during discovery. No individual is assigned automatically by this document.

## Open decisions

Confirm Nova integration and hosting; corporate identity/site permissions; approved service runtime and design system; input-delivery interfaces; warehouse service identity/network path; production target/quality policy; assay feed-to-sample lags; acceptable data age; run concurrency and performance objectives; retention and recovery objectives; report distribution; pilot site; and the scope of future combined-mode manual editing.

Each decision needs an owner, due point, options, evidence, selected outcome and affected acceptance criteria. Unconfirmed hosting and measured assay lags must remain visible blockers to their dependent production work.

## Operational runbook

Before a run, verify source acceptance, site/start/timezone, model revision, target policy and required evidence. During a run, monitor stage durations, memory, solver status, warehouse latency and cancellation. Afterward, inspect readiness dimensions and report completeness before publication.

For a failed input refresh, retain the prior accepted version only under configured policy and show its age. For an infeasible solve, preserve previous results and explain the limiting rules; do not silently soften targets. For a failed worker, retry through the same idempotent job contract. For a stale result, keep it inspectable and require recalculation before eligible export.

Rollback should restore a known compatible application/model version and publication pointer, not destructively rewrite evidence. Rehearse database/artifact recovery and prove that a failed batch cannot replace the last complete shared revision. The desktop scheduler is not an unattended fallback production service.

## Reproduction and release package

From C:\BlendMaster\blendmaster_OOP, use the validated Python environment:

`python -m unittest discover -s tests -p "test_*.py"`

Desktop launch is `python -m GUI.InitialiseGUI`. The named build specification is now `BlendMaster.spec` and obtains the executable label from AppVersion.py. Existing packaging still has workstation-specific dependency paths; an executable was not rebuilt during this handover. Curate production dependencies, licenses, solver binaries and reproducible container builds during extraction.

The release package should contain source commit and dirty diff, version manifest, retained-model checksum, schema contracts, accepted input fixtures, regression/parity receipts, architecture decisions, UI specifications, runbooks and known defects. Never bundle credentials or rely on transient workstation logs as the only evidence.

**Evidence:** [workflow CLI](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/execute/SiteWorkflow.py), [current acceptance](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/docs/CONTINUOUS_OPERATIONS_IMPLEMENTATION.md), [site workflow implementation](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/SiteWorkflow.py), [tests](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/tests).

