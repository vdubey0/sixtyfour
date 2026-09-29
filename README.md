# Sixtyfour Workflow Studio

A local web application for building and running data research, enrichment, and cleanup workflows. Start with a CSV or search for people and companies, process the results with Sixtyfour’s APIs and local data tools, and download the resulting dataset.

The application provides two workspaces:

- **Manual:** Build a workflow visually by connecting and configuring blocks. Run it, follow progress, and inspect each completed step.
- **Agent:** Describe the dataset you want in plain English, optionally attach a CSV, and let an agent plan and execute the workflow. Review its actions, answer clarification questions, and download the result.

Switching between workspaces preserves the canvas and conversation and keeps active runs running. Both workspaces use the same backend tools.

The frontend uses React, TypeScript, Vite, and React Flow. The backend uses FastAPI and pandas, with LangGraph orchestrating agent runs.

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

### Agent workflows

- Turn a plain-English request into a plan and explicit completion criteria.
- Select and execute tools incrementally using the results of earlier steps.
- Show action summaries, tool inputs, output previews, and live progress.
- Pause for clarification and check results against the initial completion criteria.
- Attempt to export the latest completed dataset when a run finishes or stops.

Agent runs have bounded tool attempts, model requests, and dataset sizes. Outcomes distinguish completed, partial, failed, and cancelled runs.

An optional **Jev** mode uses TypeSafe to select among supported actions, falling back to the OpenAI planner when needed. It supplements the planner and requires additional configuration.

## Repository structure

```text
.
├── README.md                   # Project overview and local setup
├── CHANGELOG.md                # Detailed change history
├── start.sh                    # Start both development servers
├── backend/
│   ├── main.py                 # FastAPI app, workflow, upload, and download routes
│   ├── schemas.py              # Workflow request and response models
│   ├── requirements.txt        # Python dependencies
│   ├── blocks/                 # 18 tools and shared API/data helpers
│   ├── engine/                 # Registry, graph validation, and execution
│   ├── agent/                  # Planner, LangGraph loop, limits, and Jev routing
│   └── tests/                  # Regression tests and offline browser fixture
├── frontend/
│   ├── package.json            # Dependencies and development scripts
│   ├── vite.config.ts          # Development server and backend proxy
│   └── src/
│       ├── App.tsx             # Manual/agent workspace switch
│       ├── components/         # Workflow canvas, catalog, settings, and results
│       ├── agent/              # Agent chat, streaming client, and styles
│       └── lib/                # Shared types and connection helpers
├── inputs/
│   ├── example_workflow.csv    # Synthetic sample dataset
│   └── uploads/                # Uploaded CSV files, created as needed
└── outputs/
    └── runs/                   # CSV exports grouped by run
```

Each tool has its own module in `backend/blocks/`. Shared helpers handle API transport, asynchronous job polling, pagination, and row processing. Manual execution and agent execution call the same tool registry.

## Run locally

### Prerequisites

- **Python 3.10 or newer**
- **Node.js 20.19.x or newer within Node 20, or Node.js 22.12 or newer**, with npm
- A Bash-compatible environment for `start.sh`

Sixtyfour tools require a Sixtyfour API key. Agent mode additionally requires OpenAI configuration.

Run the following commands from the repository root.

### 1. Install dependencies

```bash
python3 -m venv backend/.venv-agent
backend/.venv-agent/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
```

The virtual environment name matches the default interpreter used by `start.sh`; activating it is not required.

### 2. Configure credentials

Create the ignored local configuration file if it does not already exist:

```bash
if [ ! -f backend/.env ]; then
  touch backend/.env
fi
```

Edit `backend/.env` for the features you want to use:

| Setting | Required for |
| --- | --- |
| `SIXTYFOUR_API_KEY` | Sixtyfour search, research, and enrichment tools |
| `OPENAI_API_KEY` | Agent planning |
| `AGENT_MODEL` | Agent planning; choose an OpenAI Responses API model supporting strict function calling |
| `TYPESAFE_API_KEY` | Optional Jev routing in agent mode |

There is no default agent model: set `AGENT_MODEL` explicitly. Manual mode does not require OpenAI or TypeSafe credentials.

Credentials remain on the backend. Exported environment variables take precedence over values in `backend/.env`.

### 3. Start the application

```bash
./start.sh
```

Open:

- **Application:** [http://127.0.0.1:5173](http://127.0.0.1:5173)
- **Backend API documentation:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

The script starts both servers with development reload enabled. Press **Ctrl+C** to stop both.

To use different ports:

```bash
BACKEND_PORT=8001 FRONTEND_PORT=5174 ./start.sh
```

The script automatically configures the frontend proxy for the selected backend port. To use a different Python environment, set `PYTHON` to its interpreter path:

```bash
PYTHON=/absolute/path/to/venv/bin/python ./start.sh
```

### Start the servers separately

Alternatively, run these commands in two terminals from the repository root.

**Backend:**

```bash
backend/.venv-agent/bin/python -m uvicorn backend.main:app \
  --reload \
  --reload-dir backend \
  --host 127.0.0.1 \
  --port 8000
```

**Frontend:**

```bash
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Vite forwards `/api` requests to the backend at port 8000. If you start the backend on another port, set the proxy target when starting the frontend:

```bash
SIXTYFOUR_API_TARGET=http://127.0.0.1:8001 \
  npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

## Configuring workflows

A workflow starts with **Read CSV** or a search source. Search blocks must be first; they cannot replace a table partway through a workflow. A workflow can end at any block for a preview. Add **Save CSV** for a full downloadable file.

Additional workflow examples:

```text
Read CSV → Reverse email → Qualify leads → Filter rows → Save CSV

Search fields → Save CSV

Field values → Save CSV
```

Use **Search fields** and **Field values** to inspect the supported search vocabulary before configuring a search. Each API block includes a documentation link, and JSON editors flag syntax errors before execution.

### Column mappings and research fields

Column mappings map API field names to CSV column names. For example, a people dataset might use:

```json
{"name": "full_name", "company": "employer", "linkedin": "linkedin_url"}
```

For company research, map company identifiers:

```json
{"company_name": "company", "domain": "website_domain"}
```

An empty mapping sends all non-internal columns. Company research supports plain descriptions and typed descriptions for requested fields:

```json
{
  "industry": "Primary industry",
  "employee_count": {
    "description": "Approximate employee count",
    "type": "int"
  }
}
```

LinkedIn and qualification output fields use plain descriptions in this editor. Qualification criteria require unique names and descriptions. The editor validates criterion thresholds on a **0–1 scale**; the default requested `qualification_score` is separately defined as **0–10**. Invalid numeric scores become null, and unrecognized verdicts become `unknown`.

## Data and execution behavior

### CSV input and output

Upload UTF-8 CSV files up to 10 MB, or read existing CSV files inside `inputs/` and `outputs/`. Paths resolve relative to the repository root, independently of the server’s working directory. Headers must be unique and nonempty.

CSV values load as strings to preserve leading zeroes, identifiers, and phone prefixes. Numeric filters compare numeric conversions of those values.

Exports are written to `outputs/runs/<run-id>/<step>-<filename>.csv`. Separate runs do not overwrite each other’s exports. Nested values become JSON strings, and exports do not include a pandas index column.

### Results and enrichment

- Each completed step exposes its row count, columns, and first 20 rows. Select a result step or a completed canvas node to inspect it. Click a nonempty cell to expand its full value, including formatted JSON.
- **Inspect data** adds missing-value, distinct-count, and type statistics. Search discovery blocks also expose provider metadata.
- Enrichment fills missing values unless **Replace existing values** is enabled. Email and phone tools can skip rows with existing values. To force email rediscovery, disable skipping and enable replacement.
- Row enrichment defaults to eight concurrent rows, configurable from one to eight. Output retains input order.
- `_status` and `_error` describe the latest API step. `_raw_response` preserves its provider JSON, while `_history` retains previous API steps and errors. Evidence and confidence fields are kept when supplied by the provider.
- Search follows cursors until the configured result cap or exhaustion, including when intermediate pages are short or empty. Exclusions are reapplied when browsing completed deep searches.

### Errors, retries, and cancellation

Individual API row failures can continue by default or stop the block, depending on configuration. Authentication, permission, and insufficient-credit errors stop execution. Runs containing failed rows are reported as partial.

Rate-limit responses receive bounded retries. Ambiguous POST transport failures are surfaced without automatic resubmission, since the provider may already have accepted a billable request. Asynchronous polling handles failed, cancelled, terminated, and timed-out jobs.

**Stop** prevents further local submissions and polling. It cannot undo a remote job already submitted to Sixtyfour; that job may complete and incur charges.

## Dataset compatibility

`backend/engine/compatibility.py` defines the contracts for all 18 blocks. Graph validation predicts dataset modes and columns before execution; runtime validation checks the actual columns before each block.

| Operation | Dataset behavior |
| --- | --- |
| Read CSV | Accepts `people`, `companies`, or `unknown`; defaults to `unknown` |
| Search sources | Determine dataset mode from the configured search |
| Search fields / Field values | Produce metadata, which enrichment blocks reject |
| Find decision makers | Transitions a company dataset to people |
| Company research on people | Requires explicit company mappings and preserves people mode |
| Merge data | Appends snapshots with the same mode and unions their columns |

Mappings, configured email/LinkedIn columns, reference columns, deduplication keys, and filter columns are checked centrally. Dynamic provider fields are checked against actual output at runtime; requesting a research field does not guarantee it will be present or nonempty.

Row checks run before API requests within the tool’s error-handling policy. Reverse email requires one address per row. LinkedIn URLs must match the known dataset mode, and evidence references must be HTTP(S) URLs. Failed company-discovery rows cannot trigger subsequent enrichment calls. Ambiguous person/company column collisions fail explicitly instead of overwriting data.

### Existing workflow compatibility

The `read_csv`, `save_csv`, `filter`, `enrich_lead`, and `find_email` identifiers remain valid. Legacy enrichment `fields` arrays are converted to `struct`, and `github` is normalized to `github_url`.

Legacy filter `rule` expressions support a restricted pandas subset: `df[...]`, comparisons, `&`, `|`, `~`, null checks, `isin`, and common string methods. An allowlisted syntax-tree interpreter evaluates them without Python `eval`. Use structured filter rules for new workflows.

## Agent execution and configuration

In **agent** mode, describe the desired dataset and optionally attach a CSV. The agent loads the CSV or starts from a search, publishes a plan and completion contract, and chooses one tool at a time using the preceding results.

The conversation includes action summaries, row/job progress, expandable tool inputs, output previews, and final downloads. Full datasets remain on the server; bounded samples and profiles are sent to the configured planner. External research and enrichment tools receive the data needed for their API requests.

### Completion and stopping policy

The initial completion contract is frozen. The backend checks row bounds, entity mode, required nonempty columns, deduplication keys, and explicit conditions against the full result. Failed rows and unresolved qualitative requirements prevent a successful completion. A verified-email requirement must be backed by a condition on actual verification data.

Terminal outcomes are `completed`, `partial`, `failed`, and `cancelled`. `needs_input` pauses for clarification. Only a paused run can resume, and clarification does not reset its counters or completion contract.

At each stop, the backend attempts to export the latest completed dataset, if one exists, and supplies a deterministic summary even if the model-request budget is exhausted. Final export is attempted once and cannot restart the decision loop. Empty tables retain their columns.

Stop and stream disconnects prevent subsequent submissions and polling. If a chat stream is interrupted, **Recover result** retrieves the latest completed snapshot. Switching workspaces preserves an active run.

### Agent budgets

`backend/agent/limits.py` defines the limits, and `prompts.py` includes them in each model request. The model also receives remaining budgets and observed completion checks.

| Setting | Default / hard ceiling | What it bounds |
| --- | --- | --- |
| `AGENT_TOOL_ATTEMPTS` | 20 | Executed tools, failed executions, and initial CSV loading; automatic final export is separate |
| `AGENT_MODEL_REQUESTS` | 30 | Planning, decisions, argument repair, and transport retries |
| `AGENT_INVALID_DECISIONS` | 2 | Consecutive malformed decisions, invalid arguments, incompatible tools, duplicate actions, or premature completion |
| `AGENT_TOOL_FAILURES` | 2 | Consecutive failed executions, including all-row-failed results |
| `AGENT_NO_PROGRESS` | 3 | Consecutive executions without new public dataset content, quality state, or inspection/discovery metadata |
| `AGENT_MODEL_TIMEOUT` | 60 seconds | Each planner request; a transient failure may retry once within the request budget |
| `AGENT_MAX_ROWS` | 5,000 | Input and working datasets; oversized outputs preserve the preceding snapshot |

Environment settings can lower these limits but cannot raise their hard ceilings. Duplicate detection considers the tool, normalized configuration, and input content rather than snapshot IDs. Failed attempts are recorded before execution to prevent resubmission of an unchanged, potentially billable request.

Changes to internal row IDs, raw-response history, row ordering, or a return to earlier data do not count as progress. Authentication, permission, and insufficient-credit errors stop immediately. LangGraph’s recursion limit provides an additional backstop.

### Remote job timeouts

There is no overall agent run deadline. Waiting for a remote job does not consume another tool attempt or count as a no-progress step. Individual HTTP requests and job polling are bounded separately.

| Setting | Behavior |
| --- | --- |
| `SIXTYFOUR_JOB_TIMEOUT` | Fallback job timeout; defaults to 900 seconds |
| `SIXTYFOUR_SEARCH_JOB_TIMEOUT` | Optional deep-search override; the example configuration sets 3,600 seconds |
| `SIXTYFOUR_RESEARCH_JOB_TIMEOUT` | Optional research override; the example configuration sets 1,800 seconds |

Search and research overrides are capped at one day per job. These values bound polling; they do not predict provider completion times.

### Optional Jev routing

Set `TYPESAFE_API_KEY` in `backend/.env`, then enable **Jev** before starting an agent run. The toggle is off by default, appears purple when enabled, and remains fixed during execution and clarification. Start a new chat to change a paused run’s mode. Reloading the UI returns the toggle to its default.

`backend/agent/jev.py` calls TypeSafe’s `/v1/systemone` endpoint with `jev-latest`. After the OpenAI planner establishes the completion contract, Jev selects among validated actions with known arguments or defers to the planner. OpenAI credentials and an explicit `AGENT_MODEL` remain required.

Supported candidates include contract-based filtering and deduplication, plus missing professional-email, personal-email, or phone lookups on known people datasets with recognized identifier columns. Local reductions must preserve the requested minimum row count. Custom mappings, searches, research configurations, unresolved requirements, and recovery use the planner. Deterministic contract checks remain responsible for completion.

A selection must meet both a minimum choice probability of **0.6** and a **0.15** lead over the runner-up. `JEV_MIN_PROBABILITY` can set the probability cutoff from 0.5 to 1. Choice probability drives routing; TypeSafe’s separate confidence score is reported independently.

Each Jev request has a maximum timeout of five seconds, or the lower configured model timeout, with no retries. Requests count toward the existing model-request budget, with one request reserved for planner fallback. A transport or malformed-response failure disables Jev for the remainder of the run.

Streamed `jev_decision` events expose selections, fallbacks, confidence, and latency. Run metrics include planner/Jev request counts, selections, fallbacks, cumulative Jev latency, token usage, and a nullable Jev cost field. These metrics do not establish total run cost or a measured speedup.

## Backend architecture and API

### Shared tools

Each tool module in `backend/blocks/` owns its request construction, response handling, and execution entry point. The engine registry imports those entry points directly.

| Module | Responsibility |
| --- | --- |
| `blocks/row_processing.py` | Column mapping, concurrency, cancellation, result merging, evidence history, and partial failures |
| `blocks/search_helpers.py` | Record conversion and cursor pagination |
| `blocks/sixtyfour.py` | Credentials, HTTP requests, retries, and asynchronous polling |
| `blocks/catalog.py` | UI field definitions, configuration defaults, and validation |
| `engine/build_flow.py` | Single-path graph validation and execution ordering |
| `engine/compatibility.py` | Dataset contracts and column validation |
| `engine/execution.py` | Workflow execution, step results, and progress events |

### Agent components

| Module | Responsibility |
| --- | --- |
| `agent/graph.py` | LangGraph decision → execute → inspect loop and run state |
| `agent/tools.py` | Registry adapters, argument validation, action fingerprints, and completion checks |
| `agent/model.py` | Bounded OpenAI Responses API transport |
| `agent/prompts.py` | Planner instructions and decision context |
| `agent/limits.py` | Server-owned execution budgets |
| `agent/jev.py` | Optional TypeSafe action routing |
| `agent/routes.py` | Agent configuration, streaming, cancellation, and recovery routes |
| `frontend/src/agent/` | Chat UI, stream client, previews, and styles |

Dataset snapshots stay on the server. Discovery metadata has separate state and cannot replace a people/company table; metadata requests can explicitly target a metadata result.

See the running backend’s [interactive API documentation](http://127.0.0.1:8000/docs) for request schemas and [CHANGELOG.md](CHANGELOG.md) for the detailed change history.
