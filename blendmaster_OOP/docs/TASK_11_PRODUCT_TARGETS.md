# Task 11 — Product Targets

Implemented 6 September 2026.

The former Product Build Settings tab is now **Product Targets**. Its heading,
validation messages, navigation, agent instructions and workflow summaries use
the current name. Build rows, tonnes, grade bounds, 2WP imports and grouping retain
their existing meanings.

## Persistence and compatibility

- `product_targets` is the canonical UI attribute, page identifier and key in
  projects, site scenarios, calendar inputs and outgoing agent context.
- Load and save migrate the former `product_build_settings` key in both active
  and inactive scenarios and their calendar inputs. Migration preserves the row
  data, including existing planning provenance, without rewriting inventories.
- Saved named page states and old flat-tab indices still restore. Direct use of
  the old page name also opens/enables the current tab. Explicit current page
  state wins over older named or indexed state.
- Agent workflow sections and proposal targets accept `product_targets`,
  `product_targets_tab`, `product_build_settings`, `product_builds` and
  `product_build_settings_tab`. Lists and existing `builds`/`rows` wrappers are
  supported. Reviews present the canonical target name.
- A current target section takes precedence over legacy aliases, including when
  one appears in a top-level section and another in proposed constraints. An
  explicit empty list clears targets; an omitted section leaves current rows in
  place during workflow application.
- Existing Python GUI accessors remain aliases to the current state and methods.
  Solver, manual-planner and reporting APIs retain their established internal
  `product_build_settings` parameters. `Run` accepts either calendar key and
  forwards the selected rows to those APIs.

New saves and outgoing agent context use the current key. Compatibility means
that this version reads old projects and payloads; it does not require older
application binaries to understand new saves.

## Validation

- `python -m unittest discover -s tests`: **697 tests passed**, including 12
  Product Targets regression tests for project migration, nested scenarios,
  empty-list precedence, navigation, agent review/application, calendar storage
  and outgoing context.
- An offscreen Qt check rendered the actual renamed tab with FB/SS rows, opened
  it through the legacy page ID and stored its table values into current calendar
  inputs. Both the tab caption and heading displayed Product Targets.
- Python compilation and `git diff --check` passed.

Task 12 was subsequently implemented on 7 September; see
[Product quality-limit data and inputs](TASK_12_PRODUCT_QUALITY_LIMITS.md).
