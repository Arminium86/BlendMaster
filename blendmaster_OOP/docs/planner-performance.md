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

## Remaining work

Cold project hydration still copies restored site models and computes complete
shared-input baselines. Changed inventory submissions still perform canonical
mapping, publication and AMT map decoding. These remain opportunities for further
improvement; the unchanged-input fast path does not remove necessary changed-input
work. A fully approved production-scale new solve needs a separate validation
case; the retained diagnostic project has missing approvals.
