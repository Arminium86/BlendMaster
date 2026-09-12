# Production readiness implementation

Authorised scope: the six UI-audit workstreams plus planner/support/agent roles,
site models/contracts, configurable preparation cadences and responsive real-time
replanning. Preserve blending algorithms and existing valid project inputs.

## Agreed rules

- Planner: choose a configured site model and edit planning start/time and periods.
  Support/owner and agent can see/edit every area; operation permissions accompany
  navigation visibility. Site mode, OPFs and operating crushers belong to Support.
- Manual reclaim/crusher rates may differ from Calendar but must never exceed the
  applicable Calendar equipment limits, including after rounding and at export.
- Workspace follows the user's requested sequence. Views includes OPF Production
  Report; Support owns technical setup, Guidance Settings and Site Automation.
- Automatically select the best optimised result. Keep calculation progress on
  Calendar. Preserve quality, backup, direct-tip and rounding audit child views.
- Imports retain valid selections, dig circuits and movement rules. Only a
  successfully validated new file can invalidate entries; report the differences.
- Site-configurable inputs: HI cycles, 2WP and closing balance weekly Wednesday;
  24HR daily 14:00–15:00. Timezone, paths, deadlines and policies are configurable.
  The previous 24HR remains usable while the replacement is prepared/validated.
- Scheduled agent defaults to refreshed/validated inputs; an explicit contract
  request can run optimisation and prepare the plan. A run captures one start
  timestamp and input revision. Background work cannot overwrite newer work.
- Reuse valid unchanged data, run expensive work outside the UI thread, measure
  latency and refactor the large GUI controller into focused components as needed.
- No AWS deployment is part of this PoC implementation. The workflow/contract
  interfaces should be reusable by the future web application.

## Work and validation ledger

- [x] Preserve baseline local FrameModel guard/tests; verify baseline suite (1,099 tests).
- [x] Stability: safe model replacement, chart startup, asynchronous error/context handling.
- [x] Settings/import preservation; workflow freshness, dependency and transaction checks.
- [x] Canonical point identity, physical ratios, complete and consistent exports/audits.
- [x] Final manual equipment/inventory checks and explicit plan readiness outcomes.
- [x] Planner/Views/Support navigation, permissions, extracted reconciliation and guidance pages.
- [x] Site contracts, configurable cadences, safe preparation/agent execution and persistence.
- [x] UI accuracy, snapshot selection, numeric precision, layouts, graph and progress feedback.
- [x] Full regression suite, conditional-mode scenarios, live UI and export acceptance checks.
- [x] Final delivery report with measured results, remaining limitations and user UI checklist.

Validation: 1,168 regression tests passed; native solver/save/export fixtures
passed for all three modes. The supplied single-point model completed the live
workflow, screen walkthrough, manual submission, save/reopen and final exports.
Both saved plan revisions remain current after reopening. See
`PRODUCTION_VALIDATION_RESULTS.md` for measured timings, evidence files, existing
business-review outcomes and the real-site/hosting acceptance boundary.

## Starting state

Repository HEAD: `027f9235b29817369219275d0784fbe7d3469c31`.
Two pre-existing modified files contain the empty-model crash fix and tests:
`GUI/MaterialFlowResults.py` and `tests/test_material_flow_views.py`.
Those edits are not to be discarded. The original saved project is kept intact;
live acceptance work uses a separate working copy/session.

Audit reference: `BlendMaster_UI_Audit_2026-09-12.md` in the user's Playground.
