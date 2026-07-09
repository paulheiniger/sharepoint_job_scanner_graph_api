# Spray-Tec Estimating Assistant Architecture

This diagram shows the executive view of the estimating platform: inputs are converted into evidence-backed estimator decisions, Excel remains the calculation engine, and estimator corrections become reusable institutional knowledge.

```mermaid
flowchart LR
  notes["Field notes, email, chat"]
  photos["Photos and handwritten notes"]
  reference["Correct template summaries"]
  sharepoint["SharePoint estimates, proposals, docs"]
  productDocs["Product data sheets and pricing"]

  extraction["Document and template extraction"]
  neon["Neon / PostgreSQL knowledge store"]
  templateIntel["Template intelligence<br/>rows, selectors, formulas"]
  history["Historical decision mining"]
  products["Product and pricing knowledge"]
  memory["Estimator memory<br/>approved corrections"]

  chat["Estimating chat assistant"]
  vision["Photo / note evidence extraction"]
  takeoff["Geometry and validation"]
  proposals["Evidence-gated decision proposals"]
  workbench["Estimator decision workbench"]
  formulas["Excel workbook formula engine"]
  mileage["Mapbox route mileage<br/>optional"]

  outputs["Filled estimate workbook"]
  review["Review package and session capture"]
  dashboards["Dashboards and Admin / Health"]
  approval["Estimator approval and feedback"]

  notes --> chat
  photos --> vision
  reference --> chat
  sharepoint --> extraction
  productDocs --> extraction

  extraction --> neon
  neon --> templateIntel
  neon --> history
  neon --> products
  neon --> memory

  templateIntel --> proposals
  history --> proposals
  products --> proposals
  memory --> chat
  memory --> proposals
  chat --> takeoff
  vision --> chat
  takeoff --> proposals
  mileage --> proposals
  proposals --> workbench
  workbench --> formulas
  formulas --> outputs
  workbench --> review
  review --> dashboards
  review --> approval
  approval --> memory
  approval --> history
```

## Executive Summary

- The assistant is not replacing the estimator; it helps turn notes, photos, history, pricing, and product guidance into reviewable workbook decisions.
- Excel estimating templates remain the trusted calculation engine for quantities, labor, costs, markups, and workbook outputs.
- Historical estimates and approved estimator corrections become reusable institutional knowledge.
- Product data and pricing are attached as evidence and guidance, not hidden overrides.
- Every estimate can produce a review package, final workbook, and learning data for future recommendations.

## Control Points

- Estimator review remains required before quoting.
- Memory candidates are pending until approved.
- AI decisions are evidence-gated and can be review-marked.
- Route mileage uses Mapbox only when configured and address evidence is available.
- Dashboards and Admin / Health expose review, memory approval, data quality, and operational status.
