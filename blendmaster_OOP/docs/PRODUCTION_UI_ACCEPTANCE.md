# BlendMaster production handoff: UI acceptance

Use a copy of a configured site project. Save the original separately. Run the
application as Planner and Support in separate sessions; the launcher supplies
`BLENDMASTER_ROLE`. The desktop role setting must become authenticated server
permissions in the web application.

## Planner journey

1. **Site Configuration:** only configured site selection, planning start/time,
   periods, and project actions are available. Confirm the saved start is retained
   on reopening. Change the period count and confirm matching Calendar settings,
   targets, AMT settings and manual plans remain present. Select Now when testing
   a live replan; all preparation stages must use the same captured time.
2. **Guidance Schedules:** browse 2WP, closing balance, 24HR and HI files. Confirm
   the chosen dig circuits and movement rules survive an unchanged/new compatible
   file. Import a valid file lacking one chosen identity and inspect the reported
   removal. An invalid/incomplete replacement must leave the previous input and
   valid selections intact. Newly added equipment must not become selected.
3. **Stockpile Inventories:** inspect Use/AMT choices, nearest crusher, opening
   build/time and balances. Unselected rows must not become planning sources.
4. **Grade Reconciliation:** refresh Snowflake factors and calculate the evidence
   review. Inspect Sources and evidence and Local factors and windows. Submit must
   wait for the current review. A changed mapping, opening selection, reconciliation
   setting or start time must require fresh preparation.
5. **AMT Stockpiles:** inspect included/excluded footprints, selected builds,
   zeroing outcomes, lineage and coverage, reclaim direction, chunk settings and
   the submitted sequence. Confirm a refreshed map retains the intended settings
   and excludes the same material. Review quarantined/missing geometry evidence.
6. **Product Targets:** inspect imported tonnes/grades, target mode, evaluation
   basis and local overrides. Use Review Refresh Changes after a new 2WP. Manual
   overrides and policies must survive refresh. Check both product lanes where
   the site uses lump/fines products.
7. **Views → Expit Sequence and Workspace → Destination Reconciliation:**
   confirm both are disabled until their inputs are available, and hover for the
   missing preparation. Expit requires a configured start, 24HR plan, selected
   dig circuits and transaction reconciliation enabled. Destination requires a
   configured start, 2WP and inventory Nearest Crusher assignments.
   Open each available page and wait for refresh to finish;
   inspect actual versus planned movement, agent/dig circuit, lookback window,
   remaining allowance, build order and unresolved 24HR-only destinations.
8. **Decision Levers and Calendar:** inspect the retained business controls and
   each period's equipment/grade limits. Submit; progress and completion remain
   on Calendar. Input controls remain locked until the current operation stops.
9. **Optimised Blend Sequence:** verify the Gantt and snapshot show the same
   selected occurrence, including repeated uses of the same blend. Check snapshot
   readability, time boundaries and four-decimal phosphorus values. The old
   transaction box above the chart must be hidden.
10. **Manual Blending Dashboard and Manual Blend Sequence:** try an equipment
    rate below Calendar, then above Calendar. The lower value is allowed; the
    higher value must be rejected. Recheck a sequence crossing a period boundary
    and a recipe whose ratio rounding would exceed a source/point reclaim limit.
    Prepopulate without rounding from a fresh optimised plan and submit the
    dashboard and sequence. Exact durations must survive the rounded display.
    Changing manual rounding must invalidate only the manual plan.
    A recipe edit must make the manual plan stale before Submit. Save and reopen
    a freshly generated plan: the sequence table and Gantt must already be
    populated, and unchanged recipes must remain current.
11. **Blend Plan:** confirm Single tipping point, Multiple tipping points or
    Combined OPF matches the configured site. Inspect both Optimised Plan and
    Manual Plan and every physical point. A completed calculation must still
    show separate freshness, inventory, equipment, target, quality and destination
    checks. Editing inputs after generation must block export until regeneration.
12. **Exports:** compare UI, XLSX and PDF start/end times, physical source tonnes,
    ratios, point names and totals. Inspect quality, coverage/lineage, rounding,
    direct-tip, backup destination and readiness evidence. Resolve any blocked
    check. Review warnings must remain visible in the exported handoff.
    Check four-decimal assay values and progress while a large export writes.
13. **Material Destination Plan and Views:** inspect Database View, OPF Production
    Report, both Grade Profiles, Material Flow, Build and Depletion Profiles and
    Closing ROM Stocks Compliance. Confirm the correct site/plan selection and
    totals across views; opening a view must not alter planning inputs.
    On Build and Depletion Profiles, check the first and last source pages.
    Grade Profiles must be disabled without results; its Optimised and Manual
    pages enable independently as their results become available. Clear the
    manual plan and switch sites to confirm obsolete availability is removed.
    Gantt, AMT, build/depletion and grade charts should load automatically without
    Load/Update buttons. Change the selected plan or regenerate results and
    confirm the next view shows the new values. Return to a previously opened
    chart after switching sites and confirm it shows the selected site's data.

## Support and agent handoff

14. As Support, confirm access to Site Model Settings, Guidance Settings,
    Define/Map Fields, Data Streams, Solver Configuration, Multi Feed Setup,
    Conveyors & COS, Database Reports, Site Automation, Decision Diagnostics and
    Legacy Agent Bridge. Planner must not see or invoke these setup operations.
    Crusher Contribution remains relevant to single-point target scaling and is
    hidden in multiple-point modes. Calendar owns point reclaim limits.
    Multi Feed Setup is disabled in single-point mode; the legacy bridge is
    disabled when the legacy feature is off.
15. In Site Automation, save/export/reimport a contract for the selected site.
    Check timezone, file paths, required/optional sources, arrival windows,
    retention policy, refresh interval and handoff endpoint. Defaults are weekly
    Wednesday for HI/2WP/closing balance and daily 14:00–15:00 for 24HR. Previous-day
    24HR remains usable while a replacement is being produced. Check both a valid
    arrival and a failed/missing replacement.
16. Run **Prepare Inputs** and verify all preparation stages complete without
    optimisation. Run **Prepare Blend Plan** explicitly and verify optimisation,
    report checks and any review outcome. Cancel during a long operation: later
    stages must not run, and controls unlock only after the worker stops. Closing
    during work must retain the database until workers stop.
17. Run the documented agent CLI into a new output directory. Inspect `run.json`
    and reopen `prepared.prj` as Planner. Confirm role, file versions, settings,
    evidence and plans persist. Repeat on each site's real single-point,
    multiple-point and combined-OPF model before release.
18. Use Save Project, then close with Yes to save. Confirm each completed save
    creates a separate file, and reopening preserves freshness for unchanged
    optimised/manual plans, settings, target overrides and accepted inputs.
    Destination Reconciliation's prepared evidence and eligible manual backup
    choices must survive reopening without a warehouse query. Changing its site,
    start, source files, inventory-area mapping or lookback must invalidate it.

The desktop scheduler runs only for the active site while a Support/Owner/Agent
session is open. Closed-app scheduling, AWS hosting and web authentication require
the production integration described in `SITE_WORKFLOW_OPERATIONS.md`.
