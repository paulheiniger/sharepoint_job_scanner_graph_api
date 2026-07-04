# Spray-Tec AI Platform — Project Instructions

## Project Mission

This repository supports Spray-Tec's operational AI platform across SharePoint ingestion, document intelligence, estimating, reporting, workflow automation, and future Copilot-style assistants. The mission is to capture institutional knowledge and make it available through AI-assisted workflows.

## Repository Scope

- SharePoint and Microsoft Graph ingestion
- Operational PostgreSQL / Neon data platform
- Document intelligence and extraction
- AI-assisted estimating
- Historical estimate mining
- Template intelligence
- Decision graph and knowledge graph
- Product knowledge
- Operational dashboards and reporting
- Power BI semantic models
- Workflow automation
- Future Copilot-style conversational experiences

## Core Design Principles

- Preserve institutional knowledge.
- Treat Excel estimating workbooks as trusted calculation engines.
- Prefer deterministic business rules over AI guesses when rules exist.
- Use AI for ambiguity, interpretation, recommendations, and missing information.
- Make recommendations explainable with historical evidence and product guidance.
- Capture user corrections as future training data.
- Build reusable knowledge layers instead of one-off features.
- Prefer extending existing architecture over parallel implementations.

## Platform Architecture Principles

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

## Knowledge Layers

This repository architecture is organized into several knowledge layers that support Spray-Tec's operational AI platform.

These knowledge layers should remain reusable across the entire platform. The Estimating Assistant is one consumer of these layers, but future scheduling, CRM, reporting, operations, and Copilot experiences should reuse the same underlying knowledge rather than duplicate logic.

### Operational Data

SharePoint serves as the source content repository. PostgreSQL/Neon stores resolved identifiers, scan state, extraction state, timestamps, errors, and delta tokens. Document extraction processes content to populate semantic models, which enable operational reporting and workflow automation.

### Template Intelligence

Excel templates are treated as business-rule engines. The system preserves selector maps, lookup tables, formulas, workbook dependencies, row mappings, and business assumptions to maintain deterministic business logic.

### Decision Graph

The Decision Graph models estimator-controlled decisions rather than spreadsheet rows. Decision nodes preserve editable inputs, workbook traceability, downstream calculations, historical evidence, and product guidance to support explainable and auditable decision-making.

### Historical Decision Mining

Historical estimates are mined to learn the decisions estimators made, rather than focusing on workbook outputs. This enables capturing institutional knowledge and improving recommendation quality.

### Product Knowledge

Product Data Sheets (PDS), Application Guides, Installation Guides, Technical Bulletins, and Safety Data Sheets (SDS) form a product knowledge layer attached to decision nodes, providing recommended uses, limitations, coverage, and application guidance.

### Workbook Formula Engine

Workbook formulas remain the trusted source for calculations such as gallons, units, labor, totals, and pricing, ensuring deterministic and authoritative results.

## Estimator Intelligence Architecture

The Estimating Assistant is an AI estimator—not an AI spreadsheet filler. Excel workbooks are the trusted calculation engines. The AI's responsibility is to infer the same decisions an experienced Spray-Tec estimator would make from field notes, emails, dictated notes, drawings, photos, and historical estimates. Every estimator edit should become future training data.

### Core Estimating Flow

Field Notes / Emails / Photos / Drawings  
→ AI Scope Interpretation  
→ Deterministic Geometry & Validation  
→ Decision Graph  
→ Historical Decision Evidence  
→ Product Knowledge  
→ Workbook Formula Engine  
→ Estimator Review  
→ Workbook Export  
→ Session Capture

### Decision-First Philosophy

The AI predicts estimator decisions—not workbook outputs.  
**Roofing examples:** manufacturer/system, chemistry, warranty, wet mils, primer, fabric, board stock, thermal barrier, equipment, labor plan.  
**Insulation examples:** surface scope, target R-value, open vs closed cell, foam system, thickness, thermal barrier, primer, labor plan.  
Gallons, sets, labor hours, costs, and totals are always workbook outputs, not direct AI predictions.

### Template Intelligence

Templates are business-rule engines. Preserve selector maps, lookup tables, workbook row mappings, formulas, dependencies, and business assumptions. Prefer extracting workbook logic over statistically learning existing business rules.

### Decision Graph

Decision nodes represent estimator-controlled inputs. Preserve template rows, selector codes, editable inputs, workbook dependencies, downstream calculations, historical evidence, product guidance, and workbook traceability.

### Historical Decision Mining

Historical estimates answer: "What decisions did estimators make?"  
Mine decisions such as manufacturer, product system, warranty, wet mils, foam type, target thickness, labor planning, and equipment—not primarily workbook outputs.

### Product Knowledge

Product Data Sheets (PDS), Application Guides, Installation Guides, Technical Bulletins, and SDS support decision nodes. Product knowledge provides recommended uses, approved substrates, limitations, coverage, R-values, application guidance, and warnings—but never automatically overrides estimator decisions.

### AI Responsibilities

AI is used for interpreting notes, extracting scope, proposing decisions, explaining recommendations, summarizing evidence, and identifying missing information. AI is not used for replacing workbook formulas, authoritative pricing, deterministic geometry, identifiers, or financial calculations.

### Session Learning

Store raw notes, parsed scope, proposed decisions, historical evidence, product guidance, estimator edits, workbook export, and final decisions as the long-term training corpus.

### Continuous Learning

Every completed estimating session should become a future training example by preserving raw notes, proposed decisions, estimator edits, final decisions, workbook exports, and supporting evidence.

### Evaluation Philosophy

Evaluation should primarily validate estimator decisions, deterministic calculations, workbook outputs, explainability, historical evidence, product guidance, and workbook export integrity—rather than only quantity-per-square-foot metrics.

### Guiding Principle

Optimize for:  
**"Given these notes, what would an experienced Spray-Tec estimator decide?"**  
—not for predicting spreadsheet outputs.  
AI should think like the estimator, not like the spreadsheet.


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

## Testing and Completion

After changing code:

1. Run the most relevant unit tests, integration tests, linting, type checks, migration checks, and smoke tests available.
2. Report the actual commands executed and their results.
3. Do not claim success simply because code imports or compiles.
4. Verify the intended behavior whenever practical.
5. State remaining assumptions, limitations, and risks.
6. Provide copy-pasteable multiline shell commands for any recommended follow-up steps.

Evaluation should prioritize behavior over implementation details.

For estimator-related work, verify:
- AI scope interpretation
- deterministic geometry and calculations
- decision graph outputs
- historical decision recommendations
- product guidance
- workbook calculations
- workbook export integrity
- session capture when applicable

For operational workflows, verify:
- scanner behavior
- extraction behavior
- database writes
- dashboard functionality
- reporting outputs
- resumability
- diagnostics

Prefer evaluation-driven development.

When adding significant capability:
- add or extend representative evaluation fixtures
- prevent regressions with automated tests
- keep smoke tests runnable from the repository root
- avoid introducing functionality that cannot be validated

Do not remove or weaken existing validation merely to satisfy new functionality.

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

## Long-Term Platform Vision

This repository is evolving into an operational AI platform for Spray-Tec, combining document intelligence, historical knowledge, product knowledge, decision support, reporting, and workflow automation. The Estimating Assistant is the first major AI application built on these shared knowledge layers. Future operational assistants should reuse the same Template Intelligence, Decision Graph, Historical Decisions, Product Knowledge, and Operational Data layers rather than building separate logic.

## Development Philosophy

- Optimize for preserving Spray-Tec institutional knowledge.
- Build reusable knowledge layers before building application-specific features.
- Prefer explainable recommendations over opaque predictions.
- Preserve workbook logic whenever it already captures company expertise.
- Capture user corrections as future training data.
- AI should recommend decisions, not replace experienced estimators.
- Favor thin, end-to-end vertical slices over broad unfinished architecture.
- Every major feature should improve the long-term learning capability of the platform.