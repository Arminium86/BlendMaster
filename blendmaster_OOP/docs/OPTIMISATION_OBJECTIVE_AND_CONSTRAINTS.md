# BlendMaster optimisation objective and constraints

## Purpose of this guide

This guide explains, in operational language, how Auto Blending chooses a
blend. It covers:

- what BlendMaster is trying to achieve;
- how costs, rewards and penalties affect ranking;
- which rules are hard constraints;
- which sources are available to the solver;
- how steady states and alternative blend options are handled; and
- what to check when no feasible blend is found.

The guide describes the current application behaviour. Manual blending is not
yet governed by this optimisation model.

## The short version

BlendMaster solves a **minimisation** problem for each steady state. A lower
objective value is better.

The objective can be read as:

> source costs and enabled penalties, less throughput and enabled rewards

The default Throughput Incentive is deliberately large. With the default
setting, BlendMaster will normally process as many feasible tonnes as it can.
Once feasible throughput is established, guidance, grades, haulage cost,
stockpile preferences, states and stockpile dynamics distinguish the available
solutions.

A reward does not override a hard constraint. For example, a strongly rewarded
stockpile still cannot exceed its available balance, reclaim rate or Maximum
Quantity, and the resulting blend must satisfy every applicable grade rule.

## Important terms

**Steady state**
: A time window during which one blend decision is applied. A steady state may
  finish at a planning-period boundary, source depletion, stockpile turnover or
  product-build completion.

**Stockpile source**
: Feed reclaimed from a weighted-average inventory stockpile or an AMT
  stockpile.

**Direct-tip source**
: Eligible aggregated 24HR grade-block payloads delivered within the current
  steady-state window.

**Hard constraint**
: A rule that must be satisfied. If the rules conflict, no feasible blend is
  returned.

**Reward or penalty**
: A soft preference in the objective. It changes ranking but does not make an
  infeasible blend feasible.

**Custom ratio constraint**
: A named, user-configured hard constraint calculated from numeric properties
  of the selected sources. Its minimum and maximum can vary by Calendar period.

## The objective function

For each candidate source `s`, BlendMaster selects tonnes `x(s)`.

In simplified form, the objective is:

```text
Minimise

sum over sources [
    selected tonnes
    × (
        haulage/source cost
        + guidance penalties
        - throughput incentive
        - direct-tip reward
        - 2WP destination-turnover reward
        - continuity rewards
        - source-preference rewards
      )
]

+ selected-source penalties
+ active-blend set penalty, or
- active-blend set reward
```

Calendar Cash is retained internally for compatibility but is fixed at zero.
It is not displayed and does not affect the objective.

### How to interpret signs

- A **cost** increases the objective, making a source less attractive.
- A **reward** reduces the objective, making a source more attractive.
- A **penalty** increases the objective, making non-compliance less attractive.
- A setting of zero has no ranking effect.

The 2WP guidance value fields accept positive and negative numbers:

- a positive value rewards compliance; and
- a negative value penalises non-compliance.

### Objective components

| Component | Where it is controlled | Current default | How it works |
|---|---|---:|---|
| Throughput Incentive | Solver Configuration | 1,000,100 $/t | Reward for every feasible tonne processed. The large default makes throughput the dominant objective. Lowering it allows operating costs and other preferences to trade against throughput. Zero permits the solver to choose zero tonnes when all available tonnes have a positive net cost. |
| Rehandle Cycle Time Penalty | Decision Levers and Solver Configuration | Disabled; 5 $/hr | When enabled, a stockpile receives a haulage cost based on its shortest route to a selected crusher. The cost remains editable while the penalty is disabled. |
| 2WP Product Guidance | Decision Levers and Solver Configuration | Disabled; 0 $/t | Uses the proportion of the stockpile associated with the active product brand. Positive values reward a match; negative values penalise the unmatched proportion. |
| 2WP Source Stockpile Timing Compliance | Decision Levers and Solver Configuration | Disabled; 0 $/t; 0 h tolerance | Rewards use inside the 2WP source window, including tolerance. Outside the window, compliance declines progressively as time distance increases. |
| 2WP Active Blend | Decision Levers and Solver Configuration | Disabled; 0 $/t | Compares the selected stockpile set with the 2WP stockpile set for the active product brand and time. Ratios are not compared. |
| Direct Tip Incentive | Decision Levers and Solver Configuration | Direct tip enabled; 10 $/t | Reward for eligible direct-tip tonnes. Direct-tip ratio limits remain hard constraints. |
| 2WP ROM Destination Turnover Guidance | Decision Levers and Solver Configuration | Disabled; 10 $/t | Applies only to exact grade-block-to-ROM-stockpile matches from the 2WP. A positive value increasingly rewards later/no turnover. A negative value increasingly penalises earlier turnover and applies no penalty to no-turnover destinations. |
| Stay on Same Blend Incentive | Solver Configuration | 0 $/t | Rewards stockpile sources retained from the previously selected blend. |
| Stay With Same Grade Block Pair Incentive | Solver Configuration | 0 $/t | Rewards reuse of the previous direct-tip parent-grade-block and stockpile pairing. Operational slice suffixes such as `_627` and `_124` are ignored. |
| Prefer Fewer Stockpiles | Decision Levers and Solver Configuration | Disabled; 10 per selected stockpile | Adds a fixed penalty for each selected stockpile. This is not a per-tonne value. |
| Balance Preference | Decision Levers and Solver Configuration | None; 1 $/t | If enabled, rewards either lower-balance or higher-balance stockpiles. The reward is scaled between the lowest and highest available stockpile balances. |
| Prefer AMT Stockpiles | Decision Levers and Solver Configuration | Disabled; 1 $/t | Rewards AMT stockpile tonnes over weighted-average inventory stockpile tonnes. |
| Try Contaminated Stockpiles First | Decision Levers and Solver Configuration | Disabled; 1 $/t | Rewards stockpiles above one or more configured Si, Al, P or Mn thresholds. The reward increases with distance above the threshold. |
| Try Low Grade Stockpiles First | Decision Levers and Solver Configuration | Disabled; 1 $/t | Rewards stockpiles below the configured Fe threshold. The reward increases with distance below the threshold. |
| Source Selection Tie-break Penalty | Solver Configuration | 0.001 per selected source | Used while generating additional blend options. It gives a small preference to solutions using fewer total sources when otherwise equal. |

### Haulage cost calculation

When Rehandle Cycle Time Penalty is enabled:

```text
truck rate (t/h) = 100 t × 60 / cycle minutes

stockpile haulage cost ($/t)
    = haulage cost ($/h) / truck rate (t/h)
    = haulage cost ($/h) × cycle minutes / 6,000
```

The nominal payload is fixed at 100 t. For every stockpile, BlendMaster uses
the shortest valid cycle to one of the crushers selected on Guidance Schedules.
An eligible reclaimable stockpile without a valid selected-crusher route causes
validation to fail when the penalty is enabled.

### 2WP timing compliance

Inside the 2WP window plus the configured tolerance, compliance is 100%.
Outside that expanded window:

```text
compliance = 1 / (1 + distance in hours)
```

This progressive curve avoids a sudden all-or-nothing timing preference.

### Active-blend comparison

Active-blend guidance compares stockpile names as a set:

- every expected stockpile must be selected; and
- no additional stockpile may be selected.

Stockpile ratios are deliberately excluded. If an expected stockpile is not
available, an exact match cannot be achieved.

### 2WP ROM destination turnover guidance

BlendMaster scans the complete imported 2WP horizon. For every grade block
planned to a ROM stockpile, it finds that stockpile's first planned reclaim
after the deposit. The resulting priority is linear across the actual horizon:

```text
priority = (first reclaim time - 2WP horizon start)
           / (2WP horizon end - 2WP horizon start)

positive turnover reward ($/t) = configured value x priority

negative early-turnover penalty ($/t)
    = absolute configured value x (1 - priority)
```

Priority is clamped to 0 through 1. A stockpile with no planned reclaim in the
imported horizon receives priority 1. Only an exact 2WP destination match is
eligible; pit-level and last-destination fallbacks receive no turnover reward.
This is a soft direct-tip ranking preference and never overrides balances,
grades, delivery timing or other hard constraints.

Optimised source rows retain the planned stockpile destination, first reclaim
datetime, normalized priority, whether the guidance was applied, and the
effective signed per-tonne reward/penalty after priority scaling.

The SQLite report `two_wp_grade_block_turnover_audit` provides the same audit
independently of optimiser selection. It is built from the complete prepared
APS payload population, so grade blocks that were not direct tipped are still
included. A grade block with more than one distinct derived 2WP outcome has one
row per outcome. The report includes available payload WMT and timing, the APS
schedule destination, exact 2WP planned stockpile destination, first reclaim
datetime, normalized turnover priority, destination-resolution method,
guidance applicability, whether the enabled guidance was applied, and its
effective per-tonne incentive. Pit/last-destination fallbacks remain visible
for audit but have no exact 2WP destination, priority or applied guidance.

## Source eligibility rules

The solver only sees sources that pass the operational eligibility rules.

### Stockpiles

A stockpile must have:

- a usable stockpile state;
- non-negative available balance;
- valid Fe, Si, Al, P and Mn grades; and
- an eligible reclaim path and positive capacity.

The Calendar state controls availability:

| State | Behaviour |
|---|---|
| Build | The stockpile cannot be reclaimed. It may receive planned payloads. |
| Auto | The stockpile becomes reclaimable after its planned build transactions are complete and its balance meets the reclaim threshold. |
| Reclaim | The stockpile is made available for reclaim, subject to balance, grade, rate and quantity constraints. |
| Off | Supported internally and excluded from reclaim if present in older data, although it is not a current Calendar choice. |

For a Total_Feed crusher, reclaim capacity comes from the Max Reclaim Rate on
Stockpile Inventories. For other crushers, it comes from the Calendar reclaim
equipment rate.

### Direct-tip grade blocks

Direct-tip sources are available only when:

- Enable Direct Tip is selected;
- the 24HR movement passed the selected-agent and movement-routing rules;
- its estimated delivery time is inside the current steady-state window;
- it has positive payload tonnes and a positive available rate; and
- it has not already been consumed.

Payload arrival does **not** create a new steady state. Payloads whose delivery
time falls inside the existing steady-state window become candidates for that
window.

Operational slices whose final grade-block path component differs only by a
numeric suffix, for example `LG46_627` and `LG46_124`, keep their individual
payload arrival and balance identity. Reporting, minimum grade-block pair
duration, grade-block-to-stockpile lock, and same-pair continuity use their
shared parent name (`LG46`).

## Configuring custom constraints

Solver Configuration contains a **Custom Constraints** table with
**Add Constraint...**, **Edit...** and **Delete** actions. A definition has a
stable name, a numerator expression and a denominator expression. The two
expression fields are editable, type-to-search lists: select a field directly
or enter an expression such as:

```text
modelled_property_a * selected_fe
```

with a denominator such as:

```text
modelled_property_c
```

Expressions are parsed without `eval`. They may contain numeric field names,
numeric constants, parentheses and the operators `+`, `-`, `*` and `/`,
including unary `+` or `-`. Function calls, attributes, subscripts, powers and
other Python syntax are rejected. Division by zero and non-finite results are
also rejected.

Imported field names are canonicalised for use in expressions: they are made
lower-case, non-alphanumeric characters become underscores, repeated
underscores are collapsed, and a name beginning with a digit receives a
`field_` prefix. Python keywords receive the same prefix. Explicit APS
source-property mappings expose their stable BlendMaster names instead of the
site-specific header. See [Data Streams](DATA_STREAMS.md#field-mappings)
for the mapping catalogue.

CC OPF02 inventory `PROD3` physical properties are exposed as canonical
`prod2_*` fields, matching the logical Product 2 channel used by EXPIT and AMT.
The raw inventory names remain available for audit.

The field selector advertises only canonical additive and weighted-average
fields checked **Use in Optimisation** on Define Fields. Runtime flags,
duplicated selected-grade aliases and raw source metadata are not offered.
Enter the numeric literal `1` when a scalar denominator is required.
Availability is still validated source by source before solving, so appearing
in the selector does not guarantee that every source has a mapped value.

Weighted-average properties—grades, percentages, ratios, moisture, yields,
recovery, ultrafines and density—are exposed directly under their canonical
names. A field declared **Additive** is exposed under that same canonical name
as a per-source-WMT coefficient. The legacy alias
`<field>_per_source_wmt` remains accepted for saved expressions. For example,
if `product_dmt` is additive, `product_dmt` in an expression means its DMT per
source WMT, preserving dimensions across inventory stockpiles, AMT chunks and
grade blocks with different balances. The declared type, not the spelling of
the raw Snowflake/APS header, controls aggregation and depletion.

After Solver Configuration is submitted, Calendar adds **Min** and **Max** rows
under each custom constraint for every planning period. Either side may be left
blank to leave that bound unenforced. If both are entered, Min must not exceed
Max. Definitions and Calendar bounds are stored in projects using a stable key,
so constraints with the same display labels in different table sections do not
collide.

Every expression and every declared weight dependency is checked again against
every source available to a solve.
Preparation stops with the constraint and source named when a referenced field
is missing, non-numeric or non-finite, or when an expression divides by zero.
The effective denominator must be non-negative and capable of being positive.
Missing data is never silently replaced with zero: a missing mapping, weighted
field or additive weight stops preparation/optimisation with the constraint and
source identified.

The same strict rule applies to optimiser grades. An unbranded value in the
requested stream may serve any brand, but a missing requested stream cannot
fall to a lower or legacy stream during optimisation or manual planning.

At run preparation BlendMaster carries the canonical properties checked **Use
in Optimisation**, plus the automatically checked additive weight of every
active weighted-average field and any dependency referenced by an enabled saved
expression, through dynamic stockpile builds, depletion, EventPool and
optimisation. The full defined catalogue remains in Database View. Active
properties are written to optimised and manual reports under dynamic
`source_property_<canonical_field>` columns. Weighted-average values remain
unchanged for each report source; additive totals are scaled to the tonnes
selected from that source. Consolidation uses each weighted-average field's
declared additive Weight Field, not source WMT unless that is the chosen weight.
Unchecked properties are not duplicated through solve state or reports.

## Hard constraints inside each optimisation

### 1. Non-negative tonnes

Every source contribution must be zero or positive.

### 2. Source availability, balance and rate

For each source:

```text
selected tonnes
    <= available balance

selected tonnes
    <= source rate × steady-state duration
```

The tighter limit is used.

### 3. Crusher capacity

```text
total selected tonnes
    <= crusher rate × steady-state duration
```

The model does not contain a hard minimum crusher throughput. The Throughput
Incentive normally drives the solution toward the maximum feasible tonnes.

### 4. Calendar Maximum Quantity

Maximum Quantity is a planning-period allowance. Each steady state receives a
proportional share:

```text
selected tonnes in the steady state
    <= Maximum Quantity
       × steady-state duration
       / planning-period duration
```

### 5. Crusher blend grades

The weighted-average crusher feed must be within the Calendar minimum and
maximum for:

- Fe;
- Si;
- Al;
- P; and
- Mn.

These are hard constraints in every steady state unless both:

- “Off-spec steady states are allowed if ultimate build is on spec” is enabled;
  and
- Product Targets are configured.

### 6. Stockpile-only grade feasibility

When “Stockpile blend must be feasible” is selected, the weighted-average
stockpile portion must satisfy the crusher grade limits on its own. Direct tip
cannot rescue an off-spec stockpile blend.

When “Stockpile blend can rely on grade blocks” is selected, only the combined
stockpile plus direct-tip feed must meet the steady-state grade limits.

### 7. Product-build completion grades

Product builds track cumulative tonnes and grade metal.

If the active product build can be completed inside the current steady state,
the opening build inventory plus candidate feed must finish within the product
build's Fe, Si, Al, P and Mn limits.

When off-spec steady states are allowed, intermediate steady-state grade limits
may be relaxed, but the product-build completion constraint remains active.

### 8. Direct-tip ratio

The selected direct-tip tonnes divided by total crusher-feed tonnes must lie
between the Calendar minimum and maximum.

- Minimum 0 means direct tip is not required.
- Maximum 0 prohibits direct tip.
- Maximum 1 permits up to 100% direct tip.
- When Direct Tip is disabled, the effective direct-tip ratio is zero and
  grade-block sources are removed.

### 9. Custom constraints

Each side is aggregated across all sources selected in the steady state:

- an **Additive** expression is summed using the proportionally depleted value
  from every selected source;
- a **Weighted Average** expression is averaged using its declared additive
  Define Fields weight across every selected source; and
- a numeric literal is one scalar constant, not a value repeated for every
  selected source tonne.

The constrained value is `aggregate(numerator) / aggregate(denominator)`:

```text
quantity_1 / quantity_2 = sum(quantity_1) / sum(quantity_2)
quantity_1 / 1          = sum(quantity_1)
product_fe / 1          = sum(product_fe * product_dmt) / sum(product_dmt)
```

Operations inside an expression are evaluated for each source before the
appropriate sum or weighted average. Min and Max are hard constraints on the
whole selected steady-state blend, never independent per-source tests. Ratios
between a summed side and a weighted-average side are nonlinear and are
rejected; use an additive weighted-mass field or a constant for the other side.
A definition with both Calendar bounds blank is calculated and reported but
does not restrict the blend.

### 10. Minimum and maximum stockpile count

When configured in Decision Levers:

- at least Min Stockpiles must be selected; and
- no more than Max Stockpiles may be selected.

Only stockpiles count. Direct-tip grade blocks do not count toward these limits.

### 11. Minimum Stockpile Contribution Ratio

For stockpile-count purposes, every selected stockpile must contribute at least:

```text
minimum contribution ratio × total crusher-feed tonnes
```

The default ratio is 0.01, or 1%. This prevents negligible “token” tonnes from
being used merely to satisfy Min Stockpiles.

### 12. Depletion-controller equality

When a provisional solution shows that a selected stockpile or grouped
direct-tip source depletes before the end of the current window, BlendMaster
shortens the steady state to that depletion time and resolves it. It then
requires the controlling source to use the calculated depletion tonnes.

If that equality makes the shortened problem infeasible, BlendMaster retries
the shortened window without the equality as a safeguard.

### 13. Active-blend selection linkage

When active-blend guidance is enabled, binary source-selection variables are
linked to actual source tonnes. A source marked as selected must contribute a
positive amount. The exact-set reward or penalty is then applied only when the
selected set can be determined consistently.

### 14. Distinct alternative blend options

After finding one feasible option, BlendMaster excludes that exact active
source set and resolves to find another option. Each additional option must
therefore use a different source set.

This is a hard exclusion used to generate alternatives, not a production
constraint on the selected option.

## Candidate guardrails applied after each solve

Some operational rules are checked after a mathematically feasible candidate is
returned. A candidate that fails is rejected, its source set is excluded, and
BlendMaster searches for another option.

### Minimum Stockpile Feed Duration

If configured, the selected stockpile blend must be capable of sustaining its
selected reclaim rates for at least the smaller of the requested duration and
the candidate's final steady-state duration. The estimate uses the earliest
depletion time among the selected stockpiles. The cap is applied after dynamic
boundary adjustment, so Calendar period ends, depletion, Auto turnover and
product-build completion can shorten the required duration. For example, a
three-hour setting becomes one hour for a one-hour state. The saved setting
remains three hours and applies again to longer states. The trace reports when
the effective minimum is capped.

### Minimum Grade Block Pair Duration

For steady states at least as long as the configured threshold, selected
direct-tip payloads from each parent grade block must collectively span the
minimum delivery duration. Operational slices of the same parent are assessed
together. Missing or insufficient delivery timestamps cause the candidate to
be rejected.

### Grade block to stockpile-mix lock

When enabled, the first accepted stockpile mix used with a parent grade-block
source becomes that parent's lock. A later operational slice of the same parent
using a different stockpile set is rejected.

## Steady-state boundaries

The initial window runs to the end of the current Calendar period. BlendMaster
may shorten it at:

- stockpile depletion;
- grouped direct-tip source depletion;
- stockpile Auto turnover; or
- product-build completion.

Payload arrivals inside a window do not create a boundary. Period boundaries,
depletion, turnover and build completion create the next decision point.

## Solver controls that do not change feasibility

| Control | Purpose |
|---|---|
| Blend Option Timeout | Maximum CBC solve time for one blend-option search. Zero disables the timeout. A timeout can stop the search before all alternatives are found. |
| Max Blend Options per Steady State | Limits how many distinct feasible source sets are generated for a decision point. |
| Source Selection Tie-break Penalty | Gives deterministic ranking among alternative source sets; it does not add or remove available sources. |

## Understanding the Solver Score

BlendMaster reports a Solver Score for feasible results:

```text
Solver Score = -objective value / selected tonnes
```

A higher score is better. It is a relative ranking measure for the current
configuration, not a forecast profit or accounting margin. Scores from projects
with different incentive values should not be compared as if they were monetary
outcomes.

## Custom-constraint report columns

Optimised and manual blend reports retain both the steady-state ratio and the
per-source coefficients used to calculate it. A constraint whose stable key is
`<key>` creates these columns:

| Column | Meaning |
|---|---|
| `custom_constraint_<key>_name` | User-facing constraint name. |
| `custom_constraint_<key>_numerator_expression` | Saved numerator expression. |
| `custom_constraint_<key>_denominator_expression` | Saved denominator expression. |
| `custom_constraint_<key>_numerator` | Whole-blend aggregate of the numerator expression: sum, declared weighted average, or scalar constant. |
| `custom_constraint_<key>_denominator` | Whole-blend aggregate of the denominator expression: sum, declared weighted average, or scalar constant. |
| `custom_constraint_<key>_actual_ratio` | Numerator total divided by denominator total. |
| `custom_constraint_<key>_target_min` | Calendar minimum used for the steady state, or blank. |
| `custom_constraint_<key>_target_max` | Calendar maximum used for the steady state, or blank. |
| `custom_constraint_<key>_source_numerator_coefficient` | For an additive side, contribution per physical source WMT; for a weighted-average side, the source expression value; blank for a scalar side. |
| `custom_constraint_<key>_source_denominator_coefficient` | Same source-level interpretation for the denominator side; blank for a scalar side. |
| `custom_constraint_<key>_source_numerator_contribution` | Selected additive contribution, or weighted mass (`value × selected declared weight`) for a weighted-average side; zero for a scalar. |
| `custom_constraint_<key>_source_denominator_contribution` | Same source-level contribution for the denominator side; zero for a scalar. |

The optimised report database adds these columns dynamically, so new named
constraints do not require a schema migration. The whole-blend numerator,
denominator, ratio and targets are repeated on each source row so any row is
self-describing; the `source_*` fields are specific to that row. When payloads are consolidated
to a grouped source row, their source coefficients are weighted by the reported
source tonnes. Additive aggregates are the sum of source contributions.
Weighted-average aggregates divide summed weighted-mass contributions by the
summed selected declared weight. Scalar aggregates are shown only in the
unqualified whole-blend column.

Each imported/modelled property referenced by an enabled expression also
creates a numeric `source_property_<canonical_field>` column. The name is the
underlying source property, not its expression coefficient. For example, an
expression using `prod1_wmt_per_source_wmt` causes
`source_property_prod1_wmt` to be reported. Because `prod1_wmt` is additive,
that report value is scaled from the source total to the tonnes selected on the
row. An intensive field such as `prod1_minus_1mm_pct` is reported unchanged as
`source_property_prod1_minus_1mm_pct`. These selective columns provide the
inputs needed to reconstruct a custom constraint without writing every unused
Database View property to every result row.

## Why a run may be infeasible

Common causes include:

- no source is available in the current state and time window;
- crusher rate or source reclaim rates are zero;
- available balances are exhausted;
- Maximum Quantity is too restrictive;
- grade ranges cannot meet crusher or product-build targets;
- the stockpile-only grade rule is stricter than the available stockpiles allow;
- direct-tip minimum and maximum ratios conflict with available sources;
- a custom constraint has contradictory bounds, no positive denominator, missing
  source properties, or cannot be satisfied by the available blend;
- Min Stockpiles, Max Stockpiles and Minimum Contribution Ratio are
  incompatible;
- Total_Feed stockpiles have no positive Max Reclaim Rate;
- rehandle penalty is enabled but a reclaimable stockpile has no selected
  crusher route; or
- minimum duration and grade-block lock guardrails reject every candidate.

When diagnosing a run, review in this order:

1. source states, time availability, balances and reclaim rates;
2. crusher and product-build grade limits;
3. direct-tip ratio settings;
4. custom-ratio expressions, source-field coverage and Calendar bounds;
5. Min/Max Stockpiles and Minimum Contribution Ratio;
6. Maximum Quantity and minimum-duration settings;
7. Total_Feed reclaim rates and haul-cycle routes; and
8. the solver timeout.

Rewards and penalties should normally be adjusted only after the feasibility
rules are understood. Changing a reward cannot resolve a contradiction between
hard constraints.

For each custom constraint, infeasibility diagnostics retain its expressions,
Calendar bounds and the available per-source coefficient range when that
range can be determined. An obviously out-of-range Min or Max is named in the
likely-cause trace.
