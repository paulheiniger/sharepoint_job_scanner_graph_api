# SharePoint Job Scanner — Project Instructions

## Project objective

This repository supports a production-oriented SharePoint job indexing,
document extraction, and operational reporting pipeline.

Optimize for:

- low Microsoft Graph usage
- incremental processing
- resumability
- auditability
- safe reruns
- reliable database-backed state
- actionable operational reporting

## Architecture principles

Treat Microsoft Graph as the source system, not as the operational state store.

Persist resolved identifiers, scan state, extraction state, timestamps, errors,
and delta tokens in PostgreSQL. Do not repeatedly query Graph for information
that has already been resolved and stored.

Prefer extending the existing architecture over creating parallel scanners,
databases, caches, or command paths.

Before changing behavior, inspect:

- existing CLI commands and flags
- database models and migrations
- Graph client and throttling behavior
- scan-state and identifier-resolution logic
- document extraction workflow
- tests and smoke-test commands
- configuration and environment-variable handling

## Microsoft Graph requirements

For ongoing scans:

- Prefer Microsoft Graph delta queries wherever supported.
- Store delta tokens by site, drive, and applicable scan scope.
- Process only new, changed, moved, or deleted items.
- Reuse stored site IDs, drive IDs, item IDs, list item IDs, and parent IDs.
- Do not perform name-based identifier resolution during every normal scan.
- Do not issue one Graph request per item when batching, caching, expansion,
  delta discovery, or database joins can avoid it.
- Apply bounded concurrency, retries, exponential backoff, jitter, and timeout
  handling.
- Respect Retry-After responses.
- Track Graph request counts, retries, throttles, cache hits, unchanged items,
  resolved identifiers, and unresolved identifiers.

Treat these as distinct identifiers:

- tenant ID
- site ID
- drive ID
- drive item ID
- SharePoint list ID
- list item ID
- folder path
- web URL

Do not assume that every document currently has every identifier.

If a delta token expires or becomes invalid, use a controlled reconciliation
path. Do not silently trigger an unrestricted full scan.

## Workflow separation

Keep these workflows logically and operationally separate:

1. initial full discovery
2. incremental delta scan
3. metadata extraction
4. document-content extraction
5. identifier repair
6. explicit reconciliation
7. historical backfill

Do not make an ordinary scan automatically perform an unrestricted identifier
repair, document backfill, or full Graph traversal unless explicitly requested.

Identifier repair should be bounded, resumable, rate-limited, and capable of
processing only unresolved records.

## Database behavior

Database operations and backfills must be idempotent and safely rerunnable.

Use:

- stable source identifiers
- unique constraints where duplication is possible
- upserts where appropriate
- explicit transactions for related writes
- checkpoints for long-running work
- processing-status fields
- timestamps for discovery, source modification, retrieval, processing, and
  successful synchronization

Do not use names or folder paths as permanent identities when stable Graph IDs
are available.

Do not overwrite authoritative source values with lower-confidence inferred
values.

Schema migrations should be backward-compatible when practical.

## Extraction behavior

Prefer deterministic extraction from structured files and known templates
before using probabilistic or LLM-based extraction.

Preserve:

- original source content or source reference
- extracted value
- extraction method
- confidence
- parser or model version
- validation result
- source document and page or worksheet when available

Financial values, invoice data, contract values, job IDs, and authoritative
identifiers must not be silently changed based on LLM output.

Conflicts and low-confidence results should be flagged for review.

## CLI and operational requirements

Expensive commands should support appropriate combinations of:

- --dry-run
- --limit
- --resume
- --status
- --division
- --pipeline-status
- --folder
- --since
- --only-unresolved
- --only-changed
- --max-concurrency

Do not add flags merely for appearance; add them where they provide genuine
control or recovery value.

Commands should provide concise summaries including:

- records considered
- records processed
- records skipped
- records unchanged
- successes
- transient failures
- permanent failures
- unresolved records
- Graph calls
- retries
- throttling events
- elapsed time

Failures must include enough identifying context to diagnose and rerun the
affected records without flooding logs.

## Testing and completion

After changing code:

1. Run the most relevant unit tests, integration tests, linting, type checks,
   migration checks, and smoke tests available.
2. Report the actual commands and results.
3. Do not claim success merely because the code imports or compiles.
4. Verify the intended behavior where practical.
5. State unresolved risks and assumptions.
6. Provide the next commands as one copy-pasteable multiline shell block when
   they belong together.

Prefer commands that can be run from the repository root and return enough
output to diagnose the result in a single response.

## Security

Never place secrets, access tokens, passwords, database credentials, connection
strings, webhook URLs, or client secrets in:

- source code
- committed configuration
- test fixtures
- logs
- screenshots
- documentation examples
- Codex responses

Use environment variables and sanitized placeholders.

The `.env` file must remain excluded from Git. Do not print its values.
