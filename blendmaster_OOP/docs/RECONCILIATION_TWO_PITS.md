# Two pits, one stockpile: advanced reconciliation worked example

This example follows **the same source and the same eight historical shifts**
through the current reconciliation engine. All numbers are illustrative and were
reproduced on 7 September 2026 using the [offline example](examples/reconciliation_two_pits.py).
It replaces the earlier one-pit example. No reconciliation behavior was changed.

## 1. Separate the decisions shown in the UI

| UI field | What it answers |
| --- | --- |
| **Source / component** | What physical material is being adjusted? |
| **Method** | Which selection procedure runs? |
| **Default window**, **N days**, **Maximum lookback (calendar days)** | Which historical dates can be considered? |
| **Minimum production days** | How many distinct eligible production dates are required? |
| **Fallback level** | How much of each component's spatial address must match? |
| **History selection** | Are shifts selected per component, or shared across the whole source? |
| **Evidence match score** | How closely does the selected historical feed represent the whole source? |
| **Auto blend**, **Auto regression** | What numerical correction factors were calculated from those shifts? |

**Auto blend** and **Auto regression** mean automatically calculated factors in
any advanced Method. The column names do not identify the selected Method.
**History selection** is shown as a result column for **Auto · maximise evidence
match score**; its values are **Component-based** and **Shared history**.

## 2. Set up the source once

There is one OPF, **CB OPF**, one brand, **SF**, and one inventory source,
**Stockpile 1**, containing **1,000 WMT**. Its two components are:

| Component | WMT | Source share | Pit | Stage | Bench | Blast | Flitch | Grade block |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |
| **Pit A HG** | 500 | 50% | Pit A | 1 | 100 | 10 | 1 | HG01 |
| **Pit B BA** | 500 | 50% | Pit B | 1 | 100 | 10 | 1 | BA01 |

We use **Pit A HG** and **Pit B BA** consistently to mean these two source
components. Their full addresses differ because their pits differ, even though
the remaining address numbers happen to be the same.

| Input | Fixed value |
| --- | --- |
| Scenario start | 11 August 2026, 00:00 AWST. |
| N days | 4 when Method is Advanced · lookback window. |
| Minimum production days | 2. |
| Maximum lookback (calendar days) | 10: history is bounded to 1–10 August here. |
| Modelled ROM Fe / Modelled Product Fe | 50% / 60%. |
| Lineage | 100%: every source tonne has a known grade-block origin. |
| Local factors and windows | No local settings initially. |
| Supplied Global factors | 1.00 for all ten series, solely to simplify this example. |

The engine receives existing Global factors; it does not invent 1.00 on fallback.
Every historical shift below has valid positive values for both blend and
regression for **Fe, SiO₂, Al₂O₃, P and Mn**: ten factor series. The ten series
have separate numerical values.

## 3. Understand the Fallback level before looking at dates

| Fallback level | Parts that must match the source component | Additional history it can admit |
| --- | --- | --- |
| **Flitch + material** | Pit + stage + bench + blast + flitch + material type. | Another HG grade block in the same full flitch address as Pit A HG. |
| **Blast + material** | Pit + stage + bench + blast + material type. | A different flitch in the same blast. |
| **Bench + material** | Pit + stage + bench + material type. | A different blast in the same bench. |
| **Stage + material** | Pit + stage + material type. | A different bench in the same stage. |
| **Pit + material** | Pit + material type. | A different stage in the same pit. |
| **Global** | Use the supplied OPF/brand/analyte factors when no spatial level qualifies. | Terminal factor fallback; it is not another spatial match. |

**Every spatial Fallback level retains the pit and material type.** Pit A BA
never becomes eligible evidence for Pit B BA. Pit B HG never becomes eligible
evidence for Pit A HG. A whole shift may contain both pits and qualify for both
source components.

“Material” means HG or BA. HG01 and HG02 at the same full address both meet
Flitch + material eligibility; their exact identities still differ when scoring.

## 4. Introduce the eight shifts

All shifts are for CB OPF / SF. Historical feed contains Pit A HG, Pit B BA and,
in two shifts, **Pit A BA**. Pit A BA uses one of our two material types but is
absent from Stockpile 1. Its tonnes remain part of the whole historical feed.

| Shift | Date | Total shift-feed WMT | Pit A HG | Pit B BA | Pit A BA | Address difference for Pit A HG / Pit B BA | Evidence match score | Blend Fe factor |
| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| **1** | 1 Aug | 100 | 50% | 50% | 0% | Stage 2 instead of 1: first eligible at **Pit + material**. | 16.67% | 0.90 |
| **2** | 2 Aug | 100 | 50% | 50% | 0% | Bench 200 instead of 100: first eligible at **Stage + material**. | 33.33% | 0.95 |
| **3** | 3 Aug | 100 | 50% | 50% | 0% | Blast 20 instead of 10: first eligible at **Bench + material**. | 50.00% | 1.00 |
| **4** | 4 Aug | 100 | 50% | 50% | 0% | Flitch 2 instead of 1: first eligible at **Blast + material**. | 66.67% | 1.10 |
| **5** | 4 Aug | 300 | 100% | 0% | 0% | Exact Pit A HG grade block. | 50.00% | 1.20 |
| **6** | 7 Aug | 100 | 10% | 10% | 80% | Exact source grade blocks for the 10% + 10%. | 20.00% | 1.15 |
| **7** | 9 Aug | 100 | 0% | 100% | 0% | Exact Pit B BA grade block. | 50.00% | 1.05 |
| **8** | 10 Aug | 50 | 30% | 30% | 40% | Flitch 2 instead of 1: first eligible at **Blast + material**. | 40.00% | 1.10 |

Unmentioned address parts stay as in the source table. Shifts 4 and 5 are two
non-overlapping six-hour shifts within 4 August; the others run 06:00–18:00.
There is no production on 5, 6 or 8 August in this synthetic history. Real history
retains its actual shift times. **Shifts 4 and 5 count as one production day.**

These are already-calculated **whole-shift factors**, derived from historical
grades and assays. For example, a blend factor of 1.10 can arise from a
back-calculated ROM grade of 55 divided by a modelled ROM grade of 50. The
regression factor reconciles actual versus modelled upgrading. Selecting a
shift does not recalculate its factors from only its matching component tonnes.

### Where the Evidence match scores come from

Each shift's **whole feed** is compared with the source's **whole 50% Pit A HG /
50% Pit B BA composition**. At each resolution, overlap uses the smaller source
and historical proportion for every matching group. The final score averages
overlap across six fixed resolutions:

| Shift | Exact grade block | Flitch + material | Blast + material | Bench + material | Stage + material | Pit + material | Average |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 0% | 0% | 0% | 100% | 100% | 100% | **50.00%** |
| 4 | 0% | 0% | 100% | 100% | 100% | 100% | **66.67%** |
| 5 | 50% | 50% | 50% | 50% | 50% | 50% | **50.00%** |
| 6 | 20% | 20% | 20% | 20% | 20% | 20% | **20.00%** |
| 8 | 0% | 0% | 60% | 60% | 60% | 60% | **40.00%** |

For Shift 6, `min(50%, 10%) + min(50%, 10%) = 20%` at every resolution.
Its 80% Pit A BA cannot match either source component. For Shift 8, overlap is
60% at four resolutions: `(0 + 0 + 60 + 60 + 60 + 60)/6 = 40%`.

**Exact grade block is a scoring resolution, not an extra Fallback level.**
All six resolutions are always scored. Choosing Bench + material does not
discard the finer resolutions from the score or subtract an additional penalty.
Shift 4 keeps its 66.67% score at every spatial level that admits it.

Shift 4 illustrates the tradeoff: its different flitch loses some address
overlap, but its 50/50 composition gives it a stronger whole-source match than
the exact-address, single-component Shift 5.

### Eligibility and Evidence match score answer different questions

A shift first needs the applicable OPF/brand, positive feed, usable lineage,
valid timing within the applicable window, and valid positive factors.

| History selection | Additional requirement inside each selected shift |
| --- | --- |
| **Component-based** | For Pit A HG, require positive Pit A HG feed at the tested Fallback level. Resolve Pit B BA separately. Each component needs one level with enough valid dates for all ten series. |
| **Shared history** | Require both Pit A HG and Pit B BA at one common Fallback level, with all ten valid factors. One set of shifts supplies both components and all ten series. |

There is no minimum matching percentage. Shift 6 can qualify for Shared history
despite its poor 20% score. Shifts 5 and 7 cannot qualify for Shared history at
any spatial level here because each lacks one source component.

Each shift is scored individually before averaging. Pooling Shifts 5 and 7
does not make either shift contain both source components; their WMT-weighted
Evidence match score remains 50%.

In real data, missing factors can force fallback despite enough apparent dates.
Component-based series may use different valid shifts; the component's reported
score is the lowest of its ten series scores. Shared history requires complete
factors on every selected shift. Our eight shifts have complete factors to keep
the following arithmetic focused on dates, composition and spatial address.

## 5. Advanced · lookback window: run all three Default window selections

Keep **N days = 4**, **Minimum production days = 2**, and **Maximum lookback
(calendar days) = 10**. Default window selects dates for CB OPF / SF before
component eligibility is checked.

| Default window | What N days counts | Dates selected here | Available shifts before component eligibility |
| --- | --- | --- | --- |
| **Trailing calendar days** | Four immediately preceding calendar dates. | 7–10 Aug; the non-producing 8th occupies a date. | 6, 7, 8. |
| **Last N production days** | Four most recent brand-production dates, skipping gaps. | 4, 7, 9, 10 Aug. | 4, 5, 6, 7, 8. |
| **Last N days of latest campaign** | Up to four final dates of the latest consecutive run of brand production. | 9, 10 Aug. The gap on the 8th ends the previous campaign. | 7, 8. |

The latest campaign has two dates and can still satisfy the minimum of two.
N = 4 cannot borrow dates from an older campaign. Trailing calendar days is
anchored to scenario start; it does not find a movable four-day interval within
the ten-day maximum.

This Method uses **Component-based** selection: take **all eligible shifts**
at the first Fallback level providing two valid production dates for all ten
series, independently for each component.

| Default window | Pit A HG: selected Fallback level and shifts | Pit B BA: selected Fallback level and shifts |
| --- | --- | --- |
| Trailing calendar days | **Blast + material: 6, 8.** Flitch + material has only Shift 6, so it lacks a second date. | **Flitch + material: 6, 7.** Two dates already qualify. |
| Last N production days | **Flitch + material: 5, 6.** Two dates: 4 and 7 Aug. | **Flitch + material: 6, 7.** Two dates: 7 and 9 Aug. |
| Last N days of latest campaign | **Global.** Only Shift 8 matches Pit A HG, even after trying every spatial level. One date is insufficient. | **Blast + material: 7, 8.** Flitch + material has only Shift 7. |

In Last N production days, Shift 4 is on an allowed date but its flitch differs;
both components already have sufficient Flitch + material evidence. In Last N
days of latest campaign, fallback cannot reach back across the campaign gap to
borrow Shift 4 for Pit A HG.

### Calculate the source result using two WMT weightings

**First use total shift-feed WMT** to average factors and scores within a
component. For Pit A HG under Trailing calendar days, Shifts 6 and 8 give:

- Auto blend Fe = `(100 × 1.15 + 50 × 1.10)/150 = 1.133333`.
- Evidence match score = `(100 × 20% + 50 × 40%)/150 = 26.666667%`.

Those shifts contain only 10 and 15 WMT of Pit A HG. The weights remain **100 and
50 WMT** because their factors represent the whole shift.

**Then use source WMT shares** to combine the components. Stockpile 1's 500/500
WMT gives weights of **50% and 50%**, regardless of historical feed quantities.

| Default window | Pit A HG Auto blend Fe | Pit B BA Auto blend Fe | Source blend Fe factor | Adjusted ROM Fe | Source Evidence match score | Global evidence |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Trailing calendar days | 1.133333 | 1.100000 | 1.116667 | **55.83%** | **30.83%** | 0% |
| Last N production days | 1.187500 | 1.100000 | 1.143750 | **57.19%** | **38.75%** | 0% |
| Last N days of latest campaign | Global 1.000000 | 1.066667 | 1.033333 | **51.67%** | **23.33%** | 50% |

For the first row, source factor = `0.5 × 1.133333 + 0.5 × 1.10 = 1.116667`;
Adjusted ROM Fe = `50% × 1.116667 = 55.83%`.
Source Evidence match score = `0.5 × 26.666667% + 0.5 × 35% = 30.83%`.

For the last row, Global supplies a factor but contributes zero spatial
Evidence match score: `0.5 × 0% + 0.5 × 46.666667% = 23.33%`. Lineage remains
100%: Pit A HG's origin is known, but sufficient eligible history is unavailable.

## 6. Advanced · spatial and compositional on the same shifts

**Default window** and **N days** are inactive. This Method searches within
Maximum lookback = 10, resolves each component at its **first sufficient
Fallback level**, and ranks eligible shifts by whole-source Evidence match
score. It keeps the highest-ranked shifts needed for two distinct dates,
including all ties at the cutoff.

| Component | Fallback level | Selected shifts | Component Evidence match score |
| --- | --- | --- | ---: |
| Pit A HG | Flitch + material | **5, 6**: both are needed for two dates. | `(300 × 50% + 100 × 20%)/400 = 42.50%` |
| Pit B BA | Flitch + material | **6, 7**: both are needed for two dates. | `(100 × 20% + 100 × 50%)/200 = 35.00%` |

Source Evidence match score is **38.75%** and Adjusted ROM Fe is **57.19%**.
These equal Last N production days here because the selected shifts coincide;
the selection procedures are different.

Both components have enough Flitch + material evidence, so this Method stops
there. It does not try broader levels to admit Shifts 3 or 4 merely to improve
the score. Meeting Minimum production days establishes sufficiency, not a
strong match.

## 7. Auto · maximise evidence match score: continue the comparison

Auto varies supported windows and their N, compares all five spatial Fallback
levels even when finer evidence is sufficient, and compares **Component-based**
with **Shared history**. One combination wins for Stockpile 1 and SF. The
objective is Evidence match score, not Fe grade, factor size or closeness to a
Product Target.

### Follow Shared history through every Fallback level

Within the full ten-day search, the spatial selection rule ranks individually
scored eligible shifts and selects the best shifts needed for two dates.

| Fallback level tested | Eligible Shared history shifts | Selected shifts | Evidence match score | Why it wins or loses |
| --- | --- | --- | ---: | --- |
| Flitch + material | 6. | None. | Insufficient. | Only one production date. |
| Blast + material | 4, 6, 8. | **4, 8**. | **57.78%** | Eligible; Auto still compares broader levels. |
| Bench + material | 3, 4, 6, 8. | **3, 4**. | **58.33%** | Best score, preferred level. |
| Stage + material | 2, 3, 4, 6, 8. | **3, 4**. | **58.33%** | Shift 2 is eligible but below the selected score cutoff. |
| Pit + material | 1, 2, 3, 4, 6, 8. | **3, 4**. | **58.33%** | Shift 1 is also below the selected score cutoff. |

At Blast + material, `(100 × 66.666667% + 50 × 40%)/150 = 57.78%`.
At Bench + material, `(100 × 50% + 100 × 66.666667%)/200 = 58.33%`.
Bench + material wins the tie with Stage + material and Pit + material because
it retains more address detail. A broader level can admit more shifts without
requiring all of them to be selected under the spatial selection rule.

### Why Shared history wins over Component-based

The best Component-based alternative uses different levels for the components:

| Component | Best Fallback level | Selected shifts | Component Evidence match score |
| --- | --- | --- | ---: |
| Pit A HG | Bench + material | **3, 4, 5**. Shift 5 ties Shift 3 at the score cutoff and must be included. | `(100 × 50% + 100 × 66.666667% + 300 × 50%)/500 = 53.33%` |
| Pit B BA | Blast + material | **4, 7**. | **58.33%** |

Its source score is `0.5 × 53.333333% + 0.5 × 58.333333% = 55.83%`.

Shared history excludes Shift 5 because Pit B BA is absent and excludes Shift 7
because Pit A HG is absent. Shifts **3 and 4** each contain the whole 50/50
mixture at Bench + material, have all ten valid factors and cover two distinct
dates. **Shared history wins at 58.33%.**

### Auto also tries the three Default window selections

The shorter **Selected window** labels here are the UI's output labels. They
identify the chosen date-selection rule, separately from Fallback level.

| Rule tested by Auto | Best Selected window | Best History selection | Source Evidence match score |
| --- | --- | --- | ---: |
| Spatial selection with every Fallback level compared | **Spatial · 10 days max** | Shared history | **58.33%** |
| Trailing calendar days | **Calendar · 8 days** | Component-based | **46.55%** |
| Last N production days | **Production days · 5 days** | Component-based | **46.55%** |
| Last N days of latest campaign | **Latest campaign · 2 days** | Component-based | **23.33%** |

Calendar · 8 days includes 3–10 August. Production days · 5 days includes
3, 4, 7, 9 and 10 August: the same producing shifts here. These Auto results
differ from manual N = 4 because Auto chooses N and the best Fallback levels.

The winner is **Spatial · 10 days max / Shared history / Bench + material**.
“10 days max” is a search limit; only **two production dates and two shifts**,
3 and 4 August, were selected. Auto compares supported window and score-cutoff
selections; it does not enumerate arbitrary hand-picked subsets of shifts.

## 8. Turn the winning shifts into ten factors and adjusted grades

Shifts 3 and 4 each feed 100 WMT, so the selected factors are simple averages
in this particular case:

| Analyte | Shift 3 blend | Shift 4 blend | **Auto blend** | Shift 3 regression | Shift 4 regression | **Auto regression** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fe | 1.00 | 1.10 | **1.05** | 0.90 | 1.00 | **0.95** |
| SiO₂ | 0.90 | 1.00 | **0.95** | 1.00 | 1.10 | **1.05** |
| Al₂O₃ | 0.95 | 1.05 | **1.00** | 1.05 | 1.15 | **1.10** |
| P | 1.10 | 1.20 | **1.15** | 0.90 | 1.00 | **0.95** |
| Mn | 0.80 | 0.90 | **0.85** | 1.10 | 1.20 | **1.15** |

Both source components receive the same automatic factors because they use the
same shifts and WMT weights. That does not mean all ten series have the same
value. Another source can select different shifts; identical factors across
sources are also legitimate when their selected shifts and weights coincide.

- Source blend Fe = `0.5 × 1.05 + 0.5 × 1.05 = 1.05`.
- **Adjusted ROM Fe** = `50% × 1.05 = 52.50%`.
- Source regression Fe = `0.5 × 0.95 + 0.5 × 0.95 = 0.95`.
- **Adjusted Product Fe** = `60% × 0.95 = 57.00%` for this CB OPF example.

Evidence match score is not multiplied into a grade or factor. It measures
evidence relevance, not a probability that the adjusted grade will be correct.

## 9. Change one input at a time

| Input | Advanced · lookback window | Advanced · spatial and compositional | Auto · maximise evidence match score |
| --- | --- | --- | --- |
| **Default window** | Chooses one of the three date-selection rules. | Inactive. | Inactive; Auto compares the three rules and spatial selection. |
| **N days** | Counts dates according to Default window. | Inactive. | Inactive as a manual input; Auto chooses N or a spatial horizon. |
| **Minimum production days** | May require broader Fallback levels within the existing window. | May require lower-ranked shifts or broader Fallback levels. | Applies to every candidate and can change any part of the winning selection. |
| **Maximum lookback (calendar days)** | Caps history; cannot move the anchored calendar window or bridge a campaign gap. | Bounds the entire search. | Bounds the search, with applicable local settings also respected. |

Raising N from 4 to 8 in **Trailing calendar days** admits 3–10 August. In
**Last N production days**, it admits the seven available production dates within
the maximum. In **Last N days of latest campaign**, it still admits only 9–10
August. Raising Maximum lookback alone does not expand Trailing calendar days
while N remains 4. A window can use fewer than N producing dates if the minimum
is met; the minimum never allows crossing its maximum or campaign boundary.

Changing only **Minimum production days** at the top of the review gives these Auto results:

| Minimum production days | History selection | Selected shifts | Evidence match score | Explanation |
| ---: | --- | --- | ---: | --- |
| **1** | Component-based | 4 for both components. | **66.67%** | Shared history selects the same evidence. All remaining tie-breaks also tie, so Component-based wins the label. |
| **2** | Shared history | 3, 4 for both components. | **58.33%** | The second required date exposes the Component-based disadvantage above. |
| **3** | Shared history | 3, 4, 8 for both components. | **54.67%** | Shift 8 adds a third date and 50 WMT of lower-scoring evidence. |

A smaller minimum permits a smaller sample here. More generally, score ties,
missing factors and local settings can change how many shifts and how much WMT
are selected. Minimum production days does not force either History selection.
Several shifts on one date cannot substitute for a second required date.

All shifts must finish by scenario start. Trailing calendar days excludes the
scenario-start date; the other rules can use already-completed shifts on it.
Starting this example at midnight keeps that distinction out of the arithmetic.

## 10. Local factors and windows: inspect, then optionally edit

This tab shows the automatic factors and source/hex context as well as allowing
edits. Local settings belong to **OPF + brand + full spatial/material cell +
analyte**, across all sources using that cell. They are not confined to the
selected grade-block suffix or hex.

**Local blend factor** and **Local regression factor** change the applied values
after historical selection. For example, set Pit A HG's Fe Local blend factor
to **1.20**. Its Auto blend remains **1.05**, and Pit B BA still uses **1.05**.
Source blend Fe becomes `0.5 × 1.20 + 0.5 × 1.05 = 1.125`, so Adjusted ROM Fe
becomes **56.25%**. Evidence match score stays **58.33%**; **manual edits = 50%**
because the edited component contains half the source WMT.

**Local windows** change eligible history before selection. In Advanced ·
lookback window they can replace the inherited window rule, N, minimum and
maximum for that cell/analyte. In Advanced · spatial and compositional, minimum
and maximum control the search. In Auto, **Set local guardrails for this
analyte** uses local minimum and maximum; the date rule and N remain Auto's
choices. A local value replaces that cell/analyte's default.

Shared history must satisfy all applicable local settings together: the
strictest minimum and the intersection of allowed history, including the
tightest maximum. For example, change **only Pit A HG's Fe minimum to 3**.
Shared history now requires three dates and scores at most **54.67%**. The best
Component-based alternative can still use two dates for Pit B BA and wins at
**55.23%**. This variation shows why a higher minimum does not always make Shared
history win.

Use **Save local settings**, then **Calculate review**, and **Submit** through
the existing workflow. **Use inherited settings** removes that analyte's local
factors and window so the defaults apply again.

## 11. Read the final result in the UI

With the original inputs and no local edits:

| UI result | Value | Meaning |
| --- | --- | --- |
| Method | **Auto · maximise evidence match score** | The requested search procedure. |
| Selected window | **Spatial · 10 days max** | Ten-day search bound; only Shifts 3 and 4 selected. |
| History selection | **Shared history** | Both components and all ten series use those shifts. |
| Fallback level | **Bench + material** | Match each component's own pit, stage, bench and material type. |
| Evidence match score | **58.3%** displayed; 58.333333% before rounding. | How the selected evidence represents Stockpile 1. |
| Spatial baseline match score | **38.8%** displayed; 38.75% before rounding. | Ordinary Advanced · spatial and compositional at its first sufficient levels. |
| Improvement | **19.58 percentage points** | 58.333333 minus 38.75, not a relative percentage increase. |
| Global evidence | **0%** | No source WMT uses Global fallback evidence. |
| Lineage | **100%** | Every source tonne has a usable grade-block origin. |
| manual edits | **0%** | No component has an edited factor. |

These percentages measure different things and do not add up to 100%. As a
separate variation, suppose only 400 WMT Pit A HG + 400 WMT Pit B BA are known,
with 200 WMT of unknown origin. The known 80% still selects Shifts 3 and 4;
Lineage is 80%, Global evidence 20%, and source Evidence match score is
`0.8 × 58.333333% = 46.67%`. Unknown source WMT stays in the denominator and
retains supplied Global factors.

For AMT, apply this example to **one hex**. Selection and grade adjustment happen
before AMT chunks exist. Footprint previews and later chunks aggregate hex
evidence using physical WMT; chunk grades retain the existing grade-field
weighting. Chunking does not rerun the historical selection. With this one
inventory source, the overall Evidence match score equals Stockpile 1's score;
with more sources it is physical-source-WMT weighted.

The separate **Standard · global factors** Method retains the existing global
7/14/21/28/30-day search. Its advanced window inputs are inactive; it does not
perform the component or Shared history comparisons in this example.

## Reproduce the numbers

From `C:\BlendMaster\blendmaster_OOP`:

```text
python docs/examples/reconciliation_two_pits.py
```

The script supplies the synthetic history to the current resolver and grade
application engine, checks the principal scores, selected shifts, ten factors
and adjusted grades against the worked arithmetic, and prints Auto's window
and level comparisons. It uses no warehouse or database access.
