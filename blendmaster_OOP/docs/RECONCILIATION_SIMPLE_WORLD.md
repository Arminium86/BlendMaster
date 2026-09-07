# One source, two materials: reconciliation from start to finish

This synthetic example was checked against the current reconciliation and
application engine on 7 September 2026. It expands the
[advanced reconciliation mental map](RECONCILIATION_SHARED_HISTORY.md).
No reconciliation calculation or UI behaviour was changed for this explanation.

## 1. Our small world

- One inventory stockpile: **1,000 WMT**, made of **500 WMT HG + 500 WMT BA**.
- One pit, **P1**, one OPF and one brand, **SF**.
- The source and historical HG/BA blocks have the same exact respective
  addresses, in the same stage, bench, blast and flitch. All lineage is known.
- The source's modelled ROM Fe is **50%**.
- Scenario start: **11 August 2026 at 00:00 AWST**.
- Lookback inputs: **N = 4**, **minimum production days = 2**,
  **maximum lookback = 10 calendar days**.
- No local overrides. The supplied standard global Blend Fe factor is **1.00**
  in this imaginary world. This is an explicitly supplied example value, not a
  factor invented by the fallback logic.

We show Blend Fe to keep the arithmetic short. Every historical shift has valid
values for all ten series (five blend and five regression); each series uses
its own factor values with the same selection rules.

## 2. Historical feed

Each producing date has one shift from 06:00 to 18:00, feeding **100 WMT**.
There is no brand production on the omitted dates within 1–10 August.

| August date | HG WMT | BA WMT | Whole-shift match to the 50/50 source | Blend Fe factor |
| --- | ---: | ---: | ---: | ---: |
| 3 | 70 | 30 | 80% | 0.90 |
| 4 | 50 | 50 | 100% | 1.00 |
| 7 | 60 | 40 | 90% | 1.10 |
| 9 | 100 | 0 | 50% | 1.20 |
| 10 | 80 | 20 | 70% | 1.30 |

Because the exact addresses match, all six score resolutions have the same
composition overlap. For 7 August: `min(50, 60) + min(50, 40) = 90%`.
For 9 August: `min(50, 100) + min(50, 0) = 50%`.

The score measures how the shift represents the source; a larger factor is not
a better match. Factors are historical inputs to reconciliation, not derived
from these composition percentages alone.

## 3. What the three Lookback windows admit

| Default window, N = 4 | Time pool | Producing dates available | Why |
| --- | --- | --- | --- |
| **Trailing calendar days** | 7–10 August | **7, 9, 10** | The four immediately preceding calendar dates include the non-producing 8th. |
| **Last N production days** | Last four brand-production dates within 1–10 August | **4, 7, 9, 10** | Skip non-production dates; the 3rd is the fifth-most-recent producing date. |
| **Last N days of latest campaign** | Latest consecutive brand-production run | **9, 10** | No production on the 8th breaks the campaign. The latest campaign has only two dates, so requesting four cannot pull in the 7th. |

**N defines the time pool. Minimum production days tests whether the evidence
inside it is sufficient. Maximum lookback limits how old any evidence can be.**

N=4 does not require four usable dates in Calendar or a four-date campaign.
Two usable dates can suffice here because the minimum is two. A shorter set in
Production mode can also suffice when fewer than N dates exist within its cap.
Multiple shifts on one date would still count as one production day.

## 4. Component eligibility inside those windows

Ordinary Lookback resolves HG and BA separately. A shift must contain positive
tonnage of the applicable spatial/material group. There is no minimum proportion
threshold, but all ten series must have sufficient valid production dates at one
spatial level.

| Window | HG evidence | BA evidence | Outcome |
| --- | --- | --- | --- |
| Calendar 4 | 7, 9, 10: three dates | 7, 10: two dates | Both qualify at Flitch + material. |
| Production 4 | 4, 7, 9, 10: four dates | 4, 7, 10: three dates | Both qualify at Flitch + material. |
| Latest campaign 4 | 9, 10: two dates | Only 10: one date | HG qualifies; BA cannot meet the minimum. |

For Latest campaign, BA tries Blast, Bench, Stage and Pit next. This world has no
additional BA history elsewhere in those levels during the campaign. BA therefore
retains its supplied global factor **1.00** and gets **zero evidence match score**.
The campaign is defined by brand production; it does not become an older campaign
simply because this source component lacks enough evidence in the latest one.

Spatial fallback broadens address eligibility inside the selected time pool.
It cannot go back across the 8 August campaign gap to borrow BA production.

## 5. Calculate factors and adjust the source

Historical factor averages use **total shift-feed WMT**. Every shift here feeds
100 WMT, so these are ordinary arithmetic averages, including for BA when BA
itself supplied fewer than 100 WMT of that shift.

The stockpile is 50/50, so its applied Blend Fe factor is
`0.5 × HG factor + 0.5 × BA factor`. Adjusted ROM Fe is then
`modelled ROM Fe × applied Blend Fe factor`.

| Lookback selection | HG factor | BA factor | Source factor | Adjusted ROM Fe | Source match score |
| --- | ---: | ---: | ---: | ---: | ---: |
| Calendar 4 | (1.10 + 1.20 + 1.30)/3 = **1.20** | (1.10 + 1.30)/2 = **1.20** | **1.20** | 50 × 1.20 = **60.00%** | **75.00%** |
| Production 4 | (1.00 + 1.10 + 1.20 + 1.30)/4 = **1.15** | (1.00 + 1.10 + 1.30)/3 = **1.1333** | **1.1417** | **57.08%** | **82.08%** |
| Latest campaign 4 | (1.20 + 1.30)/2 = **1.25** | Global **1.00** | **1.125** | **56.25%** | **30.00%** |

The Calendar score comes from:

- HG: `(90 + 50 + 70)/3 = 70%`.
- BA: `(90 + 70)/2 = 80%`.
- Source: `0.5 × 70 + 0.5 × 80 = 75%`.

The Latest campaign score is `0.5 × ((50 + 70)/2) + 0.5 × 0 = 30%`.
Its source lineage coverage is still 100%, while global evidence is 50%: knowing
where BA came from is different from having enough matching BA history.

Equal component factors do not prove equal evidence: Calendar HG and BA both
happen to average to 1.20, despite using different dates and having different
match scores. The match score does not multiply the grade or the factor.

## 6. Spatial on the same world

Spatial ignores manual N=4 and searches inside maximum lookback=10. It ranks
eligible shifts by whole-source match and retains the highest-match shifts
needed to cover minimum=2 distinct dates, including cutoff ties.

Both components choose **4 August (100%)** and **7 August (90%)**. These are two
nonconsecutive production dates, both containing HG and BA with valid factors.

- Blend Fe factor for both components: `(1.00 + 1.10)/2 = 1.05`.
- Applied source factor: `0.5 × 1.05 + 0.5 × 1.05 = 1.05`.
- Adjusted ROM Fe: `50% × 1.05 = 52.50%`.
- Evidence match score: `(100 + 90)/2 = 95%`.

Spatial selects relevant shifts rather than requiring them to belong to one
contiguous calendar block or the latest campaign. It does not select the largest
factors or the factors that make the grade closest to a product target.

## 7. Auto on the same world

Auto varies N and the temporal family within the maximum and tests every spatial
level. It compares both the component-based and shared-history candidates; it is
not restricted to the manual N=4 results above.

| Best Auto candidate by temporal family | N / horizon selected | Approach | Source score |
| --- | --- | --- | ---: |
| Spatial | 10 days max | Component-based (shared history ties) | **95.00%** |
| Calendar | 7 days | Shared history | **86.67%** |
| Production | 4 production days | Shared history | **86.67%** |
| Latest campaign | 2 days | Component-based, including BA global fallback | **30.00%** |

The shared Calendar/Production candidates use **4, 7, 10** for both components,
excluding the HG-only 9th because each shared shift must contain both materials.
Their score is `(100 + 90 + 70)/3 = 86.67%`. Ordinary Lookback remains component-based,
which explains why its Production 4 result above scores 82.08% instead.

Auto's overall winner is **Spatial, 10 days max, Component-based**. Its HG and BA
sets both happen to be **4 and 7 August**. Shared history proposes the same set
and ties at 95%; all remaining ranking criteria also tie, so the established
component-based approach wins the label. All levels expose the same evidence in
this world, so the finest level wins the specificity tie.

The resulting source has **52.50% adjusted ROM Fe**, **95% evidence match score**,
**100% lineage**, **0% global evidence**, and **0% manual edits**. Auto's gain over
the ordinary Spatial baseline is **0 percentage points** in this particular world.

## 8. Which input changes what?

- **Raise N in Lookback:** change the time pool. Calendar can reach older calendar
  dates, Production can include more producing dates, and Latest campaign can
  include more dates only if the current campaign contains them.
- **Raise minimum:** require more distinct valid dates. Lookback can then need
  broader spatial fallback or global factors. Spatial must include lower-match
  dates if its best dates are insufficient. Auto compares the newly feasible
  component-based and shared sets; either approach can win.
- **Raise maximum:** permit older history, but it does not move an anchored
  Calendar window or cross the latest-campaign boundary. It expands the search
  available to Spatial, Auto and Production dates when needed.
- **Introduce nearby blocks in the same pit:** broader spatial levels may add
  evidence. Ordinary Lookback/Spatial stop at the first sufficient level; Auto
  checks all five. The score still accounts for the poorer address match.
- **Edit a local factor:** change the applied value after selection; the evidence
  score remains the same. Local minimum/maximum settings can instead change which
  history qualifies. In Auto, a shared set must satisfy every applicable local bound.

All reported factors, selected dates, fallback outcomes, scores and adjusted
grades above were reproduced using the existing engine with these synthetic
inputs. This is an explanation of selection and arithmetic, not a forecast for
the user's production data.
