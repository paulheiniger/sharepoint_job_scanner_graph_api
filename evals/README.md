# Spray-Tec Evaluation Harnesses

These scripts are repeatable evaluation harnesses for estimator and relationship
mining work. They are not replacements for unit tests; they are meant to make
small Codex patches safer by showing whether structured outputs improved or
regressed.

The evals follow the project rule that deterministic code is responsible for
math, pricing, labor, relationship mining, and totals. Future AI-assisted note
interpretation can be evaluated here, but these harnesses test structured
outputs rather than free-form prose.

## Owner Question Eval

The owner-question suite defines 15 synthetic Spray-Tec business questions and
the facts, sources, confidence classes, qualifications, and forbidden
overclaims expected from an owner-facing agent. It distinguishes current API
operations from remaining partial owner-level questions.

Validate the suite and compare its required operations with the generated
OpenAPI document:

```bash
python -m evals.owner.run_owner_question_eval
```

Write a machine-readable report:

```bash
python -m evals.owner.run_owner_question_eval \
  --json-output output/evals/owner_question_eval.json
```

The runner can also score structured answer records with `--answers`. Synthetic
proxy and inferred facts must retain their truth labels and qualifications;
they cannot be presented as accounting actuals or authoritative job links.

The current API implements `getProductionBudgetHealth` and
`getOfficeJobProgress`. Production usage remains a cost proxy, while
office-to-job text matches remain inferred unless a stable timesheet `job_id`
is present.

## Field Notes Estimator Eval

Run all field-notes cases:

```bash
python evals/estimator/run_estimator_eval.py --allow-db-missing
```

Run one case:

```bash
python evals/estimator/run_estimator_eval.py --case-id roof_coating_basic_9536 --allow-db-missing
```

Write a JSON report:

```bash
python evals/estimator/run_estimator_eval.py \
  --json-output output/evals/estimator_eval.json \
  --allow-db-missing
```

`NEON_DATABASE_URL` is optional. When it is present, the runner tries to load
database-backed estimator data with the existing `load_estimator_data` path.
When it is missing, the eval still runs in limited mode.

## Persistent Estimator Chat Eval

The staged evaluator runs the same persistent session path used by the
Estimating Assistant. Historical generated cases remain review-only until an
estimator promotes them.

The default staged benchmark is
`evals/estimator/curated_staged_cases.json`. It contains only hand-authored,
approved cases. Generated historical cases are deliberately not the default.

Validate case selection without model calls:

```bash
python -m evals.estimator.run_staged_estimator_eval \
  --dry-run \
  --limit 4
```

Compare two configured models and write a review report:

```bash
python -m evals.estimator.run_staged_estimator_eval \
  --model "$OPENAI_ESTIMATOR_MODEL" \
  --model "$OPENAI_REVIEW_MODEL" \
  --database-url "$NEON_DATABASE_URL" \
  --json-output output/evals/staged_estimator_comparison.json
```

The live comparison requires `OPENAI_API_KEY`. It scores template selections,
material choices, thickness, area, labor assumptions, pricing range when an
authoritative expected total exists, exclusions, warranty, unnecessary
questions, and unsupported assumptions.

The persistent estimator and independent review use the Responses API. Set
`OPENAI_ESTIMATOR_REASONING_EFFORT` and
`OPENAI_REVIEW_REASONING_EFFORT` when the selected models support configurable
reasoning. The estimator defaults to `gpt-5.5` when no estimator model
environment variable or explicit override is provided. Request timing is
controlled by
`OPENAI_ESTIMATOR_CHAT_TIMEOUT_SECONDS`, `OPENAI_ESTIMATOR_MAX_RETRIES`,
`OPENAI_REVIEW_TIMEOUT_SECONDS`, and `OPENAI_REVIEW_MAX_RETRIES`.
Local cost circuit breakers default to 100,000 serialized input characters and
8,000 output tokens for the estimator, and 75,000 input characters and 6,000
output tokens for independent review. Models whose name contains `pro` have a
stricter 60,000-character estimator ceiling. Override them with
`OPENAI_ESTIMATOR_MAX_INPUT_CHARACTERS`,
`OPENAI_ESTIMATOR_PRO_MAX_INPUT_CHARACTERS`,
`OPENAI_ESTIMATOR_MAX_OUTPUT_TOKENS`,
`OPENAI_REVIEW_MAX_INPUT_CHARACTERS`, and
`OPENAI_REVIEW_MAX_OUTPUT_TOKENS`. Oversized requests fail locally before API
dispatch.
Terminal authentication, quota, and model-configuration failures stop further
calls for that model by default. Use `--continue-after-model-error` only when
continuing is intentional.

Promote only cases explicitly marked `reviewed`, `approved`, or `promoted` into
a curated benchmark artifact:

```bash
python -m evals.estimator.run_staged_estimator_eval \
  --cases output/estimator_generated_cases/generated_live_cases_chat_reviewed.jsonl \
  --promote-reviewed-output output/evals/curated_staged_cases.json \
  --promote-only
```

Cases marked `needs_review` or lacking an explicit promotion status are not
included in the curated artifact.

## Relationship Mining Eval

Run against database outputs:

```bash
python evals/relationship_mining/run_relationship_eval.py \
  --output-dir output/relationships
```

The relationship eval uses `NEON_DATABASE_URL` by default, or `--db-url` when
provided. It checks the normalized relationship mining tables and warns about
missing diagnostic CSVs, generic package dominance, missing labor rates, and
sparse template context.

Write a JSON report:

```bash
python evals/relationship_mining/run_relationship_eval.py \
  --output-dir output/relationships \
  --json-output output/evals/relationship_eval.json
```

## Environment

- `NEON_DATABASE_URL` is optional for estimator full-data mode.
- `NEON_DATABASE_URL` or `--db-url` is required for relationship mining evals.
- Do not print or commit database URLs or secrets.

## How To Use Failures

Failures should guide small, targeted patches. Prefer improving one parser,
rule, or relationship query at a time, then rerun the relevant eval case.
