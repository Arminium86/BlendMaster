# Inventory refresh while viewing a saved plan

Implemented 14 September 2026.

## Behaviour

- Submit captures the inventory selection and editable cells without replacing
  accepted opening data. A worker prepares an independent snapshot, including
  mappings, reconciliation, AMT enrichment and compatible combined-OPF profiles.
- Only missing or incompatible AMT footprints are fetched. Explicit Refresh AMT
  Data refreshes all selected, included footprints. Existing refresh-tolerance,
  exclusion and reconciliation policies still apply.
- Input pages and site switching are locked during preparation. Saved sequence,
  manual allocation and report pages remain available for viewing. Calculations,
  manual plan replacement and saving cannot publish a pending selection.
- The worker validates its staging database before replacing the two opening
  tables in one SQLite transaction. WAL lets report readers continue viewing the
  last committed snapshot during publication. Saved report tables are retained.
- Failure, cancellation or an obsolete request discards the staged replacement.
  Retry uses the current selection; Keep previous inputs restores accepted
  selections, rates, thresholds and subsets. Cancellation takes effect after an
  in-flight warehouse query returns or at the next preparation checkpoint.
- A persisted opening revision invalidates dependent reconciliation and result
  freshness after success. Reconciliation review and AMT participation checks
  must pass before the next calculation. The old saved plan remains viewable.

AMT map decoding and saved-chunk reconciliation run in a worker. Automatic
preparation also reconciles generated chunks there and avoids repeating the
opening-table preparation on the UI thread. OPF preparation keeps all waiting
continuations, so a calculation requested while a profile is being loaded resumes
only after that profile is ready.

The transaction covers the opening inventory/AMT refresh. The broader Prepare
Inputs workflow retains its existing per-stage import and validation boundaries.
This change does not alter transport, conveyor or COS equations.

## Verification

- Full regression suite: 1,349 tests passed in 90.166 seconds.
- An additional real AMT map/chunk test passed and verified reconciliation ran
  on a different thread from the Qt UI.
- Explicit full-window check passed: two combined-OPF timeline intervals and
  their source details remained available during a delayed warehouse request;
  failure retained the accepted selection and three saved report rows; retry
  published the new AMT selection and initialized its native table correctly.
- Transaction checks cover cancellation between the two table replacements and
  concurrent reads of the previous snapshot. Other checks cover stale requests,
  partial AMT reuse, calculation gating, nested locks and OPF continuations.

The final native-window fixture disables warehouse connections, supplies fixed
AMT rows and substitutes the legacy embedded browser/server. Native Qt controls,
worker execution and SQLite publication are exercised. It does not benchmark a
full live warehouse refresh or reload of the large validation project.

Reproduce with Python 3.12 from `blendmaster_OOP`:

```text
python -m unittest discover -s tests -q
python -m unittest tests.render_inventory_refresh -v
```

The second command writes screenshots and a validation receipt under
`docs/screenshots/inventory_refresh`. Restart an already-running BlendMaster
process to load the implementation.
