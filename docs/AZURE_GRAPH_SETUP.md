# Azure / Microsoft Graph setup

This scanner uses Microsoft Graph with an Azure app registration.

## 1. Create an app registration

In Microsoft Entra admin center:

1. Go to **Applications → App registrations → New registration**.
2. Name it `SharePoint Job Scanner`.
3. Choose **Single tenant** unless this must run across tenants.
4. Save the **Application/client ID** and **Directory/tenant ID**.

## 2. Add a client secret

1. Open the app registration.
2. Go to **Certificates & secrets**.
3. Create a new client secret.
4. Copy the secret value immediately.

## 3. Add Microsoft Graph API permissions

For read-only scanning, add **Application permissions**:

- `Sites.Read.All` or `Files.Read.All`

Then click **Grant admin consent**.

For later write-back to SharePoint Lists or files, add only when needed:

- `Sites.ReadWrite.All`

## 4. Create `.env`

Copy `.env.example` to `.env` and fill:

```bash
MS_TENANT_ID=...
MS_CLIENT_ID=...
MS_CLIENT_SECRET=...
```

## 5. Test connection

Run the sync command from the project root:

```bash
python -m jobscan.sharepoint_sync \
  --sharepoint-url "https://yourtenant.sharepoint.com/sites/Operations" \
  --library "Documents" \
  --folder "Estimates" \
  --out output/job_index.csv \
  --json output/job_index.json \
  --xlsx output/job_index.xlsx
```

If it fails with permissions, confirm the app uses **Application permissions**, not Delegated permissions, and that admin consent was granted.
