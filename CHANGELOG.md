# Optional Jev routing — 2026-09-27

- Route on selected-choice probability separately from TypeSafe confidence, with
  a configurable 60% default cutoff and a 15-point lead over the runner-up.
  Focus the routing question on the next useful action and explain fallback scores.
- Added an off-by-default Jev switch with a purple enabled state and per-run mode.
- Added a direct TypeSafe evaluation client and contract-derived next-tool candidates
  without replacing the existing planner, graph, tool runner, or completion checks.
- Added confidence-based planner fallback, bounded requests, cancellation support,
  usage counters, and preservation of Jev mode across clarification and recovery.
- Added offline routing/API/transport regressions and a mocked Jev browser fixture.
- Verified the toggle and CSV cleanup end to end with the offline browser fixture.

# LangGraph agent mode — 2026-09-27

- Added a separate agent workspace with natural-language requests, optional CSV
  uploads, live plans/tool progress, preview tables, and automatic CSV exports.
- Added a manual/agent toggle while preserving the canvas and chat state.
- Implemented a LangGraph decision/execute/inspect loop using existing tool logic.
- Enforced tool/model budgets, duplicate detection, no-progress and failure stops,
  frozen completion checks, cancellation, and clarification without budget resets.
- Generated prompt stopping rules from the same backend limits; no global deadline.
- Added explicit planner setup, session-local streaming/recovery endpoints, and
  configurable research/search job polling bounds.
- Added offline adversarial loop tests and an isolated browser test fixture.

# One module per tool — 2026-09-26

- Moved enrichment request construction and response handling into the eight
  individual enrichment tool modules; replaced `enrichment.py` with generic
  `row_processing.py` helpers.
- Split `search.py` into four search tool modules plus `search_helpers.py`.
- Split `data.py` into `inspect_dataset.py`, `deduplicate_rows.py`, and
  `merge_datasets.py`.
- Moved CSV execution adapters into their respective CSV modules.
- Updated the registry to import every tool directly; removed tool-name dispatch.
- Updated regression tests to execute through the registry and documented the
  module responsibilities in README. Preserved block IDs and UI configuration.

# Manual blocks / API refresh — 2026-09-26

## Added 13 blocks (18 total)

| Block | Type identifier | Operation |
| --- | --- | --- |
| Inspect data | `inspect_dataset` | Local column statistics and preview; table passes through |
| Merge data | `merge_datasets` | Append selected earlier block snapshots to the current table |
| Remove duplicates | `deduplicate_rows` | Exact composite-key deduplication; missing keys preserved |
| Research companies | `research_companies` | `/company-intelligence-async` |
| Find decision makers | `find_decision_makers` | Company Intelligence with `find_people` and `lead_struct` |
| Find phone | `find_phone` | `/find-phone-async` |
| Reverse email | `reverse_email` | `/reverse-email-async` |
| Enrich LinkedIn | `enrich_linkedin` | `/enrich-linkedin` |
| Qualify leads | `qualify_leads` | `/qa-agent-async`; explicit user-defined criteria |
| Find prospects | `find_prospects` | Deep search submission/polling, then `/search/query` |
| Search by filters | `search_by_filters` | `/search/query`, paginated up to the result cap |
| Search fields | `get_search_fields` | `/search/filter-capabilities` |
| Field values | `get_search_field_values` | `/search/filter-field-values` |

## Updated the original five blocks

- **Read CSV:** upload support, bounded input paths, UTF-8 handling, duplicate
  header validation, identifiers preserved as strings, stable row IDs.
- **Save CSV:** configurable filename, per-run/per-step paths, browser downloads,
  no pandas index, JSON serialization for nested values.
- **Filter:** structured predicates, all/any logic, numeric comparisons, and
  missing-value handling. Legacy Pandas expressions use an interpreted allowlist;
  removed arbitrary Python `eval` while preserving common existing expressions.
- **Enrich lead → Enrich people:** preserved `enrich_lead` block identifier,
  migrated endpoint to `/people-intelligence-async`; custom fields, mappings,
  research instructions, depth, confidence, overwrite/partial-error options.
  Old `fields` arrays still work and `github` maps to `github_url`.
- **Find email:** replaced old `bruteforce` / `only_company_emails` request fields
  with `mode` and `verify_emails`; retained validation/type information and
  separate work/personal email fields; configurable skip/replace behavior.

## Editor changes

- Fetch one shared backend catalog instead of duplicating block definitions.
- Keep the React Flow canvas, current appearance, inspector and single-path graph.
- Render settings for all blocks, with editable JSON, checkboxes, selects, numeric
  ranges, merge-snapshot selection and documentation links.
- Add a search starter alongside the existing CSV starter.
- Add per-step Results tab with first-20-row table, counts, failure counts,
  statistics/provider metadata, expandable full cell values and file download links.
- Add upload control, API-key configuration indicator, progress messages and Stop.
- Stop propagates local cancellation; it does not cancel already submitted remote
  jobs. Previews no longer require ending with Save CSV.

## Backend changes

- Activate the existing registry; all 18 block runners share the existing engine.
- Validate empty/unknown/duplicate blocks, dangling edges, branching, cycles,
  unreachable blocks, source placement, configuration and merge references.
- Add a shared Sixtyfour client with environment credentials, bounded retries,
  job deadlines, terminal-status handling and safe transport errors.
- Preserve up to eight concurrent enrichment rows with stable output order.
- Preserve row IDs, parent IDs on expansion/merge, raw API responses, evidence,
  latest errors and per-row API history. Report partial runs explicitly.
- Stream progress, result previews and heartbeat events over the existing NDJSON
  route; use per-run state rather than a shared final output filename.
- Move existing hardcoded key to ignored `backend/.env` and add `.env.example`.
- Add `/blocks`, `/uploads`, and `/downloads/{run_id}/{filename}`. Keep
  `/execute_stream` and the existing `blocks`/`connections` request structure.

## Files

Existing files changed:
- `backend/main.py`, `backend/schemas.py`
- `backend/engine/build_flow.py`, `backend/engine/registry.py`
- `backend/blocks/enrich_lead.py`, `find_email.py`, `filter.py`, `read_csv.py`, `save_csv.py`
- `frontend/src/components/BlockEditor.tsx`, `frontend/src/lib/types.ts`, `frontend/src/index.css`
- `frontend/vite.config.ts` (optional backend proxy target environment setting)
- `frontend/src/components/BlockNode.tsx`, `Canvas.tsx` (legacy config type cleanup;
  legacy GitHub field spelling corrected)

New files, within the existing structure:
- `backend/blocks/catalog.py`, `sixtyfour.py`, `enrichment.py`, `search.py`, `data.py`
- `backend/engine/execution.py`
- `frontend/src/components/BlockSettings.tsx`
- `backend/tests/test_workflows.py`, `backend/tests/api_contract_snapshot.json`
- `backend/requirements.txt`, `backend/.env.example`, ignored `backend/.env`
- `inputs/example_workflow.csv` (synthetic, local-only demonstration data)
- `.gitignore`, `README.md`, `CHANGELOG.md`

No application agent, framework migration, database or new top-level code package
was introduced. Generated uploads, run CSVs, Python caches and frontend builds
are excluded from version control.

## Documentation used

- [Live OpenAPI schema](https://api.sixtyfour.ai/openapi.json)
- [People Intelligence](https://docs.sixtyfour.ai/api-reference/endpoint/people-intelligence)
- [Company Intelligence](https://docs.sixtyfour.ai/api-reference/endpoint/company-intelligence)
- [Find Email](https://docs.sixtyfour.ai/api-reference/endpoint/find-email)
- [Find Phone](https://docs.sixtyfour.ai/api-reference/endpoint/find-phone)
- [Reverse Email](https://docs.sixtyfour.ai/api-reference/endpoint/reverse-email)
- [LinkedIn enrichment](https://docs.sixtyfour.ai/api-reference/enrichment/enrich-linkedin-profile)
- [QA Agent](https://docs.sixtyfour.ai/api-reference/endpoint/qa-agent)
- [Deep Search lifecycle](https://docs.sixtyfour.ai/api-reference/search/search-endpoints)
- [Search Query](https://docs.sixtyfour.ai/api-reference/search/search-query)
- [Filter discovery](https://docs.sixtyfour.ai/api-reference/search/filter-search)
- [Structured output types](https://docs.sixtyfour.ai/guides/struct-and-type-casting)

## Validation

- 46 offline backend regression tests, including request-key checks against the
  public schema, pagination, terminal errors, concurrency/order, provenance,
  graph validation and legacy filters.
- Frontend TypeScript/Vite production build and ESLint.
- Local HTTP smoke: catalog, upload, read/filter/dedup/merge/inspect/save chain,
  streamed previews, CSV download, invalid graph/download and invalid UTF-8.
- Browser: CSV workflow completes with a four-row preview and download link;
  all 13 new blocks expose settings; invalid JSON shows inline validation.
- No billable Sixtyfour calls made. See README for upstream response-schema
  limitations and manual API trial instructions.
