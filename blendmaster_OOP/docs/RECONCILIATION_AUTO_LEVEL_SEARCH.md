# Auto reconciliation: independent spatial-level search

Implemented 6 September 2026, following Task 11 and before Task 12.

Extended 7 September: Auto now also compares a common whole-source shift set
against these component-based candidates. See the current
[shared-history mental map and validation](RECONCILIATION_SHARED_HISTORY.md).
The implementation and validation below describe the preceding level-search step.

Auto previously tried each supported temporal policy but stopped each component
at its first sufficient spatial level. It now compares every eligible spatial
level within each temporal policy, including broader levels when a finer level
already has enough history. Existing cached history is reused; no new warehouse
query or source schema is required.

## Mental map

Source composition → eligible historical shifts → method/window and spatial
level selection → feed-weighted factors → local factor edits → source adjustment.

| Input or behaviour | Lookback | Spatial and compositional | Auto |
| --- | --- | --- | --- |
| Main question | What matching history occurred in my chosen time window? | Which eligible historical shifts best resemble this whole source? | Which supported method, window and component levels give the highest source score? |
| History used | All valid spatially eligible shifts inside the chosen window. | Highest-match shifts within the maximum horizon, retaining all cutoff ties. | Tries both selection rules across their supported whole-day windows. |
| N days | User chooses N and Calendar, Production days or Latest campaign. | The N control is inactive; maximum lookback bounds the search. | Auto chooses N and the window family/horizon; the saved manual N is inactive. |
| Minimum production days | Each of the ten series must have enough distinct dates in the selected window and level. | Select enough highest-match shifts to meet each series' minimum distinct dates. | Enforced for every component/level/window candidate; never relaxed to raise the score. |
| Maximum lookback | Caps how far history can reach, even when N requests more. | Defines the full search horizon. | Caps all candidate horizons/windows. Local caps still apply. |
| Spatial fallback | First sufficient level. | First sufficient level. | Compare all five levels; select the highest-scoring eligible level per component. |
| Granularity | Component factors combine by physical WMT into inventory/hex and brand. | Same; shift ranking compares the whole source composition. | One method/window policy per inventory/hex and brand, with independent component levels. |
| No qualifying spatial evidence | Standard global factors. | Standard global factors. | Standard global factors when no level qualifies; global is not a scored historical candidate. |

**Calendar N** means the N completed calendar dates immediately before scenario
start's date. **Production N** means the last N brand production dates within the
maximum lookback; gaps are allowed. **Latest campaign N** means the final N dates
of the latest consecutive brand-production campaign. The minimum counts distinct
dates, not shifts, and does not require consecutive dates except for campaign
membership. Calendar windows are anchored; Auto does not slide an arbitrary
N-day block around the history.

Local windows refine the applicable component/analyte settings. Auto retains
local minimum/maximum guardrails while choosing the family and N itself. Manual
local factors apply after selection and do not increase the score. The local
tab also inspects the selected evidence and automatic factors.

## Selection and score

The five levels retain all parent address parts and material type:
Flitch → Blast → Bench → Stage → Pit. The sixth, global step remains terminal.

At each candidate level, both factor kinds and all five analytes must have
sufficient valid positive factors. Spatial keeps its shared source-match cutoff
and all ties; Lookback retains all eligible shifts in its time window. Every
series is scored by the same period-feed-weighted whole-source similarity. The
component takes the lowest of its ten series scores. Component scores combine
using physical source WMT fractions, including zero score for unknown lineage.

No extra penalty is subtracted for selecting a broader spatial level. The same
six-resolution spatial/material/composition overlap metric evaluates every
candidate. A broader level can expose a better overall feed composition. Equal
scores prefer finer levels; policy ties retain the existing less-global,
more-specific, more-production-days, more-feed, deterministic-order preferences.
Scores are rounded to ten decimal places for ranking.

Levels can be optimized independently because their component WMT contributions
are fixed and additive. The method/window is still chosen for the whole source.
This avoids enumerating every combination of component levels. The search
covers the supported window policies and spatial ranking prefixes; it does not
enumerate arbitrary subsets of shifts.

## Review, baseline and saved results

- The unchanged ordinary full-maximum Spatial result is the comparison baseline,
  using its first sufficient level. Auto now searches all levels inside that
  same horizon too, so the displayed gain includes improvement due to level
  selection as well as temporal selection.
- Expand a source/hex to a component in **Sources and evidence**. Its details
  list all five levels for the selected method/window, scores and supporting
  production dates/feed for eligible levels, failure reasons for ineligible
  levels, and the winner. Source details retain best-by-window-family summaries
  and now include selected levels and the count of evaluated level candidates.
- The audit preserves `level_search` per component and `fallback_strategy`,
  selected levels and level candidate count in `auto_selection`. Existing
  selected-level, baseline and gain fields continue through chunk/overall
  aggregation, Database View and CSV exports.
- Auto search and reconciliation algorithm revisions are now 3. Level-specific
  selection cache entries cannot collide with ordinary first-sufficient entries.
  Derived application/review/AMT enrichment caches invalidate; history inputs
  remain reusable. Older saved audits remain readable.

## Validation

`python -m unittest discover -s tests`: **708 tests passed**, including 11 new
tests. These cover each broader level beating sufficient fine-level history,
all lookback families, specificity ties, the shared ten-series rule, local
bounds, source/cache separation, manual overrides, unknown mass and review/report
provenance. An independent ordinary-resolver replay exhaustively compares integer
windows and component-level combinations on six varied histories, then checks
the selected factors. Existing Spatial/Lookback, aggregation and cache tests pass.
The native Qt review was rendered and visually checked with the broader-level
winner selected, including its five-level comparison. Python compilation and
`git diff --check` passed.

A constructed example with weak fine-level evidence on the newest date and
stronger Blast evidence on an older date improves from **1.0%** ordinary Spatial
to **66.6667%** Auto. Every window reaching the older evidence also contains
sufficient fine-level evidence, so this specifically exercises the previous
early-stop limitation. These are synthetic scores, not a claim about live data.

Synthetic performance check (seed 92061): 60 shifts, 45 hexes, three known
components per hex, 10% unknown WMT, minimum 3 dates and maximum 30 days. An
emulation of previous first-sufficient Auto took **1.09 s**; all-level Auto took
**5.45 s**. Both scored **68.7360%**, with unchanged **54,900 WMT**. This deliberately
shared history supplies equivalent evidence at different levels, so additional
search produces no gain. Times are local measurements, not latency guarantees.

Restart BlendMaster and calculate the review again. Submit applies the newly
selected factors through the existing workflow. Task 12 has not started.
