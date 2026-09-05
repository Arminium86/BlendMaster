# Task 8 — Data Streams reconciliation controls and review

Implemented 5 September 2026. Standard global reconciliation remains the default.

## User workflow

1. In **Data Streams**, select Standard, Advanced lookback, or Advanced spatial
   and compositional reconciliation.
2. Set the default minimum production days and maximum calendar lookback. In
   lookback mode, choose trailing calendar days, last N production days, or the
   final N days of the latest campaign, and enter N.
3. Click **Calculate review**. The source tree shows inventory stockpiles and
   submitted AMT chunks, with expandable hex and grade-block evidence. Before
   chunks exist, AMT footprints are explicitly labelled as previews.
4. Select a component and open **Local factors and windows**, or find its spatial
   cell using the brand selector and text filter. Select an analyte, edit blend
   or regression factors and/or its local window, then **Save local settings**.
   Blank factors inherit the automatic result. **Use inherited settings** removes
   both local factors and the local window for that analyte.
5. Calculate the review again, then **Submit** to apply the grade adjustments
   through the existing inventory, AMT enrichment, persistence and chunk workflow.

**Refresh Snowflake Factors** forces a new read. Calculate review reuses the
current input cache when its OPF, brands, opening time, selected inventory builds
and required history horizon still match. Factor edits and narrower/per-analyte
window changes therefore do not require repeated warehouse reads.

## Scope and evidence

- Local settings are keyed by OPF + brand + pit + stage + bench + blast + flitch
  + material type + analyte, as confirmed in Q32/Q44. Parent material identifiers
  sharing the same material type use the same cell. Settings do not leak across
  OPFs, brands, spatial addresses or material types.
- Each analyte can override the default window. Both blend and regression use
  that analyte's window. The resolver still requires **one shared spatial level
  for all ten factor series**, each satisfying its minimum production dates.
- The bulk history read covers the widest requested maximum for the current
  OPF and brands. Each cell/analyte subsequently filters within its own bounds.
  No automatic window expansion or confidence maximisation is introduced.
- Manual factors are applied after automatic resolution. Audit records retain
  the automatic value, effective value, exact override scope, supporting history
  and evidence fallback level. Manual edits do not increase evidence confidence.
- Unknown lineage keeps global factors. A known cell can have a manual edit
  even when its automatic result falls back globally; it still has global
  evidence and zero spatial confidence. Dry-plant regression remains fixed.
- Source confidence and overall confidence are weighted by physical WMT. AMT
  inventory headers are not counted again alongside their chunks. Previewing
  does not write grades, change chunk membership, or write a database.
- Confidence is the existing diagnostic of composition and spatial-address
  overlap. Uncertainty is its complement, not a statistical confidence interval.
  Latest campaign still means consecutive production dates; a date gap ends it.

## Review, reports and saved state

The source tree shows confidence, uncertainty, global-evidence share, lineage
coverage and selected fallback levels. Component details show both factor maps,
matched address, manual edits, production dates, period feed WMT, history rows,
and fallback reasons. Missing lineage and unavailable history are disclosed.
Very small nonzero percentages are labelled with an inequality instead of zero.

Database View exposes per-brand confidence, uncertainty, fallback levels,
global-evidence share and manual-edit share as default fields for inventory and
AMT chunk sources. Lineage coverage is also available with coverage fields.
APS grade blocks are excluded. **Export review CSV** produces one row per
reviewed source/chunk plus an overall row, retaining full numeric precision,
the selected reconciliation method, fallback levels and warnings.

Local entries live in the optional `reconciliation_settings.cells` list. Existing
scenario snapshots and `.prj` serialization already persist this dictionary and
the cached reconciliation inputs. Older settings without local entries continue
to load. Widget hydration blocks change signals during scenario/project restore.

Changing controls or effective global factors invalidates the displayed review,
CSV export and Submit. A fresh review must match current settings and sources
before submission. Warehouse workers use a frozen request context, and stale
responses from a different context are discarded. Existing displayed global
factor rounding no longer silently changes full-precision effective values.

## Validation

- Full unittest suite: **606 tests passed**, including **29 new Task 8 tests**.
  The two existing pandas warnings remain in CaseModeller and DrawCharts.
- Tests cover local scope, shared fallback, all three window modes, wider local
  history, guardrail exhaustion, weighted factor application, manual provenance,
  reset to inheritance, JSON/pickle persistence, scenario snapshots, native Qt
  controls and filtering, CSV export, submission gating, frozen worker inputs,
  stale responses and Database View fields.
- Native Qt screens were rendered and visually inspected for Standard,
  advanced source review and the local matrix. The offscreen Windows renderer
  required explicitly loading the installed Segoe UI fonts for visible text.
- Reused the Task 7 read-only warehouse extract locally: BIG01 inventory plus
  KAN82 AMT as one chunk spanning 44 positive hexes. The review retained
  **129,904.43232 WMT** and **15.487374912%** overall confidence, and calculated
  in approximately **0.64 seconds**, excluding warehouse reads and rendering.
- Validation did not modify the user's running application or production data.
  Database persistence tests used temporary databases; CSV tests used temporary
  files. No new warehouse queries were needed for the Task 8 visual check.

Task 8 is complete. Task 9 has not started. Changes are not committed.
