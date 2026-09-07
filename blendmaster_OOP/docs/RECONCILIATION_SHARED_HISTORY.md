# Auto reconciliation: shared whole-source history

Implemented 7 September 2026 following the user's approval, after Task 11.
This extends Auto only. It adds a competing shared-history candidate alongside
the existing component-based candidate. No new user inputs are required.

## Updated mental map

**Source makeup → eligible shifts → approach, window and spatial level →
feed-weighted factors → manual edits → source adjustment → chunk/overall review.**

| Behaviour/input | Lookback | Spatial and compositional | Auto |
| --- | --- | --- | --- |
| Question answered | What matching history occurred in this time window? | Which eligible shifts best represent this whole source? | Which supported approach, window and spatial levels give the highest source score? |
| Shift selection | All spatially eligible, valid shifts in the chosen time window. | Rank eligible shifts by whole-source match; retain the highest-score prefix meeting minimum dates, including all cutoff ties. | Compare both selection rules, using component-based and shared-history candidates. |
| N days / window dropdown | You choose Calendar N, Production N or Latest campaign N. | Inactive. Maximum lookback bounds the search. | Inactive as a manual input. Auto chooses N, the temporal family and Spatial horizon. |
| Minimum production days | Each of ten series must have enough distinct valid dates. | Determines how far down the match ranking selection must reach. | Required in every candidate. Shared history uses the strictest local minimum across the known components and analytes. |
| Maximum lookback | Hard calendar-age cap, including on Production/Campaign N. | Full search horizon; selected dates can be scattered within it. | Caps every candidate. Shared history respects every local cap, hence the tightest one. |
| Spatial fallback | First sufficient level per component. | First sufficient level per component. | All five levels compete even when fine evidence is sufficient. Component-based can use different levels/sets per component; shared history uses one common level and set. |
| Unit of selection | Component factors combine into a stockpile/hex. | Same, with the whole source as each shift's match reference. | One winning approach/method/window per physical inventory stockpile or AMT hex, and brand. |
| No eligible history | Supplied standard global factors. | Supplied standard global factors. | Shared history may be unavailable while component-based remains usable. Global is terminal when no spatial history qualifies for a component. |

Standard global mode remains available and unchanged.

**Calendar N** uses the N completed calendar dates immediately before the scenario
start date, clipped by maximum lookback. **Production N** uses the most recent N
dates with that brand's production inside the cap, allowing date gaps. **Latest
campaign N** uses the final N dates of the latest consecutive brand-production
campaign; a date gap ends that campaign. Spatial can also use a completed shift
on the scenario-start date. All history must finish by scenario start.

For Calendar N=7, minimum=3 and maximum=30, the window remains the anchored prior
seven calendar dates, with at least three distinct qualifying production dates.
Those dates need not be consecutive. It does not search for a movable seven-day
interval somewhere in the preceding 30 days. Insufficiency changes the spatial
level, not the fixed Calendar window. Auto can choose a different supported N.

Lowering the minimum permits a smaller evidence sample and can raise the match
score; it does not guarantee fewer shifts or less feed, because ties can retain
additional shifts/dates and Auto can select a different candidate. Multiple
shifts on one date count as one production day. Three production dates alone
cannot guarantee ten usable series if factors are missing or invalid.

## Eligibility, then quality

All candidates use validated paired blend/regression history for the applicable
OPF and brand, positive physical shift feed, valid timing and lineage, and
applicable window bounds. Factors must be finite and positive.

- **Component-based:** a shift needs positive feed from that component's
  address/material group at the tested level. One spatial level must support
  both kinds and all five analytes. Series may use different valid shifts when
  values or local bounds differ.
- **Shared history:** every selected shift must contain every known source
  address/material group at one common tested level, and have all ten valid
  factors. The intersection of all applicable local windows is used. One common
  set supplies both kinds and all five analytes for all known source components.
- There is no minimum percentage for a matching group to make a shift eligible.
  Tiny positive presence qualifies; weak proportions reduce the match score.
  There is also no newly introduced minimum match-score input.

The fallback ladder is **Flitch → Blast → Bench → Stage → Pit**, always retaining
the parent address parts and material type. HG25 and HG28, for example, share HG
for spatial eligibility; their exact grade-block identities still differ in
the score. Broadening drops address detail, so additional shifts may qualify.

Ordinary Spatial/Lookback broaden only when a level cannot supply enough valid
production dates for all ten series. Auto tests broader levels anyway. In the
shared candidate, a fine level may fail because shifts do not contain all source
groups together, because complete factor series are missing, or because common
dates inside all bounds are insufficient. A coarser eligible candidate wins only
when its rank beats the alternatives; broadening never relaxes minimum/maximum
bounds, material type or factor validity.

## Each shift represents the source individually

For a source with 50% HG and 50% BA, each shift's entire feed is compared with
that whole mixture. The score averages histogram overlap at six fixed
resolutions: exact grade block and the five spatial/material levels above.
Unrelated and unattributed historical feed reduce overlap.

| Historical shift (all lineage known) | Match score against the 50/50 source |
| --- | ---: |
| 50/50 HG/BA, same exact blocks | 100% |
| 80/20 HG/BA, same exact blocks | 70% |
| 50/50 HG/BA, different flitches in the same blast | 66.67% |
| 80/20 HG/BA, different flitches in the same blast | 46.67% |

No extra score penalty is subtracted just because Auto selected a coarser level.
Lost address overlap already reduces the fixed score. A coarser level can still
produce a higher result by admitting a much better mixture. Specificity resolves
score ties, following the preference for less global evidence.

One 100% HG shift and one 100% BA shift do **not** become a 100% match by pooling
them. Each individually scores 50% against the 50/50 target (same exact addresses).
Their feed-weighted average remains 50%. Neither supplies both required material
groups, so they cannot form the new shared set; component-based evidence may
still use them.

For each series, the selected shifts' scores and factors are averaged using
**total shift-feed WMT**, not just matching tonnes. The score does not multiply
the factors. Component-based evidence uses the lowest score across its ten
series. A shared set has the same score for every series because membership and
weights are common; each series still has its own factor values. Physical source
WMT fractions then combine component results. Unknown source lineage retains
global factors and zero score, reducing the source score exactly once.

Auto maximises within the supported window/level/selection rules. It does not
enumerate arbitrary combinations of shifts or establish prediction accuracy.
Each shift is scored individually, but a minimum-date requirement can still
force inclusion of a weakly matching shift.

## Numerical example where the addition helps

Target: 50/50 HG/BA at the same exact addresses. Minimum: two production dates.

| Shift | Feed WMT | Makeup | Match | Blend Fe factor |
| --- | ---: | --- | ---: | ---: |
| 19 August, day | 10,000 | 50% HG, 50% BA | 100% | 1.1 |
| 19 August, night | 100,000 | 100% HG | 50% | 1.8 |
| 20 August, day | 1 | 20% HG, 20% BA, 60% unrelated feed | 40% | 1.4 |

Component-based HG evidence includes all three shifts to cover the required two
dates, while BA evidence uses the two mixed shifts. The whole-source score is
**77.2697%**. A calendar window cannot remove the pure shift while retaining the
two required dates. The shared candidate excludes it because BA is absent,
retains the two mixed shifts, and scores **99.9940%**. Auto selects shared history.

Both known components receive automatic Blend Fe =
`(10,000 × 1.1 + 1 × 1.4) / 10,001 = 1.10003`. The other nine series are calculated
from exactly those same shifts using their respective values. This demonstrates
the selection effect; it is a synthetic example, not a live-data gain claim.

## Repeated factors and local factors/windows

Repeated factors across grade blocks, hexes or stockpiles can be correct: they
have selected the same historical shift set and weights. Similar HG identities
at the same spatial address often have the same eligible pool. Source makeup
still determines ranking, but different makeups need not change the winner.
With shared history, identical automatic factors across known components of that
source are intentional. It does not force other sources to use that set.

**Local factors and windows** serves inspection as well as overrides. It shows
the automatic values, source/hex context, chosen history and fallback. Local
settings apply by OPF, brand, full spatial/material cell and analyte across sources,
not just the currently selected grade-block suffix or hex. In Auto, local minimum
and maximum remain constraints; Auto selects the temporal family and N. Manual
factor edits apply after selection to the original cell/analyte only and leave
the evidence score unchanged. Thus a manual edit can make applied factors differ
between components that share the automatic history.

## Review and compatibility

- A new Auto-only **History selection** column displays **Component-based** or
  **Shared history**; chunks/overall can contain both. Evidence details show
  best scores by approach, shared-set requirements/counts, and level comparisons.
  An ineligible shared candidate is explained rather than silently applied.
- **Selected window** identifies the winning temporal rule inside Auto. Spatial
  30 days max is a search horizon, not thirty used dates. Calendar 7 days is the
  anchored completed-date window. It links to the same selection rules as the
  methods dropdown, although Auto chooses between them separately for each source.
- The blue summary's **evidence match score** is physical-source-WMT weighted.
  **Global evidence** is the physical fraction using global fallback evidence;
  **lineage** is the fraction with usable grade-block attribution; **manual edits**
  is the fraction with an edited factor. These describe different things and do
  not sum to 100%. Uncertainty is not displayed.
- The baseline remains ordinary full-maximum Spatial with its first sufficient
  component levels. Gain is the score difference in percentage points; it may
  include changes in approach, temporal selection and levels. Best Component-based
  provides a separate comparison with the previous Auto search.
- Inventory stockpiles and AMT hexes are adjusted before chunking. Before chunks
  exist, footprint previews aggregate hex results. Chunks later inherit those
  hex results using physical WMT; they are not required to run the search.
- Audit fields include `history_approach`, `best_by_approach`,
  `shared_level_comparison`, and the winning `shared_history` counts/rules.
  Component provenance preserves the common set and `best_eligible_shared_level`.
  CSV/Database View gain `recon_<brand>_history_selection`.
- Search and reconciliation algorithm revisions are **4**. Derived caches
  invalidate; existing history can be reused. Old saved audits remain readable
  and use Component-based as the legacy approach label. Internal legacy score
  keys remain for compatibility; user-facing terminology is Evidence match score.
- Ties, rounded to ten decimal places, prefer less global evidence, more specific
  levels, more production dates, more period feed, then the established
  component-based approach and deterministic policy order.

Restart BlendMaster, **Calculate review**, then **Submit** through the existing
workflow to apply the new selection. No new configuration is needed.

## Validation

The 17 new tests cover shared gains, all-ten common membership and distinct
factor values, fine and broader level selection, coarser search despite adequate
fine evidence, eligibility without a percentage threshold, non-pooled mixtures,
nonconsecutive dates, missing factors, strict local bounds, manual overrides,
unknown mass, fallback, cache isolation, JSON persistence and application/UI/
CSV/Database View aggregation. An independent raw-shift oracle enumerates all
integer windows and five levels on 12 varied histories, checking the best shared
score and that overall Auto cannot lose to the retained component candidate.

The native Qt review was rendered with installed Segoe UI fonts and visually
checked. The full suite passes: **725 tests**. The two existing pandas warnings
remain in CaseModeller/DrawCharts. Test databases are temporary; no warehouse or
production database was written.

A synthetic timing check used 60 shifts, 45 hexes, three known components, 10%
unknown source WMT, minimum three dates and maximum 30 days (seed 92061). The
previous component-only Auto path took **4.46 s**; the added shared search took
**5.66 s**, about 27% longer. Both retained **54,900 WMT**, scored **84.8832%**, and
selected component-based history for all 45 hexes. This case has common complete
evidence, so the additional candidate gives no gain. These local timings do not
predict latency or score gains for the user's live dataset.
