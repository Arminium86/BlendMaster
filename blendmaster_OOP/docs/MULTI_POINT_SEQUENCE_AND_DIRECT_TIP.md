# Multi-point sequence and direct-tip corrections

## Result views

The optimised sequence now uses a native Qt timeline instead of the Dash Gantt
server. Each OPF/tipping-point pair has its own row, including configured points
with no saved feed. Point filters, zoom, interval tooltips and source details all
use the physical point's allocation rows. Simultaneous points sharing a blend ID
are not combined. The source table stays hidden until an interval is selected.
The two snapshot/report configuration buttons were removed.

The multi-point manual dashboard and sequence read saved named manual reports.
If only an optimised report exists, they show its allocations and offer a copy
as a manual starting plan. Availability no longer depends on the legacy single
crusher recipe controls. Metadata-only checkpoint tables fall back to the legacy
Primary report; real named reports remain authoritative. Switching manual plans
restores each plan's own state, including its input revision and backup choices.

Manual editing supports tonnes and timing for existing saved source/chunk
allocations. It preserves source and route identities. Introducing a new source
requires a prepared starting plan containing that source. Edits recalculate
point ratios, mapped quantities, shared inventory, grades and product progress,
and use the existing Conveyor/COS replay. Inventory, overlapping footprint use,
Calendar equipment/grade/direct-tip limits and product-build capacity remain
hard checks. Stale saved results remain viewable but must be recalculated for
current inputs before editing.

Legacy reports that stored whole-second timestamps retain precise duration
fields. The shared timing reader recovers fractional boundaries where that
evidence agrees within one second. This prevents short intervals from acquiring
false crusher-rate violations during manual recalculation.

Material Flow now collapses groups even when there are fewer than 21 sources.
The Show all sources checkbox expands individual sources, preserves their
positions, and cannot revive a graph after its result is cleared.

## Calendar and solver

Point-scoped Calendar edits are retained when cells change. Preparation captures
the Calendar before rebuilding it; programmatic default population cannot
overwrite those edits. Captured settings and the run boundary both apply the
current period-specific Calendar values to the multi-point configuration.

A positive direct-tip ratio minimum requires positive direct-tip feed when that
point has a positive Calendar crusher rate. This closes the zero-feed (0/0)
escape in the joint solver. Failure from any physical point is propagated to the
combined result. Independent saved-result checks also identify missing direct
tipping. Product-build soft grade targets do not soften these operating limits.

## Validation on 14 September 2026

- The final full unittest suite passed with 1,314 tests, including the manual-plan
  switching correction and a Calendar rebuild regression.
- Offscreen application components read 636 saved allocation rows from an
  isolated copy of the validation project's database. Optimised and manual
  sequence views displayed 632 physical intervals across HAL_PC / CC OPF01,
  OPF01_PC / CC OPF01, and OPF02_PC / CC OPF02. The manual dashboard displayed
  the saved allocations without requiring another optimisation.
- Applying OPF02_PC minimum 0.1 to all periods in the validation check identified
  210 noncompliant saved intervals. Solver fixtures confirm that zero available
  direct-tip material fails even with soft product grades, and available material
  produces a ratio meeting the minimum.
- A manual multi-point transport fixture replayed 180 t tipped feed into 90 t
  arrivals and 81 t product using the existing transport engine.
- The actual saved project has outdated Conveyor/COS opening history relative
  to its transport setup. Its manual edit replay correctly stops at that check.
  A complete recalculation of that project requires refreshed opening evidence.

The saved project and the inspected live Calendar still showed OPF02_PC minimum
0.0 in Preplan, Period_1 and Period_2. The user specified 0.1 for all periods;
0.1 means a 10% direct-tip minimum. The project file was not modified during
validation. Restart BlendMaster to load the code changes, restore those Calendar
values, and refresh the model's opening evidence before calculating again.
