# IssueDeck

A PyQt6 desktop tool for pulling Jira issues assigned to you, filtering/auditing
them, and exporting them into formats suited to LLM ingestion and reporting:
combined Markdown, per-ticket Markdown, JSONL, and CSV.

It is aimed at consultants and engineers who need to periodically snapshot their
Jira work — with descriptions and comments — into portable text a language model
or a spreadsheet can consume, without granting a third-party service access to
the Jira instance.

## Quick start

From install to a first export in a few minutes:

1. **Install** (Python **≥ 3.11**):
   ```bash
   git clone https://github.com/phertfelder/issue_deck.git
   cd issue_deck
   python -m venv .venv
   # Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
   python -m pip install -e ".[keyring]"
   ```
2. **Run** it — `issue-deck` (or `python -m issue_deck`). On first launch a
   short **onboarding wizard** asks for your deployment (Cloud or Server/DC),
   URL, and token, lets you **Test connection**, and sets your default query
   authoring mode. You can **Skip** it and use the Connection tab instead.
3. **Fetch or import** — on **Query & Results**, choose filters and click
   **Fetch**, or **Import CSV…** to load an existing export locally (no Jira
   access needed).
4. **Filter** — refine with the workbench (or raw JQL); **Preview JQL** shows the
   exact query before you run it. Save a filter set as a **view** to reuse.
5. **Export** — click **Export…** and pick Markdown / JSONL / CSV / a pack.

Change any default later in **File → Settings** (which can also edit your
connection credentials); see the version, config location, and token-storage
status in **Help → About**. Settings and data live
in a platform-native folder (see [*Where settings are stored*](#where-settings-are-stored)).
The sections below cover each step in detail.

## 1. What the app does

- Connects to a Jira instance with your own credentials (nothing is proxied
  through any external service).
- Searches issues **assigned to the current user**, narrowed by a set of
  filters (status, issue type, severity, client, free text, recency).
- Fetches each issue's fields and, optionally, its comment thread (all / none /
  latest N / since a date).
- Converts Atlassian Document Format (ADF, Jira Cloud) and wiki/plain bodies
  (Server/DC) to Markdown-ish text.
- Can also **import a local CSV** (an existing Jira/related export) into the same
  working dataset — schema-only, no Jira API involved.
- Lets you **discover real filter values** from the current data or a bounded
  Jira sample and pin them as filters.
- Exports the result set as combined Markdown, one Markdown file per ticket,
  JSONL, or CSV.

The UI has two tabs: **Connection** (credentials + custom-field ids) and
**Query & Results** (filters, saved views, results table, issue detail panel,
export buttons). Fetching, CSV parsing, and value sampling run off the UI thread
so the window stays responsive.

> **For maintainers / LLM agents:** see [`docs/llm/`](docs/llm/) — `CONTEXT.md`,
> `FEATURE_MAP.md`, `INVARIANTS.md`, `WORKFLOWS.md`, `ROADMAP.md`, `GAPS.md`.

## 2. Supported Jira deployments

| Deployment | Search API | Pagination | Body format | Auth |
|---|---|---|---|---|
| **Cloud** | `/rest/api/3/search/jql` | `nextPageToken` | ADF (JSON) | Basic: email + API token |
| **Server / Data Center** | `/rest/api/2/search` | `startAt` | wiki / plain text | Bearer: Personal Access Token |

The deployment type is selected on the Connection tab and determines both the
endpoint and the authentication scheme. Both are fully supported.

## 3. Authentication setup

Credentials are entered on the **Connection** tab. Pick the deployment type
first — it changes which fields are required.

### Jira Cloud (email + API token)

1. Create an API token at
   <https://id.atlassian.com/manage-profile/security/api-tokens>.
2. On the Connection tab set:
   - **Base URL**: `https://yourco.atlassian.net`
   - **Deployment**: *Cloud (API token)*
   - **Email**: your Atlassian account email (used as the Basic-auth username)
   - **Token**: the API token
3. Click **Test connection** — it calls `/rest/api/3/myself` and shows your
   display name on success.

### Jira Server / Data Center (Personal Access Token)

1. In Jira, go to your profile → **Personal Access Tokens** → create a token.
2. On the Connection tab set:
   - **Base URL**: `https://jira.yourco.com`
   - **Deployment**: *Server / Data Center (PAT)*
   - **Email**: leave blank (not used; PAT is sent as a Bearer token)
   - **Token**: the PAT
3. Click **Test connection** (calls `/rest/api/2/myself`).

### Custom fields (optional)

"Client" and "Severity" are usually custom fields whose ids differ per instance.
Enter their ids (e.g. `customfield_10050`) in the Connection tab, or click
**Discover fields…** to list all custom fields (id → name) and copy the right
ids. These ids enable the severity/client filters and columns.

## 4. Install from source

```bash
git clone https://github.com/phertfelder/issue_deck.git
cd issue_deck

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[keyring]"     # add ,dev for the test/lint tooling
```

Requirements: Python **>= 3.11**, plus `PyQt6` and `requests` (installed
automatically). The `keyring` extra enables OS-keychain token storage; the `dev`
extra adds `pytest`, `ruff`, `requests-mock`, and `mypy`.

## 5. Run commands

Any of these launch the GUI (they are equivalent):

```bash
issue-deck             # console script (after install)
python -m issue_deck    # module entry point
python issue_deck.py    # compatibility shim
```

The app needs a display. On a headless machine it will not start unless an X
server / virtual framebuffer is available (`QT_QPA_PLATFORM=offscreen` is for
tests, not interactive use).

### Build a standalone executable (optional)

To produce a local, self-contained executable (no Python install required to run
it), use [PyInstaller](https://pyinstaller.org) with the committed spec:

```bash
python -m pip install pyinstaller
pyinstaller packaging/issue_deck.spec
```

This writes a onedir bundle to `dist/JiraPuller/` — run the `JiraPuller`
executable inside it. Build on each target OS separately (a Windows executable
must be built on Windows, macOS on macOS, etc.). The generated `build/` and
`dist/` folders are gitignored and must **not** be committed; only
`packaging/issue_deck.spec` is version-controlled.

## 6. Security / privacy model

- **No third party.** All requests go directly from your machine to your Jira
  base URL using your credentials.
- **Tokens are never written to `config.json`.** The config file lives in the
  app's data directory (see [*Where settings are stored*](#where-settings-are-stored))
  and contains only connection metadata (base URL, deployment, email,
  custom-field ids, the "remember token" flag, and default authoring choices).
- **Token storage** (only when *Remember token* is checked):
  - With `keyring` installed → stored in the OS keychain.
  - Without `keyring` → a `token.txt` file in the data directory, created with
    best-effort `chmod 600`. POSIX permissions do **not** apply on Windows, so
    treat this fallback as plaintext-at-rest. The checkbox label reflects which
    mode is active.
  - Unchecking *Remember token* deletes the stored token (keychain entry and/or
    file). A **Forget token** button does the same on demand, and you are warned
    before a token is ever written to plaintext.
- **Exports contain issue data only** — never credentials, `Authorization`
  headers, the full base URL (only the *host*), or raw API responses.
- **Centralized redaction** ([`redaction.py`](issue_deck/redaction.py)) can mask
  issue keys, people names, emails, client names, URLs, comments, and
  descriptions — consistently across Markdown / JSONL / CSV / export packs. Use
  **Preview redaction…** in the export dialog to check the result first.
- **Redacted logging.** All logs run through a filter that scrubs tokens and auth
  headers; the HTTP client logs only method + path.
- Tokens are keyed by base URL, so multiple instances can be remembered
  independently.

See [SECURITY.md](SECURITY.md) for the full security & privacy model.

### Where settings are stored

All settings and app data (config, saved views, JQL templates, local notes,
CSV import profiles, and the token fallback file) live in a single per-user data
directory, resolved to the **platform-native** location:

| OS | Data directory |
|---|---|
| **Windows** | `%APPDATA%\JiraPuller\` |
| **macOS** | `~/Library/Application Support/JiraPuller/` |
| **Linux** | `${XDG_CONFIG_HOME:-~/.config}/issue-deck/` |

Set the `JIRA_PULLER_HOME` environment variable to override the location (useful
for portable installs or testing). The exact path is shown in **Help → About**.

**Migrating from older versions.** Versions of IssueDeck before this one stored
everything in `~/.issue_deck`. On first launch the app **automatically migrates**
that data (config, saved views, profiles) to the native location above — it never
overwrites an existing native config, and it prints a one-line notice when it
does so. Your token is migrated only when the OS keychain is unavailable (the
plaintext fallback); when `keyring` is installed you'll simply re-enter the token
so it lands in the keychain rather than a file. **The old `~/.issue_deck` folder
is left in place** — once you've confirmed the app works, you can delete it
manually.

## 7. Query / filter workflow

On the **Query & Results** tab:

1. Choose one or more **scopes** (OR-ed): *Assigned to me*, *Reported by me*,
   *Watched by me*. "Watched by me" is enabled only when the instance supports
   watcher search (detected automatically).
2. Select filters (all optional; combined with `AND`):
   - **Status / Issue type / Status category** — multi-select.
   - **Projects / Sprint / Fix version** — narrowing clauses.
   - **Severity** — matched against the configured severity custom field
     (`cf[...] = "value"`); ignored if no severity field id is set.
   - **Client contains** — substring match against the client custom field
     (`cf[...] ~ "value"`); ignored if no client field id is set.
   - **Text search** — `text ~ "…"` across summary/description/comments.
   - **Date windows** — updated / created / resolved within N days, due within N
     days (`0` = any). **Unresolved only** toggle.
   - **Pinned field filters** — a field/op/value table (ops `= ~ != >= <= in`);
     values are quoted/escaped. Use **Discover values…** to populate these from
     real data.
   - **Commented within (days)** — a **client-side** filter applied after fetch;
     requires comments to be loaded. Not part of the JQL.
   - **Extra JQL** — a raw clause appended verbatim (power-user escape hatch,
     wrapped in parentheses). Not escaped — you own its correctness.
   - **Raw JQL mode** — replaces the whole builder with a verbatim query (no
     scope or `ORDER BY` injected).
3. In non-raw mode every query is scoped by the chosen who-clause(s) and ordered
   `ORDER BY updated DESC`.
4. Choose a **comment mode** (All / None / Latest N / Since a date). Comments are
   fetched per issue, so large result sets take longer.
5. Click **Preview JQL** to see the exact generated query (and requested fields)
   before running it; a warning appears for very broad searches.
6. Click **Fetch** (or **Cancel** to abort). Progress is shown; the results table
   lists Key, Summary, Status, Type, Priority, Severity, Client, Assignee, Points,
   and Updated (columns toggleable). Select a row to see full details in the
   issue detail panel.
7. Refine filters and re-fetch as needed, then export. Save a filter set as a
   **view** to reuse it later (`views.json` in the data directory, no credentials).

## 8. CSV import workflow

CSV import is **implemented** and **local-only** — it never contacts Jira. Click
**Import CSV…** on the results toolbar to open a six-step wizard:

1. **Privacy** — a local-only notice and an optional *Redact issue keys* toggle.
2. **File** — choose a file, drag-and-drop, or paste content. Delimiter and
   encoding (UTF-8 / cp1252 / BOM) are detected; row/column counts are shown.
3. **Mapping** — each column gets a "Maps to" dropdown, pre-filled by
   auto-detection (key, summary, status, assignee, custom fields, …).
4. **Filters** — per-column type, coverage, unique count, and examples;
   recommended filters are pre-checked and can be pinned.
5. **Preview** — the normalized issues, group-by counts, and any warnings
   (missing required key/summary, duplicate keys, empty grouping fields).
6. **Commit** — **Replace** or **Merge** into the current dataset (with an
   on-conflict rule and an optional **Preview changes…** delta view), plus
   opt-in checkboxes to save a schema-only profile and/or the normalized dataset.

Privacy invariant (enforced, not aspirational — INV-CSV-LOCAL): raw CSV rows are
used transiently for inference/preview and are **never** serialized to
`config.json`, profiles, exports, or logs. Only the file basename is retained;
saved profiles hold column→field mappings only; saving a dataset (opt-in) writes
*normalized* issues, not raw rows. Nothing persists unless you explicitly opt in.

## 9. Export formats

All exports are produced from the fetched result set via the buttons under the
results table. Use *Load comments* before exporting if you want comment bodies.

| Format | Output | Good for |
|---|---|---|
| **Markdown (combined)** | One `.md` file: a header comment plus every issue, separated by `---`. | Pasting a whole batch of context into an LLM chat. |
| **Markdown (per-ticket)** | One `KEY.md` per issue in a chosen folder. | Per-document RAG / embedding pipelines. |
| **JSONL** | One JSON object per line (full normalized issue incl. comments). | Programmatic pipelines, embeddings, fine-tuning. |
| **CSV** | Flat summary columns. | Spreadsheets, audits, status reporting. |

**Markdown** per issue includes: title (`KEY — summary`), URL, status/type/
priority (and severity/client/components/labels when present), assignee/reporter,
created/updated, the description, and each comment (author, timestamp, body).

**JSONL** — each line is one issue with these keys, in order:
`key, url, summary, status, issuetype, priority, severity, client, assignee,
reporter, created, updated, components, labels, description, comments`. Each
`comments` entry has `author, created, updated, body`. Non-ASCII is preserved
(`ensure_ascii=False`).

**CSV** columns: `key, summary, status, issuetype, priority, severity, client,
assignee, updated, url`. Nested fields (description, comments, components,
labels) are intentionally omitted; use JSONL or Markdown for those.

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| **401 Unauthorized** | Wrong email/token, or wrong deployment type. Cloud needs email + API token (Basic); Server/DC needs a PAT (Bearer) and no email. |
| **403 Forbidden** | Token is valid but lacks permission for the project/issues. |
| **410 Gone** | The instance removed the legacy `/search` endpoint. Set deployment to **Cloud** (which uses `/rest/api/3/search/jql`). |
| **Empty results** | Check **Preview JQL**. Remember every query is scoped to `assignee = currentUser()`. Confirm status/type spellings match your instance. |
| **Severity/Client filter seems ignored** | Those clauses are only added when the corresponding custom-field id is set on the Connection tab. Use **Discover fields…**. |
| **Comments missing from export** | Enable **Load comments** before fetching. |
| **Slow fetch on large result sets** | Comments are fetched one request per issue (N+1). Narrow the query, or fetch without comments for a quick pass. |
| **"Remember token" stored as plaintext** | `keyring` is not installed. `pip install -e ".[keyring]"` to use the OS keychain. |
| **App won't start on a server / CI** | It requires a display. `QT_QPA_PLATFORM=offscreen` is for the test suite only, not interactive use. |

## 11. Development / testing

Install the dev tooling and run the checks:

```bash
python -m pip install -e ".[dev,keyring]"

pytest                     # unit tests (mocks/fixtures only; no live Jira)
ruff check .               # lint
mypy issue_deck           # type check (advisory; PyQt6 has no stubs)
```

The Qt tests run headless — set the platform first:

```bash
# Windows PowerShell
$env:QT_QPA_PLATFORM = "offscreen"; pytest
# macOS/Linux
QT_QPA_PLATFORM=offscreen pytest
```

CI (`.github/workflows/ci.yml`) runs ruff + pytest on Python 3.11 and 3.12
headless; mypy runs as an advisory, non-blocking step.

### Architecture

```
issue_deck/
├── __main__.py / app.py     # entry point + QApplication bootstrap
├── config.py, credentials.py, constants.py, cancellation.py, progress.py
├── models.py                # JiraIssue, JiraComment, JiraField, SearchFilters,
│                            #   FieldFilter, SavedView, ExportOptions (legacy
│                            #   export shape; stable field order)
├── schema.py                # normalized source-agnostic schema: NormalizedIssue,
│                            #   JiraUser, JiraDeployment, JiraFieldDefinition,
│                            #   FieldMapping, IssueCollection, CsvImportProfile,
│                            #   ExportManifest (+ raw-dict/CSV converters)
├── adf.py, normalization.py, comments.py, jql.py   # pure logic, no Qt
├── filtering.py, query.py, markers.py              # in-memory filter, estimate, badges
├── field_values.py          # field-value distribution / discovery engine
├── datasource.py, store.py, merge.py, views.py     # dataset abstraction, store,
│                            #   delta/merge, saved views
├── csv_import.py            # local-only CSV import pipeline (no HTTP)
├── jira_client.py           # the only module that talks to Jira over HTTP
├── exporters/               # markdown, jsonl, csv (+ run_export dispatch)
├── services/                # issue_service, field_service, capability_service,
│                            #   value_source_service (Qt-free orchestration)
├── ui/                      # PyQt6 only: main_window, connection_tab, query_tab,
│                            #   results_table, detail_panel, dialogs, merge_dialog,
│                            #   csv_wizard, value_discovery_dialog, workers
└── tests/                   # pytest suite (26 files)
issue_deck.py               # thin compatibility shim (python issue_deck.py)
```

Layering rule: **pure logic** (config, adf, jql, normalization, comments,
exporters, services) imports zero Qt and is unit-tested directly; the **UI**
depends on the logic, never the reverse; **all HTTP** lives in `jira_client.py`;
**all Qt threads/signals** live in `ui/`. `python issue_deck.py`,
`python -m issue_deck`, and the `issue-deck` console script are equivalent
entry points and must all keep working.

## 12. Known limitations

- **Comment fetch is N+1** — one request per issue. Large result sets with
  comments are slow; narrow the query or fetch without comments for a quick pass.
- **Exports run on the UI thread** — a very large JSONL/CSV/per-ticket export can
  briefly freeze the window.
- **Exports are verbatim issue data.** They never contain credentials, but there
  is **no body/PII redaction** for Jira-API exports (issue-key redaction exists
  for the CSV-import path only).
- **On Windows without `keyring`, the token file is plaintext-at-rest** — POSIX
  `chmod 600` does not apply. Install the `keyring` extra.
- **No live-Jira tests** — the suite is mocks/fixtures only.
- **Alpha** — deps are unpinned; behavior may change.

See [`docs/llm/GAPS.md`](docs/llm/GAPS.md) for the full list.

## Roadmap

Implemented since earlier drafts of this README: saved views, dynamic filters
from real data, the in-app issue detail panel, dataset merge + delta preview, and
the local-only CSV import (section 8).

Next candidates (see [`docs/llm/ROADMAP.md`](docs/llm/ROADMAP.md) for the ranked
list): a first-class API refresh-with-delta, wiring saved *profiles* into the UI,
threaded exports, consolidating the two issue models, and — further out — an
optional LLM export pack. Not planned unless demand appears: analytics dashboards
and local annotations.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the
full text.
