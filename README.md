# Sixtyfour Workflow Studio

A local visual workflow builder for data research, enrichment, and cleanup. Connect and configure blocks, run a workflow, inspect each step, and download the resulting CSV.

Built with React, TypeScript, Vite, React Flow, FastAPI, and pandas.

## Capabilities

The application includes 18 tools:

| Area | Tools |
| --- | --- |
| CSV and data preparation | Read CSV, Save CSV, Filter rows, Inspect data, Remove duplicates, Merge data |
| Search and discovery | Find prospects, Search by filters, Search fields, Field values |
| Research and enrichment | Enrich people, Research companies, Find decision makers, Find email, Find phone, Reverse email, Enrich LinkedIn, Qualify leads |

### Manual workflows

- Configure blocks through forms and JSON fields, including mappings from CSV columns to API fields.
- Validate workflow connections, configuration, and dataset compatibility before execution.
- Stream execution progress and inspect row counts, columns, and previews of the first 20 rows at each completed step.
- Inspect provider responses and row errors, and download CSV exports.
- Combine the current dataset with earlier snapshots from the same run.

Workflows follow one connected path. Branching and relational joins are not supported; **Merge data** appends compatible snapshots.

Example workflows:

```text
Read CSV → Filter rows → Remove duplicates → Save CSV

Read CSV → Enrich people → Find email → Save CSV

Search by filters → Research companies → Find decision makers → Save CSV
```

## Run locally

Requires Python 3.10+ and Node.js 20.19+ within Node 20, or Node.js 22.12+.

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
```

Set `SIXTYFOUR_API_KEY` in your shell or in a local `backend/.env` file. Never commit credentials. API-backed tools require this key; local CSV tools do not.

```bash
./start.sh
```

Open http://127.0.0.1:5173 for the application or http://127.0.0.1:8000/docs for the backend API documentation. Press Ctrl+C to stop both servers.

Override ports with `BACKEND_PORT=8001 FRONTEND_PORT=5174 ./start.sh`. Set `PYTHON` to use another Python interpreter.

## Repository structure

- `backend/blocks/`: tools and shared API/data helpers.
- `backend/engine/`: registry, graph validation, and execution.
- `backend/tests/`: offline workflow and compatibility tests.
- `frontend/src/components/`: canvas, catalog, settings, and results.
- `inputs/example_workflow.csv`: synthetic sample dataset.

Local credentials, uploaded data, generated output, and dependencies are excluded from Git.

## Data and execution behavior

- **CSV input:** Upload UTF-8 CSV files up to 10 MB, or read CSV files inside `inputs/` and `outputs/`. File paths resolve relative to the repository root.
- **Identifier preservation:** CSV values load as strings to preserve leading zeroes and phone prefixes. Numeric filters can compare numeric conversions of those values.
- **Dataset compatibility:** The backend tracks people, company, unknown, and metadata datasets and validates tool inputs before execution.
- **Result inspection:** Step previews show the first 20 rows. Use CSV exports for the full result.
- **Exports:** Files are written under `outputs/runs/<run-id>/` with step-specific filenames. Separate runs do not overwrite each other’s exports.
- **Row failures:** API tools can continue after individual row failures. Results retain error information, and runs with failed rows are reported as partial.
- **Cancellation:** Stop prevents further local submissions and polling. Remote jobs already submitted may still complete and incur charges.

See [CHANGELOG.md](CHANGELOG.md) for the manual workflow change history.
