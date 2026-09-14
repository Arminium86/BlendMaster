# v0.3.326 change and publication receipt

Verified 14 September 2026 against committed baseline `becfca8c84f3a5098fdf89d3880b687d80cde641` plus the local changes in this handover. The baseline has 326 reachable commits including merges, starting 20 October 2024. The release label records this baseline; it is not an automatic Semantic Versioning calculation.

## Application changes

- AMT chunk configuration now has nine columns for footprint participation, tonnes, reclaim rate, target hours and calculated chunks. Grade vectors, lineage, coverage and inventory-match metadata were removed from this table. Domain data and Database View remain available.
- Removed the unused project-loading lineage-display precomputation.
- `AppVersion.py` supplies the window title and application identity. The renamed `BlendMaster.spec` reads this version for its executable name. The debug packaging spec includes the active splash and icon.
- The active background and startup splash carry v0.3.326. Both images and the rendered 720 × 480 startup splash were visually inspected.

## Validation

- Full regression suite: **1,269 passed in 50.175 seconds**, using Python 3.12 and unittest discovery. The existing AMT screen-contract test was updated to match the requested nine-column presentation.
- A native Windows Qt fixture rendered the nine-column table with readable text. At 300,000 WMT, 2,000 t/h and 72 target hours, the calculation returned two chunks of 150,000 WMT. This is a fixture check, not another full retained-project solve.
- `git diff --check` passed; only normal Windows line-ending notices were reported.
- The retained project was read for inventory/checksum and was not rewritten. SHA-256: `9B3E24CDE183B5EEC8F91C8F7E29A8A3C35106103404A27FE45094750C42CD23`.
- Historical end-to-end project evidence and outstanding target/assay/performance findings are recorded on pages 00 and 08. The 1,269-test run does not establish production acceptance.

The test log and Qt captures are local working evidence under the Playground workspace (`bm_handover_tests.log` and `bm_handover_qa`). They do not contain a release executable. The executable has not been rebuilt, and no changes have been committed or pushed.

## Outline publication

The existing collection overview and all 13 documents (00–12) were rewritten in place. Document IDs and revision history were preserved; Outline updated the title slugs and old URLs still resolved during verification.

Each saved document was reopened and compared with the authored source: every paragraph, section heading and table cell matched after whitespace normalization, with no literal HTML left in the content. Both Mermaid architecture diagrams rendered as diagrams. The collection overview was separately reloaded and checked for complete replacement and its 13 navigation links.

| Page | Checked text blocks | Tables |
|---|---:|---:|
| 00 | 33 | 1 |
| 01 | 52 | 2 |
| 02 | 60 | 2 |
| 03 | 27 | 1 |
| 04 | 49 | 1 |
| 05 | 37 | 1 |
| 06 | 46 | 1 |
| 07 | 40 | 1 |
| 08 | 57 | 1 |
| 09 | 21 | 0 |
| 10 | 36 | 1 |
| 11 | 70 | 2 |
| 12 | 60 | 2 |

Collection overview: 32 checked text blocks, with the old overview removed. The editable sources, original resolving URLs and baseline/project metadata are retained beside this receipt and in `release-manifest.json`.

## Image generation record

Tool/mode: built-in ImageGen, edit mode with each original local image supplied as reference. Outputs replaced `resources/background_v4.PNG` and `resources/splash_v2.png` after visual inspection.

- Background edit brief: preserve the existing illustration, Fortescue logo, typography and layout; replace the embedded version with `(PoC v0.3.326)`.
- Splash edit brief: preserve the original composition, logo and loading treatment; update the version to v0.3.326 and the banner to `BlendMaster PoC v0.3.326 - 2026 Fortescue - MOPP`.

The icon assets contained no release label and did not need editing. Historical fallback artwork is not the currently selected background or splash.
