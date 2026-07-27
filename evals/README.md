# Spray-Tec Evaluation Harnesses

These scripts are repeatable evaluation harnesses for estimator and relationship
mining work. They are not replacements for unit tests; they are meant to make
small Codex patches safer by showing whether structured outputs improved or
regressed.

The evals follow the project rule that deterministic code is responsible for
math, pricing, labor, relationship mining, and totals. Future AI-assisted note
interpretation can be evaluated here, but these harnesses test structured
outputs rather than free-form prose.

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

Validate case selection without model calls:

```bash
python -m evals.estimator.run_staged_estimator_eval \
  --dry-run \
  --limit 10
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
