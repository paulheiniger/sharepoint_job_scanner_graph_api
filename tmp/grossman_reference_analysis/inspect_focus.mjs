import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const input = await FileBlob.load(
  "/Users/paulheiniger/Downloads/Estimate + Spec - Grossman Tuning AWM (1).xlsx",
);
const workbook = await SpreadsheetFile.importXlsx(input);

for (const [sheetName, rangeAddress] of [
  ["Estimate", "A1:R191"],
  ["People", "A1:K34"],
  ["Job Spec", "A1:I50"],
  ["Tracking", "A20:Y29"],
]) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const range = sheet.getRange(rangeAddress);
  console.log(`FOCUS ${sheetName} ${rangeAddress}`);
  console.log(JSON.stringify({ values: range.values, formulas: range.formulas }));
}
