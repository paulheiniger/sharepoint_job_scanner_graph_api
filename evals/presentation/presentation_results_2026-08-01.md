# Spray-Tec presentation-readiness results — 2026-08-01

## Executive result

The owner-level interface is ready for a controlled presentation. Its strongest
demonstrations are the five-action owner briefing and the sales pipeline chart.
The estimating intelligence is also compelling, but workbook delivery should
be described as a preview until the signed-link and workbook-quality issues
below are corrected.

Nine compact cases were run: five estimating cases and four owner-interface
cases. Four passed, five were partial, and none failed to retrieve core
business evidence or perform its primary calculation.

## Case results

| Case | Result | User-level assessment |
|---|---|---|
| Grossman complex roofing context | Pass with UX concern | Correctly reconciled 5,136 sq ft, separated the 320 sq ft tear-off, found comparables, calculated foam sets, used 62-mile round-trip context, and clearly separated evidence from assumptions. The response was much too long for normal estimator review. |
| Elsby repair workbook | Partial | Correctly paused for approval, refused to invent unavailable mileage, generated after a 70-mile test override, recalculated costs, and returned a $2,879.71 worksheet price. Generation needed two schema-correction retries and the displayed download text was not clickable. |
| Handwritten insulation workbook | Partial | Azure returned `201`, a $14,128.44 total job cost, and a $22,282.20 worksheet price. The signed download used `http`, not `https`. |
| Gardiner flooring workbook | Partial | Azure returned `201`, a $2,606.79 total job cost, and a $5,970.83 worksheet price using the stored flooring template. The signed download used `http`. |
| Multiple warranty options | Partial | The API created two separately named workbooks. A synthetic $250 warranty input was interpreted as $250 per sq ft and produced an approximately $3.18M worksheet price, exposing an ambiguous semantic contract. Both download URLs used `http`. |
| Five-action owner briefing | Pass | Produced a compact ranked list spanning all requested business areas. It identified Clark County RDC progress risk, DePauw crew assignment, DRG coating exposure, unassigned sales work, and office attribution problems while labeling authoritative, inferred, and proxy evidence correctly. |
| Sales pipeline chart | Pass | Produced a useful horizontal chart, complete stage and owner rollups, $38,646,627.97 total value, 467 opportunities, and $21.67M of unassigned exposure. It explicitly avoided calling pipeline forecasted revenue. |
| Profitability proxy | Partial | Qualifications were excellent and the weakest over-plan list was actionable. The endpoint returned only 25 of 27 comparable jobs, so the assistant correctly could not guarantee the strongest portfolio-wide ranking. Duplicate and unnamed tracking rows also weakened the result. |
| Office activity and progress | Pass with UX concern | Correctly separated 27.58 captured hours from 91 activity-only touches and clearly labeled every job link inferred. The answer became too long and presented 64 stalled labels without enough owner-level prioritization. |

## Workbook inspection

A generated roofing workbook was imported and all eight worksheets were
rendered. The estimate, job specification, people, materials, extras, tracking,
and warranty structures remained intact and the estimate sheet retained its
formula-driven cost engine.

Material concerns found during visual and formula inspection:

1. The `Tracking` sheet visibly contains `#DIV/0!` values when no actual
   production data exists. Fifteen formula-error cells were detected.
2. `TEST Job Spec` remains as a visible extra worksheet and contains only a
   fragment of the actual scope.
3. Warranty presentation can conflict across the estimate header, warranty
   inputs, and the separate warranty sheet; the rendered example showed an
   `N/A` warranty term while another sheet carried a workmanship term.
4. Empty operational sheets render with very large unused areas. This is not a
   calculation failure, but it makes the draft feel less finished.

The API's reported workbook totals were independently supported by retained
formulas, and replacing the returned link scheme with `https` downloaded a
valid 50,638-byte XLSX. That confirms the artifact is sound and the immediate
download problem is URL construction behind Azure's proxy.

## Prioritized course

### P0 — before presenting workbook creation

1. Honor Azure forwarded-protocol headers or configure an explicit public API
   origin so signed artifact URLs are always `https`.
2. Add a live test asserting that a production workbook response contains an
   HTTPS URL and that the URL downloads successfully.

### P1 — presentation quality

3. Add an estimator response budget: one-screen decision summary first, then
   optional evidence/material/labor detail. Grossman is accurate but
   substantially too long.
4. Make workbook-generation request errors directly actionable to the GPT by
   aligning OpenAPI descriptions and examples with validator rules. The Elsby
   flow recovered, but took 56 seconds and multiple retries.
5. Suppress expected blank-actual `#DIV/0!` errors in `Tracking`, hide or remove
   `TEST Job Spec` from exported drafts, and reconcile warranty fields across
   all worksheets.
6. Clarify warranty pricing as `cost_per_sqft` versus `flat_amount`; reject a
   suspiciously large warranty extension before generating option files.

### P2 — owner-interface reliability

7. Return complete strongest/weakest job rankings from the production-budget
   service, not only a 25-record bounded list.
8. Reconcile duplicate/unnamed tracking identities and expose a dedicated data
   quality queue.
9. Add priority scoring to stalled office projects so the owner sees the top
   five actionable items before the full inferred list.
10. Add explicit `planned` cases to the 15-question owner evaluation suite so
    future capability gaps remain visible rather than every question appearing
    supported or partial.

## Automated verification

- Owner-question contract validation: 15 cases passed structural validation;
  13 supported and 2 partial.
- Focused API, workbook, owner, chart, sales, operations, office, and
  production-budget regression tests: 85 passed with one dependency
  deprecation warning.
- Live Azure workbook calls returned `201` for roofing repair, insulation,
  flooring, and corrected multi-option payloads.

## Remediation verified later on 2026-08-01

Version `0.13.2` closed the presentation blockers and was deployed to Azure
Container Apps revision `spraytec-business-api--0000002` with 100% traffic.

- Azure now uses an explicit HTTPS public origin for artifact links. A live
  authenticated workbook call returned `201`; its HTTPS link downloaded with
  `200`, the XLSX MIME type, a valid `PK` signature, and 50,691 bytes.
- Warranty pricing is explicitly per square foot, capped at $25/sq. ft., and
  requires its area to match the estimate area. Flat/localized warranty costs
  must use an adder. Conflicting warranty years are rejected.
- Exported roofing drafts hide `TEST Job Spec`, show a consistent warranty term,
  and wrap empty-actual Tracking formulas with `IFERROR`. Independent artifact
  inspection found zero formula-error matches and visually verified Estimate,
  People, Job Spec, Tracking, Warranty, and Materials.
- The GPT now leads with a one-screen estimating summary, strongest evidence,
  and confirmation questions; detailed evidence is optional.
- Production-budget responses include complete-portfolio strongest/weakest
  rankings before the bounded detail limit. A live query ranked all 27 matching
  jobs while returning only five detail records.
- Office progress now returns a scored top-five owner priority queue before the
  full stalled list. The live data still contains 64 stalled labels, but the
  owner-facing response is bounded to five priorities.
- The published Custom GPT schema and instructions were updated. A live GPT
  question returned three strongest and three weakest cost-position proxies,
  explicitly stated that all 27 jobs were ranked before detail truncation, and
  retained the accounting-profitability caveat.

Regression result after remediation: 95 passed with one existing Starlette
dependency deprecation warning.

Remaining lower-priority work is upstream tracking-identity cleanup and explicit
`planned` owner-evaluation cases; neither blocks the current presentation.

## Grossman comparison correction — 2026-08-02

The original result above described the 320 sq. ft. callout as localized
tear-off. Comparison with the completed estimator workbook shows the stronger
interpretation: approximately 3,120 sq. ft. receives full removal and 2-inch
ISO, while 320 sq. ft. is the nested deteriorated-deck repair area. The current
structured-area compiler models 3,120 and 2,016 sq. ft. as exclusive roof
sections, keeps 320 sq. ft. nested, and uses 5,136 sq. ft. for full-roof
foam/coating. The earlier wording remains above as a record of the test result,
not the current acceptance criterion.
