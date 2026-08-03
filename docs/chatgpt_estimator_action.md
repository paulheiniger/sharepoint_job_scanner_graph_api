# Test the Estimator Context API with ChatGPT Pro

A private custom GPT can call the Phase A estimator context API before
Microsoft Copilot licensing and Entra administration are available.

This test exercises conversational scope interpretation, evidence retrieval,
semantic estimate drafting, and explicit-confirmation roofing or insulation workbook
generation. It does not independently validate workbook formula outputs or
upload the artifact to SharePoint.

## Architecture

```text
Private custom GPT
    -> HTTPS ngrok tunnel
    -> bearer API-key gate
    -> local estimator context API
    -> read-only Neon estimator data
```

Keep the custom GPT private. Do not use unauthenticated tunnel traffic.

## 1. Configure local secrets

The API loads the repository `.env` file without printing or committing it.
Ensure it contains:

```text
NEON_DATABASE_URL=<existing read-only or least-privilege Neon connection>
ESTIMATOR_API_KEY=<new random test-only secret>
ESTIMATOR_API_REQUIRE_AUTH=false
```

Generate the test-only secret locally:

```bash
openssl rand -hex 32
```

Store the same value in `.env` and later in the custom GPT's API-key
authentication field. Do not place the value in OpenAPI, source code, shell
history, screenshots, or chat messages.

## 2. Start and verify the local API

From the repository root:

```bash
python -m pip install -r services/estimator_api/requirements.txt

python -m services.estimator_api.server
```

In a second terminal:

```bash
curl http://127.0.0.1:8770/health
```

The health response should report:

```json
{
  "ok": true,
  "api_key_required": true
}
```

Verify that an unauthenticated context request returns `401`:

```bash
curl \
  --request POST \
  --header "Content-Type: application/json" \
  --data '{"raw_notes":"30x40 metal building","template_type":"insulation"}' \
  http://127.0.0.1:8770/v1/estimating/context
```

## 3. Start the HTTPS tunnel

ngrok is already installed and configured on this machine:

```bash
ngrok http 8770
```

Copy the temporary `https://...ngrok...` forwarding origin. The URL changes
when a free temporary tunnel is restarted.

Do not start the tunnel until the health response confirms
`api_key_required=true`.

## 4. Generate the action contract

Replace the placeholder with the HTTPS ngrok origin:

```bash
python -m services.estimator_api.generate_openapi \
  --server-url "https://YOUR-NGROK-HOST" \
  --output /tmp/spraytec-chatgpt-action.json
```

The checked-in OpenAPI remains environment-neutral. The `/tmp` copy is the
temporary contract to paste into ChatGPT.

## 5. Create the private custom GPT

Using ChatGPT on the web:

1. Open `https://chatgpt.com/gpts/editor`.
2. Create a GPT named `Spray-Tec Estimator Test`.
3. Copy `services/estimator_api/chatgpt_instructions.md` into Instructions.
4. Under Actions, create a new action and import or paste
   `/tmp/spraytec-chatgpt-action.json`.
5. Configure Authentication:
   - type: API Key;
   - auth type: Bearer;
   - API key: the value stored as `ESTIMATOR_API_KEY`.
6. Under Capabilities, enable Code Interpreter & Data Analysis so the GPT can
   render charts and analyze CSV files returned by actions.
7. Keep the GPT private.
8. Use Preview to run the test prompts below.

The action must use a standard model that supports actions. ChatGPT's separate
Pro mode cannot execute custom actions.

## 6. Test prompts

Start with:

```text
Prepare a preliminary insulation estimate from these notes. Retrieve historical
context before making recommendations. Show material quantities, labor evidence,
assumptions, missing inputs, and source estimate links.

30x40 metal building, 9-foot walls. Include outside walls and underside of roof
deck. Use closed-cell foam. Job site is 314 E Aberdeen Drive, Trenton, OH.
```

Then test correction behavior:

```text
The roof deck is excluded. Revise the semantic draft and identify which
quantities and labor assumptions changed. Do not claim a workbook was created.
```

For a roofing, insulation, or flooring draft, review the proposed values and then test the consequential
action with an explicit follow-up such as:

```text
I approve the displayed scope, material quantities, labor plan, pricing,
and allowances. Create the draft estimate workbook now.
```

Verify that ChatGPT:

- calls `getEstimatorContext`;
- cites historical job IDs or estimate files;
- separates historical observations from assumptions;
- uses current-job mileage only;
- asks for missing calculation inputs;
- explicitly includes or excludes sales/inspection travel, truck travel,
  loading labor, and traveling labor before confirmation;
- supplies trip count and round-trip mileage for included travel, and hours per
  trip plus crew size for included loading/traveling labor;
- never claims workbook creation before explicit confirmation;
- returns the signed draft download link after a successful confirmed action;
- summarizes the API-validated calculated outputs, including travel and labor
  costs; and
- still works if historical workbook row numbers differ.

Then test a multi-option request:

```text
Prepare separate 10-year and 15-year warranty options. Show the complete scope,
materials, labor, logistics, pricing, and allowances for both. After I approve
both displayed options, create both draft estimate files.
```

Verify that ChatGPT calls `generateEstimateWorkbookOptions` once, sends two
complete uniquely labeled options rather than partial overrides, and returns a
separate signed link and calculated-output summary for each workbook.

Then test chart generation:

```text
Show the current sales pipeline by stage as a chart. Label dollars and job
counts clearly, state the data as-of time, and summarize the two most important
owner-level observations without treating a bounded job list as the total.
```

Verify that ChatGPT calls `getChartDataset`, uses the returned rollup and chart
specification, renders a chart with Data Analysis, and states relevant filters,
freshness, warnings, and coverage. Also test `downloadChartDatasetCsv` by asking
for the chart's underlying CSV file.

The response includes `response_budget`. The action-safe profile preserves the
most relevant semantic evidence and source links while limiting repeated source
details, pricing candidates, product guidance, and memories. A truncated field
is still usable evidence; ChatGPT should disclose the truncation rather than
treating retrieval as failed.

## 7. Shut down

Stop ngrok when testing is complete. Rotate or remove the temporary
`ESTIMATOR_API_KEY` before the next test window.

## Current limitations

- Context and business operations are read-only. Single and multi-option
  workbook generation are consequential, confirmation-gated actions.
- ChatGPT reasoning is not deterministic workbook validation; the API performs
  template profiling, recalculation, and output checks before delivery.
- The draft is stored temporarily on the API host and is not uploaded to
  SharePoint.
- The tunnel URL is temporary.
- Consumer ChatGPT data controls apply; use sanitized test notes unless the
  account's data settings and Spray-Tec policy allow real customer information.
