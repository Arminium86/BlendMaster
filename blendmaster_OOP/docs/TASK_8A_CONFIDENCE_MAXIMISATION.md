# Task 8A — Auto-maximise reconciliation confidence

Implemented 6 September 2026 at the user's request, bringing the previously
unnumbered deferred Q38/Q42 feature forward before Task 9. Existing task numbers
are unchanged. Standard global reconciliation remains the default.

## User workflow

1. In **Data Streams**, select **Auto · maximise confidence**.
2. Set **Minimum production days** and **Maximum lookback (calendar days)**.
   Auto chooses the window family and N, so the manual window/N controls are
   disabled. Local cell/analyte guardrails remain available in **Local factors
   and windows**; existing local factor edits still apply.
3. Click **Calculate review**. The search runs in a background worker against
   a frozen source/settings snapshot. Submit and CSV export become available
   after a successful review matching the current inputs.
4. Inspect **Selected window**, source evidence, and the overall summary. They
   report the chosen policy, original full-window spatial confidence, and gain
   in percentage points. Source details also compare the best result in each
   supported window family.
5. In the local matrix, select **Review source / hex** to see that source's
   automatic factors. The same spatial cell can have different automatic values
   for different source compositions. Local manual edits still apply to that
   cell/analyte across all its sources.
6. **Submit** applies the selected factors through the existing inventory, hex,
   chunk and persistence workflow. **Export review CSV** includes the selected
   windows, baseline confidence and confidence gain.

Restart an already running BlendMaster process to load the new controls.

## Search and confidence contract

- One policy is selected per physical inventory stockpile or AMT hex and brand,
  using the complete source composition. Inventory is not split into artificial
  chunks. All components of that source share the selected policy.
- Candidates cover the existing spatial/compositional method with whole-day
  trailing timestamp horizons, plus lookback over trailing completed calendar
  dates, last N brand production dates, and final N dates of the latest
  consecutive-date campaign. A gap between production dates ends a campaign.
  Only completed, validated periods for the requested OPF/brand are eligible.
- The search is exhaustive over the distinct evidence sets available from
  those families within the configured bounds. Empty-day boundaries and
  equivalent memberships need not be evaluated repeatedly. It does not choose
  arbitrary assay-period subsets, independently move both ends of a historical
  window, or independently optimise a window for each analyte or component.
- Every cell/analyte keeps its minimum distinct production dates and maximum
  calendar lookback. A local maximum can extend beyond the default. A selected
  spatial horizon is clipped to each analyte's own maximum. Production/campaign
  N counts production dates, still bounded by each maximum calendar lookback.
  Auto retains saved manual window/N settings for switching back to other modes.
- A component must find one common fallback level supporting blend and
  regression for all five analytes. Factors retain the existing total-period
  feed WMT weighting. The component confidence is the lowest of its ten series
  scores, and physical source fractions weight component confidence.
- Confidence retains both composition and spatial-address overlap. It is a
  diagnostic evidence score, not a physical-distance measurement or a
  statistical confidence interval. Manual factor edits occur after the search
  and do not raise the evidence score.
- The full maximum-lookback spatial result is included as the baseline
  candidate. Scores are ranked to ten decimal places. Ties prefer less global
  evidence, finer source-weighted fallback depth, more supporting production
  dates, then more period feed WMT. Remaining ties use fixed policy order:
  baseline first, shorter spatial horizons in descending order, then calendar,
  production and campaign N in ascending order.
- With fixed source tonnes and no shared resource constraint, independently
  maximising each source score also maximises the WMT-weighted overall score
  over this supported search space. Unknown lineage retains global factors
  and zero confidence for its physical fraction. Positive sources without
  lineage or eligible history remain at zero; zero-WMT sources are unscored.
- AMT chunks aggregate the selected per-hex factors and audit scores. Before
  chunks exist, raw hexes can be evaluated and aggregated into labelled
  footprint previews. If hexes/lineage are absent, the existing provisional
  global fallback remains until source data is available.

## Integration and saved state

The method is persisted as `auto_max_confidence` in the existing reconciliation
settings. Optional `auto_selection` audit metadata retains the chosen family/N,
baseline, gain, candidate/evidence counts, comparison families, objective and
tie rule. Existing JSON/pickle and source/chunk persistence carry these fields
without a schema-version change. Database View and CSV add per-brand
`selected_window`, `baseline_confidence_pct` and `confidence_gain_pp` fields.

Selection caches are bounded and period memberships are shared across repeated
source/cell evaluations. Similarity is calculated once per period per source.
The review worker transfers its application cache after the snapshot is verified;
stale workers cannot restore old settings or enable Submit. Existing agent
workflow advancement waits for the completed review. Switching from another
advanced method reuses the loaded history when the required horizon is unchanged.

## Validation

- 26 new tests cover exhaustive window-by-window oracle comparisons over ten
  varied histories, different source compositions, all window families, local
  bounds, shared fallback, missing factors/lineage, zero WMT, deterministic ties,
  manual edits, source/hex/chunk application and repeat enrichment, serialization,
  CSV, input-cache reuse, source-specific matrix values and stale/error handling.
- A real Qt worker test verifies that review results return on the UI thread
  before Submit is enabled. Native Qt review and local-matrix screenshots were
  rendered with installed Segoe UI fonts and visually inspected.
- Full regression suite: **634 tests passed**. The two existing pandas warnings
  remain in CaseModeller and DrawCharts.
- Reused the saved Task 7 read-only warehouse extract: BIG01 inventory and 44
  positive KAN AMT hexes, also previewed as two test chunks. Physical WMT remained
  **129,904.43232**. Overall confidence rose from **15.487374912%** to
  **26.656915597%**, a gain of **11.169540685 percentage points**.
- BIG01 selected a one-day spatial horizon; AMT selections varied between one,
  two and six days. The complete local review took approximately **0.94 seconds**,
  excluding warehouse reads and rendering. This measured diagnostic score gain
  does not establish predictive accuracy on future production.
- Validation used the saved extract, temporary CSV/database files and isolated
  Qt contexts. No new warehouse read or production-data write was required.

Task 8A is complete. Task 9 has not started. Changes are not committed or pushed.
