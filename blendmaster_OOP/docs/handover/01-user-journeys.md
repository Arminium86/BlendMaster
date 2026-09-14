# 01 - User Journeys and UI UX Handover

## Product experience

The production experience should guide a planner from a configured site to an explainable, current plan. Preserve business decisions and evidence while redesigning the desktop navigation for the web. Use the existing Nova design system if its owner confirms that integration path. The current desktop screen arrangement is a behavioural reference, not a pixel-level web specification.

## Roles and journeys

| User | Journey | Result |
| --- | --- | --- |
| Support / site modeller | Configure site, topology, fields, mappings, reconciliation, transport, solver presets and input contracts | Versioned, validated site model |
| Planner | Select site and start, review prepared inputs, set targets and Calendar, calculate, compare and issue eligible outputs | Saved plan with input revision and review findings |
| Technical reviewer | Inspect source lineage, quantities, grades, route choices, solver diagnostics and audits | Explained result or actionable defect |
| Scheduled service | Acquire and validate inputs, prepare each enabled site, publish a complete input revision | Fresh prepared inputs and run receipt |
| Production owner | Agree target policy and acceptance; approve pilot and cutover | Recorded operating approval |

Today `BLENDMASTER_ROLE` supplies a desktop role. The web application must use the authenticated user's site-scoped permissions. A project file or browser payload must never grant a role.

## Main planner flow

1. Select an approved site configuration and planning timestamp. Show the site timezone and horizon.
2. Review input delivery status. Distinguish current, retained previous, missing, invalid and late data.
3. Review inventory participation, AMT chunks, reconciliation, product targets and destinations.
4. Set Decision Levers and Calendar within permitted equipment and site settings.
5. Submit a calculation against a frozen revision. Show progress, cancellation and a readable failure reason.
6. Compare the selected optimised plan with contingencies and available manual plans.
7. Review readiness dimensions, then export a current eligible plan. Save working drafts independently of calculation readiness.

When Support publishes new inputs, show a selectable change comparison against the Planner's original baseline and current edits. Local conflicts start unselected. Keep targets, Calendar choices, recipes and unsaved work unless explicitly replaced. Mark affected results stale; reopening or importing must not automatically run the solver.

## AMT chunk-sizing surface

The left Chunk Settings table has nine columns: AMT Stockpiles; Include footprint; Raw Signed AMT WMT; AMT Total WMT; Inventory Stockpile Total WMT; Average Reclaim Rate (t/h); Target Hours per Chunk; Calculated Number of Chunks; Calculated Chunk Size (WMT).

Only inclusion, rate and hours are user inputs. Keep computed values read-only and show excluded/zeroed states visibly. Grades, grade-block lineage, modelled-product coverage and detailed inventory-match provenance belong in Database View or technical reports. This presentation change does not remove the underlying evidence or alter chunk algorithms.

The web design should freeze footprint identity, group tonnes and sizing inputs, and keep the map visible without forcing every source attribute into the table. Provide keyboard alternatives for map operations, explicit units, validation beside the input, and a clear Submit result.

## Result and error states

| State | User message and action |
| --- | --- |
| No prepared data | Name the missing source and the preparation action |
| Preparation running | Stage, elapsed time and cancellation state |
| Retained previous delivery | Timestamp, age, reason and replacement status |
| Solve infeasible | Limiting rule/source/build and diagnostics; retain previous results |
| Completed with shortfalls | Actual versus target and remaining tonnes; no success-only banner |
| Stale result | Show its original revision; offer recalculation |
| Export blocked | Explain physical, equipment or freshness failure |
| Soft quality breach | Show deviation and selected policy; preserve review evidence |

The manual single-point journey supports editable recipes and recalculation after ratio rounding. Combined-mode manual handover is currently an independent copy of optimised joint allocations for per-point review; it is not an implemented combined allocation editor.

## UI/UX delivery acceptance

Provide clickable flows for normal planning, late data, conflict imports, cancellation, infeasibility, soft quality review and stale export. Test keyboard navigation, non-colour status indicators, readable units/precision, large-table filtering and persistent user selections. Compare all displayed totals and charts against the same saved result revision. Use human labels in operational views and stable internal IDs in evidence.

Proposed usability targets should be agreed with site planners before implementation; this handover does not claim user-study results. Deliver a component inventory, interaction specification, error-state copy, permissions matrix and acceptance scenarios to engineering.

**Evidence:** [desktop operation boundaries](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/GUI/WorkflowPermissions.py), [workflow/navigation reference](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/docs/SITE_WORKFLOW_OPERATIONS.md), [input comparison](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/GUI/SharedProjects.py). The older scheduler paragraph in the workflow note is superseded by ScheduledSiteBatch and page 09.

