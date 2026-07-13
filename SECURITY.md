# Security & Privacy

`issue_deck` is a local desktop tool. It talks to *your* Jira instance, keeps
data in memory, and only writes to disk when you ask it to. This document
describes the guarantees the code makes about credentials, redaction, and what
can (and cannot) end up in a file on disk.

These are **enforced by tests** (`issue_deck/tests/test_security_privacy.py`,
`test_redaction.py`, `test_credentials.py`, `test_logging_redaction.py`), not just
documented.

## Credentials & token storage

- **Tokens are never stored in `config.json`.** `AppConfig` holds connection
  settings only; token persistence is delegated to
  [`issue_deck/credentials.py`](issue_deck/credentials.py).
- **Keychain preferred.** When the [`keyring`](https://pypi.org/project/keyring/)
  package and an OS backend are available, the token is stored in the OS keychain
  (encrypted at rest, keyed by base URL).
- **Explicit plaintext fallback.** If no keychain is available *and* you choose
  "Remember", the app **warns you** before writing the token to a local
  `token.txt`. That file is created with `0600` permissions inside a `0700` app
  directory on platforms that support POSIX permissions.
- **Forget token.** The connection tab has a **Forget token** button that deletes
  the stored token from both the keychain and the plaintext file and clears the
  input. Un-checking "Remember" and saving also forgets it.
- **Not in logs or exports.** A token, PAT, password, or `Authorization` header is
  never logged or written to any export (see below).

## Redaction

All redaction is centralized in [`issue_deck/redaction.py`](issue_deck/redaction.py)
so it behaves identically across every output. `RedactionSettings` covers:

| Setting | Effect |
|---|---|
| **Issue keys** | `PROJ-123` → `PROJ-•••`, including keys embedded in browse URLs |
| **People names** | assignee / reporter / comment authors become stable pseudonyms (`Person 1`, …); account ids, usernames and emails are dropped |
| **Emails** | email addresses scrubbed from all free text (`[email redacted]`) |
| **Client / customer names** | replaced with stable pseudonyms (`Client 1`, …) |
| **URLs** | `http(s)` URLs scrubbed from all free text (`[url redacted]`) |
| **Comments** | comment bodies omitted entirely |
| **Descriptions** | issue descriptions omitted entirely |

- **Consistent across formats.** Redaction runs in a single pipeline
  (`exporters/transform.prepare_issues`) that feeds Markdown, JSONL, CSV, and the
  ZIP export/prompt packs, so the same settings produce the same redactions in
  every artifact.
- **Deterministic.** Pseudonyms are assigned in a stable order, so two exports of
  the same data diff cleanly.
- **Preview before export.** The export dialog's **Preview redaction…** button
  shows a before/after sample (via `exporters.redaction_preview`) so you can
  confirm nothing sensitive leaks before writing a file.
- The applied redaction is recorded in each pack's `manifest.json`.

## Configuration & saved filters

- `config.json` contains connection settings only — never a token and never
  ad-hoc query values.
- Sensitive filter values (free-text search, client names, etc.) are only
  persisted inside a **saved view** you explicitly name and save
  (`views.json`) — the current query is not auto-persisted.

## CSV import (local-only)

- The CSV wizard is entirely offline — it never contacts Jira.
- **Raw rows are transient.** Parsed rows live in memory only for inference and
  preview; they are never serialized.
- **Schema-only profiles.** A saved import profile stores column names + field
  mappings — never cell values. Only the file *basename* is retained (never the
  absolute uploaded path), and only if you opt in to saving.
- Committing an import only mutates the in-memory dataset unless you explicitly
  opt in to saving the profile and/or normalized dataset.

## Exports

- **No credentials.** No token, password, PAT, or `Authorization` header is ever
  written to an export.
- **No full base URL.** Only the *host* of your instance URL is recorded in a
  pack manifest (`host_of()` deliberately drops any `user:pass@` and path).
- **No raw API responses.** Exports contain the normalized issue shape only; the
  full raw Jira payload is never retained (`NormalizedIssue` keeps only the
  custom fields you mapped) and there is no un-gated raw-response export.

## Logging

- Logging is centralized in
  [`issue_deck/logging_utils.py`](issue_deck/logging_utils.py). Every logger
  carries a `RedactingFilter` that scrubs tokens, `Authorization` headers, and
  credentials-in-URL from **every** record before it reaches a handler.
- The HTTP client logs only the request method and path — never auth, query
  params, or bodies. Verbose logging is opt-in via the `JIRA_PULLER_DEBUG`
  environment variable and remains redacted.

## Reporting a vulnerability

This is a personal/local tool without a formal disclosure process. If you find a
security issue, please open an issue on the project's GitHub repository describing
the problem (without including any real tokens or personal data).
