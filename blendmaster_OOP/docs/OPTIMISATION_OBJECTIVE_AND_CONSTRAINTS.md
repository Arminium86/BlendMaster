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
| Stay on Same Blend Incentive | Solver Configuration | 0 $/t | Rewards stockpile sources retained from the previously selected blend. |
| Stay With Same Grade Block Pair Incentive | Solver Configuration | 0 $/t | Rewards reuse of the previous direct-tip grade-block and stockpile pairing. |
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

## Configuring custom ratio constraints

Solver Configuration contains a **Custom Ratio Constraints** table with
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
site-specific header. See [Data Streams](DATA_STREAMS.md#aps-field-mappings)
for the mapping catalogue.

CC OPF02 inventory `PROD3` physical properties are exposed as canonical
`prod2_*` fields, matching the logical Product 2 channel used by EXPIT and AMT.
The raw inventory names remain available for audit.

The field selector advertises the union of imported/modelled properties found
on the current Database View sources. This keeps optional APS fields and AMT
properties with partial lineage coverage discoverable. Availability is still
validated source by source before solving; appearing in the selector does not
guarantee that every source has the field. The following built-in fields are
always offered:

| Field | Per-source value |
|---|---|
| `one` | `1.0`; use this as the denominator for a tonne-weighted average or as the numerator/denominator basis for a source-share ratio. |
| `is_direct_tip`, `is_grade_block` | `1.0` for a direct-tip grade block, otherwise `0.0`. |
| `is_stockpile` | `1.0` for an inventory or AMT stockpile source, otherwise `0.0`. |
| `is_amt` | `1.0` for an AMT chunk, otherwise `0.0`. |
| `is_inventory` | `1.0` for a non-AMT inventory stockpile, otherwise `0.0`. |
| `source_balance` | Source balance available when the event was created. |
| `selected_fe`, `selected_si`, `selected_al`, `selected_p`, `selected_mn` | The active brand's effective selected grade after data-stream fallback. |
| `grade_fe`, `grade_si`, `grade_al`, `grade_p`, `grade_mn` | Aliases of the same effective optimiser grades. |

Numeric intensive properties—grades, percentages, ratios, moisture, yields,
recovery, ultrafines and density—are exposed directly under their canonical
names. Additive source totals ending in `_wmt`, `_dmt`, `_tonnes`, `_mass` or
`_volume` are instead exposed as per-source-tonne coefficients named
`<field>_per_source_wmt`. For example, an imported `product_dmt` total is used
as `product_dmt_per_source_wmt`. This preserves dimensions when inventory
stockpiles, AMT chunks and grade blocks have different balances, and prevents a
source-level total from being multiplied by selected tonnes as though it were
an intensive grade. Other numeric APS fields are retained and WMT-weighted as
intensive properties by default; Database View emits a warning naming fields
where that assumption was required. Runtime/control columns are not advertised
as constraint fields.

After Solver Configuration is submitted, Calendar adds **Min** and **Max** rows
under each custom constraint for every planning period. Either side may be left
blank to leave that bound unenforced. If both are entered, Min must not exceed
Max. Definitions and Calendar bounds are stored in projects using a stable key,
so constraints with the same display labels in different table sections do not
collide.

Every expression is checked again against every source available to a solve.
Preparation stops with the constraint and source named when a referenced field
is missing, non-numeric or non-finite, or when an expression divides by zero.
The denominator must be non-negative for every source and positive for at least
one available source. Missing data is never silently replaced with zero.

At run preparation BlendMaster compiles the field dependencies from every
enabled numerator and denominator. Only those source properties are carried
through dynamic stockpile builds, depletion, EventPool and optimisation; the
full source-property catalogue remains in Database View and the saved APS
source snapshot. The active properties are also written to optimised and manual
reports under dynamic `source_property_<canonical_field>` columns. Intensive
values remain unchanged for each report source; additive totals are scaled to
the tonnes selected from that source. Unreferenced source properties are not
duplicated through solve state or reports.

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
- Product Build Settings are configured.

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

### 9. Custom ratio constraints

For selected tonnes `x(s)`, per-source numerator expression `N(s)` and
per-source denominator expression `D(s)`, BlendMaster constrains:

```text
custom ratio = sum over sources [x(s) * N(s)]
               / sum over sources [x(s) * D(s)]
```

This is a ratio of tonne-weighted totals, not an arithmetic average of each
source's `N(s) / D(s)`. Operations inside an expression—such as
`field_a * field_b`—are evaluated once for each source, producing a constant
coefficient before the linear optimisation is built. Min and Max are enforced
by cross-multiplying the non-negative denominator, so the resulting rules
remain linear hard constraints. A definition with both Calendar bounds blank
is calculated and reported but does not restrict the blend.

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
selected reclaim rates for at least the requested duration. The estimate uses
the earliest depletion time among the selected stockpiles.

### Minimum Grade Block Pair Duration

For steady states at least as long as the configured threshold, selected
direct-tip grade-block payloads must span the minimum delivery duration. Missing
or insufficient delivery timestamps cause the candidate to be rejected.

### Grade block to stockpile-mix lock

When enabled, the first accepted stockpile mix used with a grade-block source
becomes that source's lock. A later candidate using the same grade-block source
with a different stockpile set is rejected.

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
| `custom_constraint_<key>_numerator` | `sum[x(s) * N(s)]` for the steady state. |
| `custom_constraint_<key>_denominator` | `sum[x(s) * D(s)]` for the steady state. |
| `custom_constraint_<key>_actual_ratio` | Numerator total divided by denominator total. |
| `custom_constraint_<key>_target_min` | Calendar minimum used for the steady state, or blank. |
| `custom_constraint_<key>_target_max` | Calendar maximum used for the steady state, or blank. |
| `custom_constraint_<key>_source_numerator` | `N(s)` coefficient for this report source row. |
| `custom_constraint_<key>_source_denominator` | `D(s)` coefficient for this report source row. |

The optimised report database adds these columns dynamically, so new named
constraints do not require a schema migration. When payloads are consolidated
to a grouped source row, their source coefficients are weighted by the reported
source tonnes. These fields make the reported ratio independently
reconstructable from the source rows.

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
- a custom ratio has contradictory bounds, no positive denominator, missing
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
