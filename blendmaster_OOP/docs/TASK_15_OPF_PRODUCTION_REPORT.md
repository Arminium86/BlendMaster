# Task 15 — OPF Production Report

Completed 7 September 2026 after Task 14. The new tab is in **Workspace**,
immediately after **Product Targets**. Task 16 has not been started.

## Short UI review guide

1. Restart BlendMaster with the updated source and load your scenario. Open
   **Workspace → OPF Production Report**. Check that **From (AWST)** and
   **To (AWST)** initially cover the 24 hours before the scenario start.
2. Select **OPF** and **Product brand**, then **Refresh**. Check the loading
   indicator, the five assay charts and the fetch/production/update timestamps.
   Changing OPF or dates clears the old scope and requires Refresh. Changing
   brand or aggregation redraws the loaded records immediately.
3. Compare **Raw observations**, **Shift (DMT weighted)** and **Daily (DMT
   weighted)**. Hover observations for the value, DMT, assayed-DMT coverage and
   sample time. Shift/daily values include only records within the selected
   window, so the first and last periods may be partial.
4. Check **LQL**, **Target** and **HQL** against Product Targets. Hover the
   vertical build-change markers and open **Target build details** for all
   visible build intervals and unrounded specifications. A default historical
   window may precede your dated 2WP targets; the report says when none overlap.
   The first undated build is labelled a current reference, with no invented
   historical build schedule. Limits remain reference lines in this task.
5. If multiple OPFs are configured across your site scenarios, select **All
   configured OPFs** and toggle **Show combined series**. Per-OPF values remain
   visible; the combined value uses actual DMT rather than equal OPF weights.
   Choose a common brand such as SF for a comparison across warehouse aliases.
   Missing OPF contributions are explicitly noted. Quality specifications stay
   attached to their OPF.
6. Check **Auto-refresh (minutes)**; **Off** disables it, and leaving the tab
   pauses the timer. It refreshes the selected window rather than advancing it.
   **Last 24 hours** resets the window to the scenario start. Save/reload your
   project to check the retained report filters and refresh interval.

## Screenshots and state checks

These native UI captures use **illustrative records and targets**, including
controlled loading/outage/empty responses. They do not represent actual plant
performance. A separate live warehouse read validated the service, as documented
in Task 14.

| State | Expected behavior | Screenshot |
|---|---|---|
| Loaded | Five charts, OPF/combined legend, target lines and build markers | [Loaded](screenshots/task15/loaded.png) |
| Loading | Inline progress, Refresh disabled; previous data may remain for the same selection | [Loading](screenshots/task15/loading.png) |
| Cached | Cached-data label, original fetch timestamp | [Cached](screenshots/task15/cached.png) |
| Snowflake unavailable, cache exists | Amber message and matching cached charts; timestamps retained | [Offline cached](screenshots/task15/offline-cached.png) |
| Successful empty response | Explicit no-product-record status and no assay curves; target references can remain | [No data](screenshots/task15/no-data.png) |
| Smaller window | Filters, plots and legend remain readable | [Compact loaded](screenshots/task15/compact-loaded.png) |
| Raw observations | Production observations before aggregation | [Raw](screenshots/task15/raw-observations.png) |
| Daily | One DMT-weighted value per OPF/calendar day and the combined value | [Daily](screenshots/task15/daily.png) |
| Full application | Actual Windows navigation and background-worker integration | [Full application](screenshots/task15/full-application.png) |

An outage with no exact cached window/OPF match displays an explicit unavailable
message and no historical curve from a different selection. Missing assays stay
missing, including a visible warning when records exist but every assay is absent.
The automated fixtures cover these cases without changing network settings or
requiring a real warehouse outage.

## Implementation and validation

- `GUI/OPFProductionReport.py` supplies native Qt controls and Matplotlib charts;
  the existing background-task runner performs warehouse reads. Requests carry a
  generation token, so late replies cannot replace a newer scenario/window.
- `classes/ProductAssayReport.py` resolves row-owned, dated target specifications,
  keeps CB lump/fines distinct, and reports ambiguous aliases or overlapping
  ownership. Existing Min/Max constraints and reconciliation calculations are
  unchanged.
- Report settings persist separately per scenario and in the project. Existing
  flat-tab restoration identifiers are unchanged. The read-only report is
  accessible before optimisation inputs are complete, once an OPF/start exists.
- Matplotlib 3.10.3 is declared in requirements and the report SQL is included in
  both executable build specifications. An existing packaged executable must be
  rebuilt to include this tab; no executable was built during this task.
- **801 automated tests passed**, including 15 history-service tests and 26
  report/target/UI tests. The two existing pandas deprecation warnings remain.
  Python compilation and `git diff --check` passed.
- Native Qt control tests cover all three grains, combined weighting, target
  changes, loading, fresh cache, offline cache, no cache, empty data, missing
  assays, stale responses, fixed-window refresh, settings and tab placement.
  The actual Windows application initialized and loaded the tab using its normal
  navigation and background worker. Full application smoke testing used the
  Windows platform because its existing QtWebEngine views cannot initialize in
  the offscreen platform; report component captures use offscreen Qt.
- Visual review covered large and compact windows. Overlapping legend/build
  labels found at compact size were resolved by keeping build details in the
  expandable panel.

Reproduce the controlled screenshots from `blendmaster_OOP`:

```powershell
python -m tests.render_opf_production_report
```

Run the checks:

```powershell
python -m unittest tests.test_product_assay_history tests.test_opf_production_report -q
python -m unittest discover -s tests -q
```
