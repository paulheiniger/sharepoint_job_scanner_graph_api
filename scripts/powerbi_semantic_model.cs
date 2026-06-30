// Spray-Tec Power BI semantic model configuration
// Compatible with Tabular Editor 2 and Tabular Editor 3 Advanced Scripting.
//
// Important: this script assumes the tables have already been renamed in Power BI.
// It does not rename tables. It configures columns, folders, synonyms, formats,
// descriptions, and reusable measures on the existing semantic model.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Text.RegularExpressions;
using Microsoft.AnalysisServices.Tabular;

var sprayTecTables = new[]
{
    "Documents",
    "Estimate Template Rows",
    "Estimator Feedback",
    "Jobs",
    "Labor Defaults",
    "Labor History",
    "Material Defaults",
    "Material History",
    "Pricing Catalog",
    "Quality Warnings",
    "Repair Defaults",
    "Repair Labor",
    "Repair Materials",
    "Repairs",
    "Rule Candidates",
    "Timesheets",
    "Unknown Templates"
};

var tableDescriptions = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
{
    {"Documents", "Document inventory and extraction status for SharePoint files."},
    {"Estimate Template Rows", "Parsed estimate workbook rows used for estimator calibration and template QA."},
    {"Estimator Feedback", "Captured estimator edits comparing historical defaults to final values."},
    {"Jobs", "Curated operational job and sales pipeline data."},
    {"Labor Defaults", "Historical labor productivity defaults generated from completed work."},
    {"Labor History", "Historical job labor package usage and cost evidence."},
    {"Material Defaults", "Historical material quantity and cost defaults generated from completed work."},
    {"Material History", "Historical job material package usage and cost evidence."},
    {"Pricing Catalog", "Current and historical material pricing catalog."},
    {"Quality Warnings", "Operational and data-quality warnings."},
    {"Repair Defaults", "Historical repair default ranges by repair type and roof type."},
    {"Repair Labor", "Repair labor usage history."},
    {"Repair Materials", "Repair material usage history."},
    {"Repairs", "VSimple repair job, scope, and outcome history."},
    {"Rule Candidates", "Candidate estimating rules from relationship mining and review workflows."},
    {"Timesheets", "Office/admin/sales timesheet entries."},
    {"Unknown Templates", "Grouped unknown template rows for parser review."}
};

string[] acronymTokens =
{
    "id", "url", "hvac", "pdf", "qa", "ai", "spf", "ocr", "csv", "json", "db", "lf"
};

var exactColumnNames = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
{
    {"id", "ID"},
    {"job_id", "Job ID"},
    {"document_id", "Document ID"},
    {"template_row_id", "Template Row ID"},
    {"repair_id", "Repair ID"},
    {"pricing_item_id", "Pricing Item ID"},
    {"entry_id", "Entry ID"},
    {"warning_id", "Warning ID"},
    {"rule_id", "Rule ID"},
    {"estimated_sqft", "Estimated Square Feet"},
    {"area_sqft", "Area Sq Ft"},
    {"qty_per_sqft", "Historical Quantity Per Sq Ft"},
    {"cost_per_sqft", "Cost Per Sq Ft"},
    {"price_per_sqft", "Price Per Sq Ft"},
    {"median_qty_per_sqft", "Median Quantity Per Sq Ft"},
    {"p25_qty_per_sqft", "P25 Quantity Per Sq Ft"},
    {"p75_qty_per_sqft", "P75 Quantity Per Sq Ft"},
    {"median_cost_per_sqft", "Median Cost Per Sq Ft"},
    {"median_hours_per_1000_sqft", "Median Labor Hours Per 1000 Sq Ft"},
    {"p25_hours_per_1000_sqft", "P25 Labor Hours Per 1000 Sq Ft"},
    {"p75_hours_per_1000_sqft", "P75 Labor Hours Per 1000 Sq Ft"},
    {"hours_per_1000_sqft", "Hours Per 1000 Sq Ft"},
    {"invoice_amount", "Invoice Amount"},
    {"total_invoice_amount", "Total Invoice Amount"},
    {"total_bill_amount", "Total Bill Amount"},
    {"folder_url", "Folder URL"},
    {"sharepoint_url", "SharePoint URL"},
    {"proposal_url", "Proposal URL"},
    {"estimate_url", "Estimate URL"},
    {"contract_url", "Contract URL"},
    {"invoice_url", "Invoice URL"},
    {"job_tracking_url", "Job Tracking URL"},
    {"primary_doc_link", "Primary Document Link"},
    {"roof_condition", "Roof Condition"},
    {"template_bucket", "Template Bucket"},
    {"template_type", "Template Type"},
    {"parser_version", "Parser Version"},
    {"source_year", "Source Year"},
    {"line_item_kind", "Line Item Kind"},
    {"source_file", "Source File"},
    {"file_name", "File Name"},
    {"file_extension", "File Extension"},
    {"mime_type", "MIME Type"},
    {"requires_ocr", "Requires OCR"},
    {"is_current", "Is Current"},
    {"needs_review", "Needs Review"},
    {"review_required", "Review Required"},
    {"evidence_count", "Evidence Count"},
    {"job_count", "Job Count"},
    {"photo_count", "Photo Count"},
    {"wet_mils", "Wet Mils"},
    {"warranty_years", "Warranty Years"},
    {"unit_price", "Unit Price"},
    {"unit_cost", "Unit Cost"},
    {"final_price", "Final Price"},
    {"total_cost", "Total Cost"},
    {"total_hours", "Total Hours"},
    {"labor_hours", "Labor Hours"},
    {"labor_cost", "Labor Cost"},
    {"gross_profit", "Gross Profit"},
    {"gross_profit_percentage", "Gross Profit Percentage"},
    {"pipeline_status", "Pipeline Status"},
    {"extraction_status", "Extraction Status"},
    {"extraction_method", "Extraction Method"},
    {"extraction_error", "Extraction Error"},
    {"classification_reason", "Classification Reason"},
    {"parsed_confidence", "Parsed Confidence"},
    {"labor_package", "Labor Package"},
    {"material_package", "Material Package"},
    {"product_name", "Product Name"},
    {"product_name_normalized", "Normalized Product Name"},
    {"unit_of_measure", "Unit of Measure"},
    {"price_basis", "Price Basis"},
    {"source_hint", "Source Hint"}
};

var columnDescriptions = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
{
    {"Estimated Square Feet", "Estimated roof area after deductions."},
    {"Area Sq Ft", "Area used for historical quantity and labor normalization."},
    {"Invoice Amount", "Invoice value recorded for this project."},
    {"Final Price", "Final quoted or sold project price."},
    {"Historical Quantity Per Sq Ft", "Historical quantity per square foot from completed projects."},
    {"Median Quantity Per Sq Ft", "Median historical material quantity per square foot from completed projects."},
    {"P25 Quantity Per Sq Ft", "25th percentile historical material quantity per square foot."},
    {"P75 Quantity Per Sq Ft", "75th percentile historical material quantity per square foot."},
    {"Median Cost Per Sq Ft", "Median historical cost per square foot from completed projects."},
    {"Median Labor Hours Per 1000 Sq Ft", "Historical median labor requirement derived from completed jobs."},
    {"P25 Labor Hours Per 1000 Sq Ft", "25th percentile historical labor hours per 1000 square feet."},
    {"P75 Labor Hours Per 1000 Sq Ft", "75th percentile historical labor hours per 1000 square feet."},
    {"Hours Per 1000 Sq Ft", "Labor hours normalized per 1000 square feet."},
    {"Evidence Count", "Number of historical rows or jobs supporting this default."},
    {"Job Count", "Number of jobs supporting this relationship or default."},
    {"Template Bucket", "Canonical estimate template bucket assigned by the parser."},
    {"Parser Version", "Parser version that produced the extracted row or field."},
    {"Roof Condition", "Roof condition category parsed from estimate or field-note data."},
    {"Folder URL", "SharePoint folder link for the project."},
    {"Document ID", "Internal document identifier from the scanner."},
    {"Job ID", "Business job identifier used to connect operational, document, estimate, and repair data."},
    {"Repair ID", "Repair job identifier from VSimple repair history."},
    {"Warning Count", "Count of quality or operational warnings."},
    {"Quality Warnings", "Warnings that may require data cleanup or operational review."},
    {"Price Per Sq Ft", "Project price divided by estimated square feet."},
    {"Gross Profit Percentage", "Gross profit as a percentage of invoice or bill value."},
    {"Is Current", "Indicates whether a pricing row is the current active price."},
    {"Needs Review", "Indicates a row or field should be reviewed by an estimator or operator."}
};

var synonymMap = new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase)
{
    {"Estimated Square Feet", new[] {"roof area", "roof size", "sqft", "square footage", "roof square footage"}},
    {"Area Sq Ft", new[] {"area", "sqft", "square feet", "roof area"}},
    {"Invoice Amount", new[] {"invoice", "billing", "bill", "amount billed"}},
    {"Final Price", new[] {"price", "revenue", "sales price", "quote amount"}},
    {"Historical Quantity Per Sq Ft", new[] {"historical average", "default quantity", "historical quantity", "material default"}},
    {"Median Quantity Per Sq Ft", new[] {"historical average", "default quantity", "historical material usage"}},
    {"Median Labor Hours Per 1000 Sq Ft", new[] {"historical labor", "default labor", "labor productivity", "labor default"}},
    {"Repair ID", new[] {"repair", "service", "fix", "patch", "service call"}},
    {"Documents", new[] {"files", "attachments", "estimate files", "documents"}},
    {"Folder URL", new[] {"folder", "SharePoint", "link", "project folder"}},
    {"Template Bucket", new[] {"bucket", "package", "estimate package"}},
    {"Material Package", new[] {"material", "package", "material package"}},
    {"Labor Package", new[] {"labor", "task", "labor task", "work package"}},
    {"Quality Warnings", new[] {"warnings", "QA", "issues", "data quality"}},
    {"Pricing Catalog", new[] {"pricing", "price list", "material price", "catalog"}}
};

string NormalizeKey(string value)
{
    if (String.IsNullOrWhiteSpace(value)) return "";
    return Regex.Replace(value.ToLowerInvariant(), @"[\s_\-\/]+", "");
}

string SplitCamel(string value)
{
    return Regex.Replace(value, "([a-z])([A-Z])", "$1 $2");
}

bool StringArrayContains(string[] values, string target)
{
    if (values == null || target == null) return false;
    foreach (string value in values)
    {
        if (String.Equals(value, target, StringComparison.OrdinalIgnoreCase)) return true;
    }
    return false;
}

bool ListContainsIgnoreCase(List<string> values, string target)
{
    if (values == null || target == null) return false;
    foreach (string value in values)
    {
        if (String.Equals(value, target, StringComparison.OrdinalIgnoreCase)) return true;
    }
    return false;
}

void AddUnique(List<string> values, string value)
{
    if (values == null || String.IsNullOrWhiteSpace(value)) return;
    string clean = value.Trim();
    if (!ListContainsIgnoreCase(values, clean)) values.Add(clean);
}

List<string> BuildSynonymList(string first, string[] extras)
{
    var values = new List<string>();
    AddUnique(values, first);
    if (extras != null)
    {
        foreach (string extra in extras) AddUnique(values, extra);
    }
    return values;
}

string FriendlyName(string originalName)
{
    if (String.IsNullOrWhiteSpace(originalName)) return originalName;

    string trimmed = originalName.Trim();
    if (exactColumnNames.ContainsKey(trimmed)) return exactColumnNames[trimmed];

    string lower = trimmed.ToLowerInvariant();
    if (exactColumnNames.ContainsKey(lower)) return exactColumnNames[lower];

    string cleaned = SplitCamel(trimmed.Replace("_", " ").Replace("-", " ").Replace("/", " "));
    var friendlyTokens = new List<string>();
    foreach (string rawToken in Regex.Split(cleaned, @"\s+"))
    {
        if (String.IsNullOrWhiteSpace(rawToken)) continue;
        string token = rawToken.Trim();
        string tokenLower = token.ToLowerInvariant();
        if (tokenLower == "sqft" || tokenLower == "sq" || tokenLower == "sf")
        {
            friendlyTokens.Add("Sq Ft");
        }
        else if (tokenLower == "ft")
        {
            friendlyTokens.Add("Ft");
        }
        else if (tokenLower == "pct" || tokenLower == "percent" || tokenLower == "percentage")
        {
            friendlyTokens.Add("Percentage");
        }
        else if (StringArrayContains(acronymTokens, tokenLower))
        {
            friendlyTokens.Add(tokenLower.ToUpperInvariant());
        }
        else if (tokenLower == "url")
        {
            friendlyTokens.Add("URL");
        }
        else if (tokenLower == "id")
        {
            friendlyTokens.Add("ID");
        }
        else
        {
            friendlyTokens.Add(Char.ToUpperInvariant(tokenLower[0]) + tokenLower.Substring(1));
        }
    }

    string result = String.Join(" ", friendlyTokens.ToArray());
    result = result.Replace("Sq Ft Ft", "Sq Ft");
    result = result.Replace("Per Sq Ft", "Per Sq Ft");
    return result;
}

dynamic FindTable(string tableName)
{
    foreach (var table in Model.Tables)
    {
        if (table.Name.Equals(tableName, StringComparison.OrdinalIgnoreCase)) return table;
    }
    return null;
}

IEnumerable<dynamic> ColumnsOf(dynamic table)
{
    foreach (var column in (IEnumerable)table.Columns) yield return column;
}

IEnumerable<dynamic> MeasuresOf(dynamic table)
{
    foreach (var measure in (IEnumerable)table.Measures) yield return measure;
}

dynamic FindColumn(string tableName, params string[] candidates)
{
    var table = FindTable(tableName);
    if (table == null) return null;

    foreach (string candidate in candidates)
    {
        string friendly = FriendlyName(candidate);
        string candidateKey = NormalizeKey(candidate);
        string friendlyKey = NormalizeKey(friendly);
        foreach (var column in ColumnsOf(table))
        {
            string columnKey = NormalizeKey(column.Name);
            if (columnKey == candidateKey || columnKey == friendlyKey) return column;
        }
    }
    return null;
}

string DaxName(string name)
{
    return name.Replace("]", "]]").Replace("'", "''");
}

string TableDax(string tableName)
{
    return "'" + tableName.Replace("'", "''") + "'";
}

string ColumnDax(string tableName, params string[] candidates)
{
    var column = FindColumn(tableName, candidates);
    if (column == null) return null;
    return TableDax(tableName) + "[" + DaxName(column.Name) + "]";
}

string SumExpr(string tableName, params string[] candidates)
{
    string column = ColumnDax(tableName, candidates);
    return column == null ? "BLANK()" : "SUM(" + column + ")";
}

string AverageExpr(string tableName, params string[] candidates)
{
    string column = ColumnDax(tableName, candidates);
    return column == null ? "BLANK()" : "AVERAGE(" + column + ")";
}

string DistinctCountExpr(string tableName, params string[] candidates)
{
    if (FindTable(tableName) == null) return "0";
    string column = ColumnDax(tableName, candidates);
    return column == null ? "COUNTROWS(" + TableDax(tableName) + ")" : "DISTINCTCOUNT(" + column + ")";
}

string CountRowsExpr(string tableName)
{
    return FindTable(tableName) == null ? "0" : "COUNTROWS(" + TableDax(tableName) + ")";
}

string FilterCountExpr(string tableName, string condition)
{
    return FindTable(tableName) == null ? "0" : "COUNTROWS(FILTER(" + TableDax(tableName) + ", " + condition + "))";
}

string TextNotBlankCondition(string tableName, params string[] candidates)
{
    string column = ColumnDax(tableName, candidates);
    return column == null ? "FALSE()" : "NOT ISBLANK(" + column + ") && " + column + " <> \"\"";
}

string EqualsTextCondition(string tableName, string value, params string[] candidates)
{
    string column = ColumnDax(tableName, candidates);
    return column == null ? "FALSE()" : column + " = \"" + value.Replace("\"", "\"\"") + "\"";
}

string BoolTrueCondition(string tableName, params string[] candidates)
{
    string column = ColumnDax(tableName, candidates);
    return column == null ? "FALSE()" : column + " = TRUE()";
}

string ContainsTextCondition(string tableName, string value, params string[] candidates)
{
    string column = ColumnDax(tableName, candidates);
    return column == null ? "FALSE()" : "CONTAINSSTRING(LOWER(" + column + "), \"" + value.ToLowerInvariant().Replace("\"", "\"\"") + "\")";
}

bool IsNumericColumn(dynamic column)
{
    string dataType = Convert.ToString(column.DataType);
    return dataType.IndexOf("Decimal", StringComparison.OrdinalIgnoreCase) >= 0 ||
           dataType.IndexOf("Double", StringComparison.OrdinalIgnoreCase) >= 0 ||
           dataType.IndexOf("Int", StringComparison.OrdinalIgnoreCase) >= 0 ||
           dataType.IndexOf("Currency", StringComparison.OrdinalIgnoreCase) >= 0;
}

bool IsTextColumn(dynamic column)
{
    return Convert.ToString(column.DataType).IndexOf("String", StringComparison.OrdinalIgnoreCase) >= 0;
}

bool IsDateColumn(dynamic column)
{
    string dataType = Convert.ToString(column.DataType);
    return dataType.IndexOf("Date", StringComparison.OrdinalIgnoreCase) >= 0;
}

bool IsMoneyName(string name)
{
    string n = name.ToLowerInvariant();
    return n.Contains("amount") || n.Contains("cost") || n.Contains("price") ||
           n.Contains("revenue") || n.Contains("invoice") || n.Contains("bill") ||
           n.Contains("profit") || n.Contains("subtotal") || n.Contains("total job");
}

bool IsPercentName(string name)
{
    string n = name.ToLowerInvariant();
    return n.Contains("percentage") || n.Contains("percent") || n.Contains(" pct") || n.EndsWith("pct");
}

bool IsHoursName(string name)
{
    string n = name.ToLowerInvariant();
    return n.Contains("hour") || n.Contains("duration");
}

bool IsSqFtName(string name)
{
    string n = name.ToLowerInvariant();
    return n.Contains("sq ft") || n.Contains("square feet") || n.Contains("sqft");
}

bool IsUrlName(string name)
{
    string n = name.ToLowerInvariant();
    return n.Contains("url") || n.Contains("link");
}

bool IsCountName(string name)
{
    string n = name.ToLowerInvariant();
    return n.Contains("count") || n.Contains("number") || n.EndsWith(" id");
}

bool IsMedianOrPercentile(string name)
{
    string n = name.ToLowerInvariant();
    return n.StartsWith("median ") || n.StartsWith("p25 ") || n.StartsWith("p75 ") || n.Contains(" historical ");
}

bool ShouldHideColumn(string tableName, string columnName)
{
    string n = columnName.ToLowerInvariant();
    if (columnName.Equals("Job ID", StringComparison.OrdinalIgnoreCase)) return false;
    if (columnName.Equals("Repair ID", StringComparison.OrdinalIgnoreCase)) return false;

    if (n == "document id" || n == "template row id" || n == "repair material usage id" ||
        n == "repair labor usage id" || n == "warning id" || n == "entry id" ||
        n == "rule id")
        return true;

    if (n.Contains("hash") || n.Contains("raw") || n.Contains("parser version") ||
        n.Contains("created at") || n.Contains("updated at") || n.Contains("modified at") ||
        n.Contains("extracted at") || n.Contains("source row") || n.Contains("source column") ||
        n.Contains("normalized product name"))
        return true;

    return false;
}

string DisplayFolderFor(string tableName, string columnName, bool hidden)
{
    if (hidden) return "Hidden Technical";
    string n = columnName.ToLowerInvariant();
    string t = tableName.ToLowerInvariant();

    if (n.Contains("url") || n.Contains("link")) return "Links";
    if (n.Contains("date") || n.Contains(" at") || n.Contains("year")) return "Dates";
    if (n.Contains("warning") || n.Contains("review") || n.Contains("qa")) return "Warnings";
    if (n.Contains("photo")) return "Photos";
    if (n.Contains("document") || n.Contains("file") || n.Contains("folder") || t.Contains("document")) return "Documents";
    if (n.Contains("customer")) return "Customer";
    if (n.Contains("job") || n.Contains("pipeline") || n.Contains("status") || n.Contains("division")) return "Job";
    if (IsMoneyName(columnName) || n.Contains("revenue") || n.Contains("invoice")) return "Financial";
    if (n.Contains("material") || t.Contains("material")) return "Materials";
    if (n.Contains("labor") || n.Contains("hour") || t.Contains("labor")) return "Labor";
    if (n.Contains("repair") || t.Contains("repair")) return "Repair";
    if (n.Contains("price") || n.Contains("vendor") || n.Contains("product") || t.Contains("pricing")) return "Pricing";
    if (n.Contains("template") || n.Contains("parser") || n.Contains("extraction") || n.Contains("bucket")) return "Parser";
    if (n.Contains("historical") || n.Contains("median") || n.Contains("p25") || n.Contains("p75") || n.Contains("evidence")) return "History";
    if (n.Contains("quality")) return "Quality";
    return "General";
}

string DescriptionFor(string tableName, string columnName)
{
    if (columnDescriptions.ContainsKey(columnName)) return columnDescriptions[columnName];

    string n = columnName.ToLowerInvariant();
    if (n.Contains("url") || n.Contains("link")) return "Clickable link for navigation to the related source artifact.";
    if (n.Contains("job id")) return "Business job identifier used for relationships across job-related tables.";
    if (n.Contains("repair id")) return "Repair identifier used for relationships across repair tables.";
    if (n.Contains("evidence count")) return "Number of historical observations supporting this row.";
    if (n.Contains("confidence")) return "Confidence label derived from supporting historical evidence.";
    if (n.Contains("template bucket")) return "Canonical parser bucket assigned to an estimate template row.";
    if (n.Contains("package")) return "Material, labor, repair, or estimating package category.";
    if (n.Contains("status")) return "Current workflow or processing status.";
    if (n.Contains("source")) return "Source location or source context for traceability.";
    if (n.Contains("warning")) return "Warning text for operational or data-quality review.";
    if (IsMoneyName(columnName)) return "Financial amount used for operational and estimating analysis.";
    if (IsSqFtName(columnName)) return "Square-foot area used for project and estimating analysis.";
    if (IsHoursName(columnName)) return "Labor time used for productivity and cost analysis.";
    if (IsPercentName(columnName)) return "Percentage value used for margin, confidence, or variance analysis.";
    return "Business field from " + tableName + ".";
}

void TryAddSynonyms(object target, IEnumerable<string> synonyms)
{
    if (target == null || synonyms == null) return;
    var clean = new List<string>();
    foreach (string synonym in synonyms)
    {
        AddUnique(clean, synonym);
    }
    if (clean.Count == 0) return;

    try
    {
        var property = target.GetType().GetProperty("Synonyms", BindingFlags.Public | BindingFlags.Instance);
        if (property == null) return;

        var value = property.GetValue(target, null);
        if (value == null && property.CanWrite)
        {
            property.SetValue(target, String.Join(",", clean), null);
            return;
        }

        if (value is string && property.CanWrite)
        {
            var existing = new List<string>();
            foreach (string item in ((string)value).Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
            {
                AddUnique(existing, item);
            }
            foreach (var s in clean)
            {
                AddUnique(existing, s);
            }
            property.SetValue(target, String.Join(",", existing.ToArray()), null);
            return;
        }

        var contains = value.GetType().GetMethod("Contains", new[] { typeof(string) });
        var add = value.GetType().GetMethod("Add", new[] { typeof(string) });
        if (add != null)
        {
            foreach (var s in clean)
            {
                bool exists = false;
                if (contains != null)
                {
                    exists = (bool)contains.Invoke(value, new object[] { s });
                }
                if (!exists) add.Invoke(value, new object[] { s });
            }
        }
    }
    catch
    {
        // Synonyms are supported differently across Tabular Editor / model versions.
        // Ignore when unavailable; descriptions and names still improve Copilot.
    }
}

void TrySetDataCategory(dynamic column, string dataCategory)
{
    try
    {
        column.DataCategory = dataCategory;
    }
    catch
    {
        // Some compatibility levels or object types do not expose DataCategory.
    }
}

void ConfigureSummarization(dynamic column, string friendlyName)
{
    try
    {
        if (IsTextColumn(column) || IsUrlName(friendlyName) || friendlyName.EndsWith(" ID", StringComparison.OrdinalIgnoreCase))
        {
            column.SummarizeBy = AggregateFunction.None;
            return;
        }
        if (!IsNumericColumn(column)) return;

        if (IsMedianOrPercentile(friendlyName))
            column.SummarizeBy = AggregateFunction.None;
        else if (IsPercentName(friendlyName))
            column.SummarizeBy = AggregateFunction.Average;
        else if (friendlyName.IndexOf("Evidence Count", StringComparison.OrdinalIgnoreCase) >= 0 ||
                 friendlyName.IndexOf("Job Count", StringComparison.OrdinalIgnoreCase) >= 0 ||
                 IsMoneyName(friendlyName) ||
                 IsHoursName(friendlyName) ||
                 IsSqFtName(friendlyName))
            column.SummarizeBy = AggregateFunction.Sum;
        else
            column.SummarizeBy = AggregateFunction.None;
    }
    catch
    {
        // Keep script compatible with models where summarization cannot be changed.
    }
}

void ConfigureFormat(dynamic columnOrMeasure, string name)
{
    try
    {
        if (IsPercentName(name))
            columnOrMeasure.FormatString = "0.0%";
        else if (name.IndexOf("Price Per Sq Ft", StringComparison.OrdinalIgnoreCase) >= 0 ||
                 name.IndexOf("Cost Per Sq Ft", StringComparison.OrdinalIgnoreCase) >= 0)
            columnOrMeasure.FormatString = "$#,0.00;($#,0.00);$#,0.00";
        else if (IsMoneyName(name))
            columnOrMeasure.FormatString = "$#,0;($#,0);$#,0";
        else if (IsHoursName(name))
            columnOrMeasure.FormatString = "#,0.0";
        else if (IsSqFtName(name) || IsCountName(name))
            columnOrMeasure.FormatString = "#,0";
        else if (name.IndexOf("Median", StringComparison.OrdinalIgnoreCase) >= 0 ||
                 name.IndexOf("P25", StringComparison.OrdinalIgnoreCase) >= 0 ||
                 name.IndexOf("P75", StringComparison.OrdinalIgnoreCase) >= 0)
            columnOrMeasure.FormatString = "#,0.0000";
        else
            columnOrMeasure.FormatString = "#,0.00";
    }
    catch
    {
        // Ignore unsupported format changes.
    }
}

void ConfigureColumn(dynamic table, dynamic column)
{
    string original = column.Name;
    string friendly = FriendlyName(original);
    bool friendlyNameAlreadyExists = false;
    foreach (var existingColumn in ColumnsOf(table))
    {
        if (!Object.ReferenceEquals(existingColumn, column) &&
            existingColumn.Name.Equals(friendly, StringComparison.OrdinalIgnoreCase))
        {
            friendlyNameAlreadyExists = true;
            break;
        }
    }

    if (!String.IsNullOrWhiteSpace(friendly) &&
        !original.Equals(friendly, StringComparison.Ordinal) &&
        !friendlyNameAlreadyExists)
    {
        column.Name = friendly;
    }

    string name = column.Name;
    bool hide = ShouldHideColumn(table.Name, name);
    column.IsHidden = hide;
    column.DisplayFolder = DisplayFolderFor(table.Name, name, hide);
    column.Description = DescriptionFor(table.Name, name);

    if (IsUrlName(name)) TrySetDataCategory(column, "WebUrl");
    ConfigureSummarization(column, name);
    if (IsDateColumn(column))
    {
        try { column.FormatString = "m/d/yyyy"; } catch {}
    }
    else if (IsNumericColumn(column))
    {
        ConfigureFormat(column, name);
    }

    TryAddSynonyms(column, BuildSynonymList(name, synonymMap.ContainsKey(name) ? synonymMap[name] : null));
}

dynamic AddOrUpdateMeasure(dynamic table, string name, string expression, string displayFolder, string formatString, string description, params string[] synonyms)
{
    dynamic measure = null;
    foreach (var existingMeasure in MeasuresOf(table))
    {
        if (existingMeasure.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
        {
            measure = existingMeasure;
            break;
        }
    }

    if (measure == null)
    {
        measure = table.AddMeasure(name, expression, displayFolder);
    }
    else
    {
        measure.Expression = expression;
        measure.DisplayFolder = displayFolder;
    }

    measure.Description = description;
    if (!String.IsNullOrWhiteSpace(formatString)) measure.FormatString = formatString;
    TryAddSynonyms(measure, BuildSynonymList(name, synonyms));
    return measure;
}

string RevenueExpression()
{
    string jobsRevenue = SumExpr("Jobs", "final_price", "Final Price");
    if (jobsRevenue != "BLANK()") return jobsRevenue;
    return SumExpr("Repairs", "invoice_amount", "Invoice Amount");
}

dynamic FindPerspective(string perspectiveName)
{
    try
    {
        foreach (var perspective in (IEnumerable)Model.Perspectives)
        {
            if (perspective.Name.Equals(perspectiveName, StringComparison.OrdinalIgnoreCase)) return perspective;
        }
    }
    catch
    {
        // Some older/compatibility models may not expose perspectives.
    }
    return null;
}

dynamic GetOrCreatePerspective(string perspectiveName)
{
    dynamic perspective = FindPerspective(perspectiveName);
    if (perspective != null) return perspective;

    try
    {
        // Tabular Editor 2/3 TOMWrapper API.
        return Model.AddPerspective(perspectiveName);
    }
    catch
    {
        // If perspectives are unsupported in this model, skip safely.
    }
    return FindPerspective(perspectiveName);
}

void SetInPerspective(dynamic modelObject, dynamic perspective, bool included)
{
    if (modelObject == null || perspective == null) return;

    try
    {
        // Tabular Editor 2/3 exposes an InPerspective indexer on tables,
        // columns, measures, and hierarchies. This direct dynamic call is kept
        // in a try/catch because TE API availability varies by compatibility level.
        modelObject.InPerspective[perspective] = included;
        return;
    }
    catch
    {
    }

    try
    {
        // Reflection fallback for hosts that expose InPerspective but do not bind
        // cleanly through dynamic dispatch.
        var property = modelObject.GetType().GetProperty("InPerspective", BindingFlags.Public | BindingFlags.Instance);
        if (property == null) return;
        var indexer = property.GetValue(modelObject, null);
        if (indexer == null) return;
        var itemProperty = indexer.GetType().GetProperty("Item");
        if (itemProperty == null) return;
        itemProperty.SetValue(indexer, included, new object[] { perspective });
    }
    catch
    {
        // Skip objects that cannot be assigned to perspectives in this model.
    }
}

bool NameContainsAny(string value, string[] keywords)
{
    if (String.IsNullOrWhiteSpace(value) || keywords == null) return false;
    string lower = value.ToLowerInvariant();
    foreach (string keyword in keywords)
    {
        if (!String.IsNullOrWhiteSpace(keyword) && lower.Contains(keyword.ToLowerInvariant())) return true;
    }
    return false;
}

string ObjectSearchText(dynamic modelObject)
{
    if (modelObject == null) return "";
    string text = "";
    try { text += " " + Convert.ToString(modelObject.Name); } catch {}
    try { text += " " + Convert.ToString(modelObject.Description); } catch {}
    try { text += " " + Convert.ToString(modelObject.DisplayFolder); } catch {}
    return text;
}

bool IsVisibleColumn(dynamic column)
{
    if (column == null) return false;
    try
    {
        if (column.IsHidden) return false;
    }
    catch {}
    return true;
}

void ClearPerspective(dynamic perspective)
{
    if (perspective == null) return;
    foreach (var table in Model.Tables)
    {
        SetInPerspective(table, perspective, false);
        foreach (var column in ColumnsOf(table))
        {
            SetInPerspective(column, perspective, false);
        }
        foreach (var measure in MeasuresOf(table))
        {
            SetInPerspective(measure, perspective, false);
        }
        try
        {
            foreach (var hierarchy in (IEnumerable)table.Hierarchies)
            {
                SetInPerspective(hierarchy, perspective, false);
            }
        }
        catch {}
    }
}

void IncludeTableColumns(dynamic perspective, string tableName, string[] priorityKeywords)
{
    var table = FindTable(tableName);
    if (table == null || perspective == null) return;

    SetInPerspective(table, perspective, true);
    foreach (var column in ColumnsOf(table))
    {
        if (!IsVisibleColumn(column)) continue;
        // Include the business-facing columns from the table. Priority keywords
        // are used by IncludeTablePriorityColumns below to make sure important
        // hidden-by-default fields can still be added if needed later.
        SetInPerspective(column, perspective, true);
    }
}

void IncludeTablePriorityColumns(dynamic perspective, string tableName, string[] priorityKeywords)
{
    var table = FindTable(tableName);
    if (table == null || perspective == null) return;

    SetInPerspective(table, perspective, true);
    foreach (var column in ColumnsOf(table))
    {
        if (!IsVisibleColumn(column)) continue;
        if (NameContainsAny(ObjectSearchText(column), priorityKeywords))
        {
            SetInPerspective(column, perspective, true);
        }
    }
}

void IncludeNamedMeasures(dynamic perspective, string[] measureNames)
{
    if (perspective == null || measureNames == null) return;
    foreach (var table in Model.Tables)
    {
        foreach (var measure in MeasuresOf(table))
        {
            if (StringArrayContains(measureNames, measure.Name))
            {
                SetInPerspective(table, perspective, true);
                SetInPerspective(measure, perspective, true);
            }
        }
    }
}

void IncludeMeasuresByKeywords(dynamic perspective, string[] keywords)
{
    if (perspective == null || keywords == null) return;
    foreach (var table in Model.Tables)
    {
        foreach (var measure in MeasuresOf(table))
        {
            if (NameContainsAny(ObjectSearchText(measure), keywords))
            {
                SetInPerspective(table, perspective, true);
                SetInPerspective(measure, perspective, true);
            }
        }
    }
}

void BuildPerspective(string perspectiveName, string[] tableNames, string[] priorityKeywords, string[] measureNames)
{
    dynamic perspective = GetOrCreatePerspective(perspectiveName);
    if (perspective == null) return;

    ClearPerspective(perspective);
    foreach (string tableName in tableNames)
    {
        IncludeTableColumns(perspective, tableName, priorityKeywords);
        IncludeTablePriorityColumns(perspective, tableName, priorityKeywords);
    }
    IncludeNamedMeasures(perspective, measureNames);
    IncludeMeasuresByKeywords(perspective, priorityKeywords);
}

foreach (string tableName in sprayTecTables)
{
    var table = FindTable(tableName);
    if (table == null) continue;

    if (tableDescriptions.ContainsKey(tableName)) table.Description = tableDescriptions[tableName];

    try
    {
        if (tableName.Contains("Defaults") || tableName.Contains("Pricing") || tableName.Contains("Rule") || tableName.Contains("Unknown"))
            table.DisplayFolder = "Reference";
        else if (tableName.Contains("History"))
            table.DisplayFolder = "History";
    }
    catch {}

    TryAddSynonyms(table, BuildSynonymList(tableName, synonymMap.ContainsKey(tableName) ? synonymMap[tableName] : null));

    foreach (var column in ColumnsOf(table))
    {
        ConfigureColumn(table, column);
    }
}

dynamic measureTable = FindTable("Jobs");
if (measureTable == null)
{
    foreach (var table in Model.Tables)
    {
        if (StringArrayContains(sprayTecTables, table.Name))
        {
            measureTable = table;
            break;
        }
    }
}
if (measureTable != null)
{
    string completedCondition = EqualsTextCondition("Jobs", "Completed", "pipeline_status", "Pipeline Status") +
        " || " + EqualsTextCondition("Jobs", "Completed", "status", "Status");
    string pipelineCondition = "NOT (" + completedCondition + ")";

    AddOrUpdateMeasure(measureTable, "Total Jobs",
        CountRowsExpr("Jobs"),
        "Measures",
        "#,0",
        "Count of jobs in the model.",
        "job count", "number of jobs");

    AddOrUpdateMeasure(measureTable, "Completed Jobs",
        FilterCountExpr("Jobs", completedCondition),
        "Measures",
        "#,0",
        "Count of completed jobs.",
        "completed projects", "finished jobs");

    AddOrUpdateMeasure(measureTable, "Pipeline Jobs",
        FilterCountExpr("Jobs", pipelineCondition),
        "Measures",
        "#,0",
        "Count of jobs not marked completed.",
        "open jobs", "pipeline count");

    AddOrUpdateMeasure(measureTable, "Total Revenue",
        RevenueExpression(),
        "Measures",
        "$#,0;($#,0);$#,0",
        "Total revenue based on job final price when available.",
        "sales", "revenue", "total sales");

    AddOrUpdateMeasure(measureTable, "Total Invoice Amount",
        SumExpr("Repairs", "invoice_amount", "Invoice Amount"),
        "Measures",
        "$#,0;($#,0);$#,0",
        "Total invoice amount from repair history.",
        "billing", "invoice total", "billings");

    AddOrUpdateMeasure(measureTable, "Average Price Per Sq Ft",
        AverageExpr("Jobs", "price_per_sqft", "Price Per Sq Ft"),
        "Measures",
        "$#,0.00;($#,0.00);$#,0.00",
        "Average project price per square foot.",
        "average unit price", "price per square foot");

    AddOrUpdateMeasure(measureTable, "Average Estimated Sq Ft",
        AverageExpr("Jobs", "estimated_sqft", "Estimated Square Feet"),
        "Measures",
        "#,0",
        "Average estimated square footage.",
        "average roof area", "average sqft");

    AddOrUpdateMeasure(measureTable, "Average Labor Hours",
        AverageExpr("Labor History", "total_hours", "Total Hours"),
        "Measures",
        "#,0.0",
        "Average labor hours from historical labor records.",
        "average hours", "labor hours");

    AddOrUpdateMeasure(measureTable, "Average Invoice",
        AverageExpr("Repairs", "invoice_amount", "Invoice Amount"),
        "Measures",
        "$#,0;($#,0);$#,0",
        "Average repair invoice amount.",
        "average bill", "average billing");

    AddOrUpdateMeasure(measureTable, "Average Historical Labor",
        AverageExpr("Labor Defaults", "median_hours_per_1000_sqft", "Median Labor Hours Per 1000 Sq Ft"),
        "Measures",
        "#,0.0",
        "Average of historical median labor defaults.",
        "default labor", "historical labor");

    AddOrUpdateMeasure(measureTable, "Jobs With Warnings",
        FilterCountExpr("Jobs", TextNotBlankCondition("Jobs", "warnings", "Warnings")),
        "Measures",
        "#,0",
        "Count of jobs with warning text.",
        "warning jobs", "jobs with issues");

    AddOrUpdateMeasure(measureTable, "Repair Revenue",
        SumExpr("Repairs", "invoice_amount", "Invoice Amount"),
        "Measures",
        "$#,0;($#,0);$#,0",
        "Total repair invoice amount.",
        "repair sales", "service revenue");

    AddOrUpdateMeasure(measureTable, "Repair Count",
        CountRowsExpr("Repairs"),
        "Measures",
        "#,0",
        "Count of repair records.",
        "service calls", "repair jobs");

    AddOrUpdateMeasure(measureTable, "Material Packages",
        DistinctCountExpr("Material History", "package", "Package", "material_package", "Material Package"),
        "Measures",
        "#,0",
        "Count of distinct material packages in historical material records.",
        "material package count");

    AddOrUpdateMeasure(measureTable, "Labor Packages",
        DistinctCountExpr("Labor History", "package", "Package", "labor_package", "Labor Package"),
        "Measures",
        "#,0",
        "Count of distinct labor packages in historical labor records.",
        "labor package count");

    AddOrUpdateMeasure(measureTable, "Total Photos",
        SumExpr("Jobs", "photo_count", "Photo Count"),
        "Measures",
        "#,0",
        "Total photo count recorded across jobs.",
        "photos", "job photos");

    AddOrUpdateMeasure(measureTable, "Total Documents",
        CountRowsExpr("Documents"),
        "Measures",
        "#,0",
        "Count of scanned documents.",
        "files", "attachments");

    AddOrUpdateMeasure(measureTable, "Total Estimate Rows",
        CountRowsExpr("Estimate Template Rows"),
        "Measures",
        "#,0",
        "Count of parsed estimate template rows.",
        "estimate lines", "template rows");

    AddOrUpdateMeasure(measureTable, "Total Unknown Rows",
        SumExpr("Unknown Templates", "row_count", "Row Count"),
        "Measures",
        "#,0",
        "Count of estimate template rows still classified as unknown.",
        "unknown rows", "unmapped template rows");

    AddOrUpdateMeasure(measureTable, "Warning Count",
        CountRowsExpr("Quality Warnings"),
        "Measures",
        "#,0",
        "Count of quality warning records.",
        "warnings", "QA warnings");

    AddOrUpdateMeasure(measureTable, "Average Revenue Per Job",
        "DIVIDE([Total Revenue], [Total Jobs])",
        "KPIs",
        "$#,0;($#,0);$#,0",
        "Average revenue per job.",
        "average job revenue", "revenue per job");

    AddOrUpdateMeasure(measureTable, "Average Repair Invoice",
        AverageExpr("Repairs", "invoice_amount", "Invoice Amount"),
        "KPIs",
        "$#,0;($#,0);$#,0",
        "Average invoice amount for repair work.",
        "average repair bill", "service average invoice");

    AddOrUpdateMeasure(measureTable, "Average Material Cost",
        AverageExpr("Material History", "total_cost", "Total Cost"),
        "KPIs",
        "$#,0;($#,0);$#,0",
        "Average historical material package cost.",
        "average material spend", "material cost");

    AddOrUpdateMeasure(measureTable, "Average Labor Cost",
        AverageExpr("Labor History", "total_cost", "Total Cost"),
        "KPIs",
        "$#,0;($#,0);$#,0",
        "Average historical labor package cost.",
        "average labor spend", "labor cost");

    AddOrUpdateMeasure(measureTable, "Jobs Missing Invoice",
        FilterCountExpr("Jobs", "NOT (" + TextNotBlankCondition("Jobs", "invoice_url", "Invoice URL") + ")"),
        "KPIs",
        "#,0",
        "Count of jobs without an invoice link.",
        "missing invoices", "no invoice");

    AddOrUpdateMeasure(measureTable, "Jobs Missing Contract",
        FilterCountExpr("Jobs", "NOT (" + BoolTrueCondition("Jobs", "has_signed_contract", "Has Signed Contract") + ")"),
        "KPIs",
        "#,0",
        "Count of jobs without a signed contract flag.",
        "missing contracts", "no contract");

    AddOrUpdateMeasure(measureTable, "Jobs With QA Warnings",
        "[Jobs With Warnings]",
        "KPIs",
        "#,0",
        "Count of jobs with quality warnings.",
        "QA warning jobs", "jobs with data quality issues");

    string materialPackage = ColumnDax("Material Defaults", "package", "Package");
    string medianQty = ColumnDax("Material Defaults", "median_qty_per_sqft", "Median Quantity Per Sq Ft");
    string primerExpr = materialPackage == null || medianQty == null
        ? "BLANK()"
        : "AVERAGEX(FILTER(" + TableDax("Material Defaults") + ", CONTAINSSTRING(LOWER(" + materialPackage + "), \"primer\")), " + medianQty + ")";
    string coatingExpr = materialPackage == null || medianQty == null
        ? "BLANK()"
        : "AVERAGEX(FILTER(" + TableDax("Material Defaults") + ", CONTAINSSTRING(LOWER(" + materialPackage + "), \"coating\") || CONTAINSSTRING(LOWER(" + materialPackage + "), \"silicone\")), " + medianQty + ")";

    AddOrUpdateMeasure(measureTable, "Average Historical Primer Usage",
        primerExpr,
        "KPIs",
        "#,0.0000",
        "Average historical primer quantity per square foot from material defaults.",
        "primer usage", "primer default");

    AddOrUpdateMeasure(measureTable, "Average Historical Coating Usage",
        coatingExpr,
        "KPIs",
        "#,0.0000",
        "Average historical coating quantity per square foot from material defaults.",
        "coating usage", "silicone usage", "coating default");
}

BuildPerspective(
    "Executive",
    new[] { "Jobs", "Quality Warnings", "Repairs", "Timesheets" },
    new[]
    {
        "job count", "revenue", "invoice", "pipeline", "division", "customer",
        "warning", "completed", "repair revenue", "labor hours", "status",
        "final price", "gross profit", "duration"
    },
    new[]
    {
        "Total Jobs", "Completed Jobs", "Pipeline Jobs", "Total Revenue",
        "Total Invoice Amount", "Average Price Per Sq Ft", "Average Estimated Sq Ft",
        "Average Labor Hours", "Average Invoice", "Jobs With Warnings",
        "Repair Revenue", "Repair Count", "Total Photos", "Warning Count",
        "Average Revenue Per Job", "Average Repair Invoice", "Jobs With QA Warnings"
    }
);

BuildPerspective(
    "Estimator",
    new[]
    {
        "Jobs", "Estimate Template Rows", "Material Defaults", "Labor Defaults",
        "Material History", "Labor History", "Pricing Catalog", "Rule Candidates",
        "Estimator Feedback", "Unknown Templates"
    },
    new[]
    {
        "estimate", "default", "material", "labor", "package", "template bucket",
        "quantity per sq ft", "hours per 1000", "pricing", "price", "evidence",
        "confidence", "rule", "unknown", "bucket", "warranty", "coating",
        "roof condition", "historical", "median", "p25", "p75"
    },
    new[]
    {
        "Total Estimate Rows", "Total Unknown Rows", "Material Packages",
        "Labor Packages", "Average Historical Labor", "Average Historical Primer Usage",
        "Average Historical Coating Usage", "Average Material Cost", "Average Labor Cost"
    }
);

BuildPerspective(
    "Repairs",
    new[] { "Repairs", "Repair Materials", "Repair Labor", "Repair Defaults", "Jobs" },
    new[]
    {
        "repair", "service", "patch", "roof type", "repair type", "labor hours",
        "invoice", "total bill", "gross profit", "material", "default", "customer",
        "job", "status"
    },
    new[]
    {
        "Repair Revenue", "Repair Count", "Average Repair Invoice",
        "Total Invoice Amount", "Average Invoice"
    }
);

BuildPerspective(
    "Operations",
    new[]
    {
        "Jobs", "Documents", "Quality Warnings", "Timesheets",
        "Estimate Template Rows", "Unknown Templates"
    },
    new[]
    {
        "job status", "status", "pipeline", "document", "file", "extraction",
        "warning", "quality", "timesheet", "employee", "duration", "template",
        "unknown", "parser", "scan", "folder", "link"
    },
    new[]
    {
        "Total Jobs", "Pipeline Jobs", "Completed Jobs", "Total Documents",
        "Warning Count", "Jobs With Warnings", "Total Estimate Rows",
        "Total Unknown Rows", "Total Photos"
    }
);
