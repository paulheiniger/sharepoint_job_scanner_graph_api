# Copilot Estimator: Semantic Estimate and Template Adapter Plan

## Decision

The durable object is a **semantic estimate**, not a set of workbook rows.

Historical estimates should teach the system what work was performed, which
materials were used, how much material and labor were required, what conditions
affected the work, and why those choices fit the job. A workbook is one
replaceable projection of that knowledge.

This changes the boundary without discarding the existing estimator
intelligence:

```text
Current job facts
    -> historical material, labor, and decision evidence
    -> semantic estimate proposed and discussed by Copilot
    -> deterministic semantic calculations and validation
    -> compatibility mapping for the selected workbook
    -> reviewed workbook draft
```

Workbook coordinates can remain as source provenance or adapter configuration.
They must not be the public identity of an estimating decision.

## Canonical knowledge model

### 1. Job context

Represent facts that affect the estimate independently of a template:

- building and assembly type;
- included surfaces, openings, areas, and linear dimensions;
- substrate and existing conditions;
- required performance, warranty, or finish;
- access, equipment, crew, schedule, and travel conditions;
- customer notes and estimator assumptions; and
- referenced jobs, drawings, photos, and source documents.

Facts retain value, unit, source, confidence, and whether they were observed,
calculated, assumed, or confirmed.

### 2. Estimator decisions

Use stable semantic concepts rather than rows. Examples include:

- insulation foam system, cell type, thickness, thermal barrier, primer, and
  surface inclusion;
- roofing manufacturer, system, chemistry, warranty, wet mils, primer, fabric,
  board stock, details, and coating;
- labor activities, crew plan, equipment, loading, mobilization, travel, and
  lodging; and
- freight, overhead, profit, and other commercial decisions.

Each decision records the proposed value, alternatives, supporting evidence,
assumptions, confidence, and estimator disposition. Template mappings are
separate.

### 3. Historical observations

Historical work should be exposed at the grain Copilot needs to reason:

**Material usage**

- semantic material or assembly;
- product and product family;
- amount and unit;
- applicable area, linear measure, or count;
- normalized usage rate and observed range;
- yield or coverage assumption;
- unit price and effective date;
- waste or overage when known; and
- job, estimate, worksheet, and source-link provenance.

**Labor performance**

- semantic activity;
- total hours, crew size, days, and role mix;
- applicable quantity or area;
- productivity rate and range;
- access, equipment, weather, travel, and complexity conditions; and
- job and estimate provenance.

**Assemblies and co-occurrence**

- groups of materials and labor activities commonly selected together;
- the job contexts in which the group was used;
- substitutions and mutually exclusive choices; and
- confidence based on reviewed historical observations.

Source workbook rows can help extract and audit these observations. They are
not their durable IDs.

### 4. Calculation definitions

Store calculations in terms of semantic inputs, units, and outputs. Examples:

- board feet from area and thickness;
- foam sets from board feet, yield, and waste;
- coating gallons from area, coverage, coats, and waste;
- labor hours from quantity and productivity;
- travel from route mileage, trips, vehicles, and labor policy; and
- cost from quantity and effective unit price.

Each definition should state required inputs, units, rounding, exclusions,
effective version, and authoritative source. The current workbook formulas can
be used to extract and verify these rules, but a row number is not part of the
definition.

## Context API evolution

The Phase 1 context action remains the correct first endpoint, but its contract
should evolve toward these explicit evidence collections:

```json
{
  "job_context": {},
  "matched_comparables": [],
  "historical_material_usage": [],
  "historical_labor_performance": [],
  "historical_assemblies": [],
  "decision_evidence": [],
  "decision_concepts": [],
  "calculation_requirements": [],
  "approved_memories": [],
  "pricing_candidates": [],
  "product_guidance": [],
  "mileage_context": {},
  "source_links": []
}
```

The first implementation can derive these collections from existing
`EstimatorData`, template examples, historical decision evidence, pricing, and
product guidance. It should preserve the current retrieval ranking and bounding
behavior.

The API must not:

- call an LLM;
- tell Copilot which worksheet row to populate;
- treat the current template as the complete estimating ontology;
- silently discard historical items that are not present in the current
  template; or
- expose database or Microsoft credentials.

## Template capability and adapter layer

Workbook generation should be split from semantic validation.

### Template inspection

For each workbook version, inspect:

- sheets, tables, named ranges, labels, selector lists, and formulas;
- editable inputs and protected/calculated outputs;
- units, validation rules, and dependencies; and
- a deterministic template fingerprint.

Inspection produces a **capability manifest** describing what the workbook can
represent. It does not redefine the semantic estimate.

### Mapping

An adapter maps semantic concepts to the selected template using stable names,
tables, labels, and formulas where possible. Row or cell coordinates are
allowed only inside the versioned adapter.

Mapping results must classify every semantic estimate item as:

- mapped;
- intentionally excluded with a reason;
- unsupported by this template; or
- ambiguous and requiring review.

Changing, reordering, or replacing a template should trigger inspection and a
compatibility report. It must not erase or invalidate the semantic estimate.

### Post-write verification

After a reviewed mapping is executed, verify that:

- all approved mapped inputs were written;
- formulas and validation rules were preserved;
- calculated outputs were refreshed;
- unsupported or ambiguous concepts remain visible to the estimator; and
- the draft retains links to its semantic estimate and evidence.

## Validation boundary

Validation should be delivered in three distinct stages:

1. **Semantic validation** checks units, geometry, material quantities, labor,
   pricing currency/effective dates, conflicts, missing calculation inputs, and
   estimator confirmation.
2. **Template compatibility** checks whether a selected workbook can represent
   the validated semantic estimate without loss.
3. **Workbook verification** checks the created artifact after mapping and
   recalculation.

The future `POST /v1/estimating/validate` action should implement stage 1 only.
It should accept and return semantic estimate objects, not row-keyed decisions.

Workbook planning and execution should remain deferred until their contracts
have been reviewed. A mapping-preview operation may eventually return the
compatibility report before any durable artifact is created.

## Phased implementation

### Phase A: semantic evidence API

Current implementation status:

1. `POST /v1/estimating/context` and `GET /health` remain the only operations.
2. Typed schemas now cover material usage, labor performance, assemblies,
   decision concepts, calculation requirements, and source provenance.
3. An additive translation layer converts the existing row-derived historical
   packet into semantic observations without changing the current estimator.
4. Workbook coordinates are excluded from the new semantic observations.
5. Synthetic insulation and roofing fixtures cover quantities, productivity,
   assemblies, source links, and row-independent identifiers.

Still required before Phase A review:

1. Run the translation against reviewed database-backed historical examples.
2. Inspect coverage and unit quality for material families beyond foam and
   coating.
3. Add fixtures representing at least two actual template versions.
4. Review the payload with estimators and tune bounding limits.

### Phase B: semantic estimate and validation

1. Define the semantic estimate draft and atomic decision schemas.
2. Extract deterministic calculations from the existing estimator/workbook
   logic into semantic calculation services.
3. Implement semantic validation without requiring a workbook.
4. Add comparison fixtures showing that the same job produces the same
   semantic estimate for two structurally different templates.
5. Review Phase B before exposing a validation action.

### Phase C: template inspection and compatibility

1. Build a read-only template inspector and fingerprint.
2. Generate capability manifests for the current insulation and roofing
   workbooks.
3. Implement versioned mappings from semantic concepts to template
   capabilities.
4. Test reordered rows, renamed sheets, added items, and a replacement template.
5. Require a visible compatibility report for unsupported or ambiguous items.

### Phase D: controlled execution and deployment

1. Add reviewed workbook-plan and confirmed workbook-generation operations.
2. Use a narrowly permissioned Microsoft identity for SharePoint writes.
3. Add Entra scopes, roles, group restrictions, audit events, idempotency, and
   artifact retention policy.
4. Import the bounded OpenAPI contract into Copilot Studio when licensing and
   tenant administration are available.

## Acceptance criteria

- Historical material amounts and labor remain usable after a template change.
- The same estimating concept has the same ID across workbook versions.
- Recommendations include normalized quantities or productivity ranges and
  cite the jobs and files that support them.
- A template can add, remove, or reorder rows without changing the context API.
- Unsupported mappings are reported and never silently omitted.
- Semantic calculations are deterministic, unit-aware, and independently
  testable.
- The context and validation paths make no OpenAI call.
- Existing Estimating Assistant behavior remains unchanged while the semantic
  layer is introduced alongside it.

## Immediate next slice

Before adding another endpoint:

1. sample the existing historical template rows and decision evidence for both
   insulation and roofing;
2. define the first typed material-usage and labor-performance observations;
3. implement the row-to-semantic translation behind the context service;
4. add representative evaluation fixtures from real reviewed estimates; and
5. review the resulting context payload with estimators.

This is the smallest slice that tests the new premise directly: whether Copilot
receives enough historical material and labor intelligence to reason about a
new job without depending on the current workbook layout.
