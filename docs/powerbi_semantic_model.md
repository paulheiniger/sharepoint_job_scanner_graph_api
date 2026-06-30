# Power BI Semantic Model Setup

This repo includes a Tabular Editor script for configuring the existing Spray-Tec
Power BI semantic model after the mart tables have been imported.

The script does **not** rename tables. It expects these existing table names:

- Documents
- Estimate Template Rows
- Estimator Feedback
- Jobs
- Labor Defaults
- Labor History
- Material Defaults
- Material History
- Pricing Catalog
- Quality Warnings
- Repair Defaults
- Repair Labor
- Repair Materials
- Repairs
- Rule Candidates
- Timesheets
- Unknown Templates

## Script

Run this script in Tabular Editor:

```text
scripts/powerbi_semantic_model.cs
```

It is compatible with Tabular Editor 2 and Tabular Editor 3 Advanced Scripting.

If Tabular Editor throws lambda/dynamic errors, use this no-LINQ script version.

## How To Run

1. Open the Power BI Desktop file connected to the Spray-Tec analytics marts.
2. Open Tabular Editor from External Tools.
3. In Tabular Editor, open Advanced Scripting.
4. Paste or open `scripts/powerbi_semantic_model.cs`.
5. Run the script.
6. Save the model back to Power BI.
7. Refresh visuals and validate measure results.

## What It Configures

The script inspects the current model and updates it in place:

- Converts snake_case column names to business-friendly names.
- Adds column and table descriptions for Copilot/Q&A.
- Organizes columns into display folders.
- Hides technical/internal fields.
- Sets default summarization.
- Applies currency, percent, hours, date, count, and square-foot formatting.
- Adds synonyms where the model compatibility level supports them.
- Creates or updates reusable measures without duplicating them.
- Creates focused perspectives for Copilot/demo use: `Executive`, `Estimator`,
  `Repairs`, and `Operations`.

## Measure Folders

Measures are created in the existing `Jobs` table when present, otherwise the
first Spray-Tec table found in the model.

Display folders:

- `Measures`
- `KPIs`

Column display folders include:

- General
- Job
- Customer
- Financial
- Estimator
- Materials
- Labor
- Repair
- Pricing
- Documents
- Quality
- Parser
- History
- Metadata
- Dates
- Links
- Photos
- Warnings
- Hidden Technical

## Rerunnable Behavior

The script is designed to be rerunnable:

- Existing measures are updated in place.
- Measures are not duplicated.
- Tables are not renamed.
- Already-friendly column names are left alone.
- Missing tables or columns are skipped.

## Known Limitations

- Synonym support varies by model compatibility level and Tabular Editor
  version. If synonyms are unavailable, the script still applies names,
  descriptions, folders, formats, and measures.
- The script does not create relationships. Review relationships in Power BI
  after importing marts, especially `Job ID` and `Repair ID` joins.
- Some measures return blank or zero when their source table/column is not
  present in the model.
- Power BI may require a model save and refresh before Copilot/Q&A fully uses
  updated descriptions and synonyms.

## Expected Behavior

After running the script, report builders should see a cleaner model:

- Friendly column names such as `Estimated Square Feet`, `Invoice Amount`, and
  `Median Labor Hours Per 1000 Sq Ft`.
- Technical fields like document/template row IDs, raw parser fields, hashes,
  and processing timestamps hidden.
- Financial values formatted as currency.
- Percentages formatted as percentages.
- URLs categorized as web URLs where supported.
- Measures such as `Total Jobs`, `Total Revenue`, `Repair Count`,
  `Warning Count`, and KPI measures available under measure folders.
