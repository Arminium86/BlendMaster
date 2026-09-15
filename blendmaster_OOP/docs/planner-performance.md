# Planner performance changes

Implemented from the 14 September 2026 validation-project diagnosis.

## Behavior

- Calendar applies its saved model before constructing the table once.
- WorkflowViews owns Manual Blend Sequence entry. Identical pending loads are
  coalesced; successful charts survive navigation. Failed loads can retry, and
  the manual workspace's explicit Refresh bypasses its successful-result cache.
- Scenario snapshots share accepted approval registries and history caches.
  Mutable controls and physical sources remain independently copied. Project
  hydration defers intermediate scenario captures until completion.
- Inventory preparation and AMT map workers copy mutable source data without
  recopying accepted approval evidence or replacement-owned OPF caches.
- An unchanged inventory Submit checks an in-memory preparation receipt before
  copying, staging, publication, Calendar setup or AMT map preparation. Receipt
  checks run in a worker. Explicit Refresh always runs preparation.
- Inventory receipts cover actual settings, selected controls, physical sources,
  opening-input revision, source-file revisions, scenario and database/WAL
  versions. Mutable dependencies are content-checked. Only successful inventory
  delivery may establish a receipt; an unrelated map refresh cannot accept new
  preparation inputs. Database changes are checked again before reuse delivery.
- Accepted approval JSON encodings are reused for freshness/profile checks. The
  resulting SHA256 is identical to the previous canonical JSON hash, preserving
  compatibility with saved result revisions.
- Feed guidance, destination guidance and Calendar destinations derive from one
  projected Mining.csv read. The two haul-cycle route views share one read.
- Shared-input hashing retains its existing representation with cheaper scalar
  encoding. It still checks the complete content of each shared-input group.
- Checkpoint creation compacts the detached SQLite backup when it has free pages.
  It does not vacuum the live database or add compaction to Submit handlers.

## Evidence ownership contract

`classes/AcceptedEvidence.py` defines the boundary. A published approval registry
and its nested evidence are read-only. Writers must call `fork_registry` before
replacing source approvals or changing active-record metadata. Manual review and
continuous-assay updates follow this rule. Existing nested evidence is reused;
changes to nested evidence require replacement or a detached copy.

`data_stream_input_cache_result` is replaced as a whole. A resolver receives a
detached copy. Site activation/project hydration continue to detach restored
evidence before use. No runtime encoding cache is part of a scenario capture.

Do not add identity-cached mutable fields to `fingerprint_fields`. In-place edits
to an accepted registry would violate both snapshot isolation and cached-hash
correctness. Physical inventory, mapping and Calendar changes are deliberately
content-checked rather than trusted by object identity.

## Validation

Run `python -m unittest discover -s tests -q` with `QT_QPA_PLATFORM=offscreen` for
the test suite. `test_planner_performance.py` exercises evidence isolation,
canonical hash compatibility, receipt invalidation, explicit Refresh, database
changes between work and delivery, single-pass guidance, Calendar population,
manual-load retries and detached checkpoint compaction.

Desktop timing evidence and saved-report checks are recorded in the accompanying
15 September 2026 performance report in the Playground artifact directory.

## Content-based opening source reuse (15 September 2026)

`inventory_source_cache` is persisted with each scenario. Each source has raw
and prepared content fingerprints, preparation dependencies and a detached
prepared snapshot. `InventoryRefresh` compares sources separately, maps and
enriches only changed sources, and publishes changed/new rows in one SQLite
transaction. Unchanged rows and report tables remain intact. A submission with
no source/dependency changes skips publication and view rebuilding entirely.
A new column is added to the opening table without replacing existing rows.

Snapshot observation times (`transaction_datetime`, AMT inventory snapshot
transaction times, `last_update`, `hex_updated`, snapshot/as-of/fetch metadata)
are excluded from physical content. Raw tonnes, grades, build, geometry,
lineage and other source attributes remain significant. Generated fields are
tracked separately so mapping/enrichment cannot invalidate its own input.
AMT flattened modelled aliases and compact modelled JSON have the same meaning.
Sample dates and dates *inside* physical lineage are retained.

An AMT request for a different model time must still read the warehouse to
verify content unless the request itself is already cached. This read is not
an import or reconciliation: an identical result reuses the prepared source.
Refresh from Snowflake forces this verification, then uses the same content
comparison. The AMT Refresh Tolerance UI and age-based bypass are removed;
legacy saved values have no effect.

Reconciliation approvals now require matching source content, policy and
historical evidence. Changing a source invalidates only its approvals;
changing an OPF's historical evidence invalidates approvals depending on it.
Missing/changed sources remain pending until the user explicitly updates
Grade Reconciliation. Background profile delivery cannot continue a calculation
when a source changed during preparation and now requires approval.

The Grade Reconciliation **Lookback refresh tolerance** is configurable,
defaults to **60 minutes**, and accepts 0 for no time-shift tolerance. The model
start is compared with the last actual reconciliation's lookback anchor.
The boundary is inclusive; 60 minutes reuses, 61 minutes requires an update.
Reuse does not advance the anchor. Prepared OPF source grades also survive
model-start shifts within this window. Source or history changes invalidate
immediately, even inside the tolerance. Already accepted physical movements
and continuous-assay priors retain their separate lifecycle.

Auditable legacy snapshots can migrate with their verified source evidence.
Older identity-only approvals cannot prove unchanged tonnes/grades/history
and require one manual reconciliation update to establish these fingerprints.
The per-source cache is immutable in use and replaced on publication, like
accepted reconciliation registries. Hashing shared history is scoped to a
single comparison pass, rather than repeated for every hex.

Validation includes `tests/test_source_snapshots.py`: ordinary and AMT
observation-only refreshes, source append with a SQLite trigger preventing any
rewrite of an existing source, preserved reports, saved cache round trips,
forced warehouse verification, per-source reconciliation, evidence changes,
anchored tolerance boundaries and UI/default validation. Existing cancellation,
atomic publication, multi-OPF, approval and workflow tests also apply.

## Task submissions and role-safe dependencies

The Run button above the navigation submits or continues the active task using
its existing validation. Workspace progresses from top to bottom for both roles;
Support submissions stay on their page, including background completions.
Grade Reconciliation Run updates missing evidence when submission is unavailable;
review the resulting factors and Run again to submit. Secondary actions such as
exports and explicit refreshes remain on their pages.

Task receipts are saved with the site: green means a valid submission, red means
a task needs submission or resubmission, and only the next required Workspace
task is neutral. Later submissions also establish their earlier prerequisites
as ready. For legacy projects without receipts, saved input-task gates and
submitted inventory establish the prior progress; explicit invalidations take
precedence. AMT and Destination Reconciliation tabs are hidden while their
required selections are absent and return when applicable.
Editing submitted inputs invalidates downstream receipts without discarding
cached evidence or navigating away. Support configuration submissions invalidate
their dependent Workspace tasks. Views with no rows are disabled; the Load views
menu provides access to load Database, Expit and production history while those
tabs are unavailable.

Calendar checks conveyor/COS actuals after capturing the submitted opening rates.
Missing or stale actuals refresh automatically from saved Support settings for
either role. A successful refresh returns to Grade Reconciliation and waits for
user review, invalidating the later Workspace submissions. Failure retains the
previous actuals; results from a different site, database or request are rejected.
Planners do not gain permission to edit Support settings. Tests cover global Run,
asynchronous Support navigation, receipts, empty views, reuse and failed refreshes.

## Reconciliation copy ownership

Factor application now copies editable factor maps, coverage and source metadata
while retaining read-only search history, provenance and automatic-selection
evidence. Opening transport movements reuse the same evidence with their own
movement tonnes. A factor edit cannot alter the accepted factors for another
application. Changes to evidence still require replacement and normal approval
validation; sharing does not bypass invalidation or enable background searches.

Manual reconciliation, inventory application and OPF preparation workers use the
accepted-evidence snapshot boundary. Editable source records remain detached;
manual reconciliation forks the registry before replacing approvals. Completed
OPF profiles share their read-only audits with published inventory/chunk rows.
Unchanged publication leaves rows in place; missing or changed grades still
hydrate from the profile. AMT chunk builders use temporary member row shells and
transfer completed member audits, without copying their nested history again.

History delivery copies the cache and editable fields once each, without an
intermediate whole-result copy. Effective overrides cannot mutate cached history.
Scenario restoration detaches each required mutable field without first copying
the whole scenario. Restored caches and selected crushers remain isolated from
the saved scenario, as do the editable inventory and chunk collections.

`tests/test_reconciliation_copy_cost.py` enforces these copy budgets and checks
failed-worker isolation, repeated application, cache hydration and saved-state
isolation. These are bounded fixtures, not a production-scale timing benchmark.

## Remaining work

Cold project hydration still copies restored site models and computes complete
shared-input baselines. Changed sources still need publication and AMT map
decoding. A fully approved production-scale new solve needs a separate
validation case; the retained diagnostic project has missing approvals.
