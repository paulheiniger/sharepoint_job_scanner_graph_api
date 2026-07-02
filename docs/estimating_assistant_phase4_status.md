# Estimating Assistant Phase 4 Status

## Current Useful Demo Slice

- Decision-first workbench is wired for roofing coating/restoration and insulation spray foam.
- Historical estimator decisions are mined into Neon analytics tables and loaded by the workbench.
- Session capture stores raw notes, parsed scope, proposed decisions, estimator edits, final decisions, calculated outputs, workbook writes, and artifacts.
- Product knowledge is advisory only. It surfaces guidance, warnings, source documents, and source text evidence.
- Review packages include summary JSON, debug JSON, workbook summary XLSX, original notes, README, and generated workbook when export succeeds.

## Remaining Gaps

- Legacy automatic estimator evals are currently passing, but the old auto-estimate path should still be treated as secondary to the decision-first workbench. Production confidence should come from workbook export, decision trace review, and estimator edits.
- Product coverage is thin. The current local queue has two Gaco PDFs; more manufacturer sheets are needed for GAF, Gaco, GE Enduris, primers, sealants, thermal barriers, granules, and foam systems.
- Workbook formula mirroring is intentionally minimal. The workbook remains the estimating engine; Python mirrors only enough to show calculated outputs in the workbench.
- UI polish is good enough for review, but not finished. Better row grouping, column widths, and estimator-friendly copy should come after more estimator feedback.
- AI scope interpretation now preserves deterministic scope if the OpenAI call fails, but live AI quality still needs messy-note review before it becomes the default operator workflow.
- Session learning is capture-only. No automatic rule learning or approved-rule dashboard is implemented yet.
- Power BI/reporting should wait until the decision/session schemas stabilize after real estimator use.
- Estimate type coverage is intentionally narrow. Repair, full insulation variants, and other roofing templates need separate decision graph coverage later.

## Near-Term Recommendations

1. Run several real estimator sessions through the workbench and export session packages.
2. Review the exported workbook and decision trace with an estimator.
3. Add the next 10-20 high-value product PDFs through the product document queue and AI ingest path.
4. Use session exports to identify which editable decisions estimators change most often.
5. Only after that, build suggested-rule review dashboards.

## Non-Goals For Now

- Product sheets should not override estimator decisions automatically.
- Similar jobs should not drive primary defaults.
- Do not broaden to every estimate type until roofing coating and insulation spray foam are reliable.
