import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "/Users/paulheiniger/Downloads/Estimate + Spec - Grossman Tuning AWM (1).xlsx";
const outputDir = "/Users/paulheiniger/Downloads/sharepoint_job_scanner_graph_api/tmp/grossman_reference_analysis/rendered";

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
await fs.mkdir(outputDir, { recursive: true });

const sheetOverview = await workbook.inspect({
  kind: "sheet",
  include: "id,name",
  maxChars: 12000,
});
console.log("SHEETS");
console.log(sheetOverview.ndjson);

const sheets = workbook.worksheets.items;
for (const sheet of sheets) {
  const used = sheet.getUsedRange();
  console.log(`USED ${sheet.name}`);
  console.log(JSON.stringify({ address: used?.address ?? null }));
  if (used) {
    const table = await workbook.inspect({
      kind: "table",
      sheetId: sheet.name,
      range: used.address,
      include: "values,formulas",
      tableMaxRows: 160,
      tableMaxCols: 24,
      tableMaxCellChars: 240,
      maxChars: 70000,
    });
    console.log(table.ndjson);
  }
  const safeName = sheet.name.replace(/[^a-z0-9_-]+/gi, "_");
  const preview = await workbook.render({
    sheetName: sheet.name,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  await fs.writeFile(`${outputDir}/${safeName}.png`, new Uint8Array(await preview.arrayBuffer()));
}

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "formula error scan",
  maxChars: 12000,
});
console.log("ERRORS");
console.log(errors.ndjson);
