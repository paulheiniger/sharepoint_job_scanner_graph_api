# QuickBooks Online integration

The Spray-Tec Business API synchronizes a deliberately limited, read-only
accounting projection from QuickBooks Online into PostgreSQL. Assistant requests
query PostgreSQL; they do not call QuickBooks on demand and cannot mutate the
company file.

Initial scope:

- customers and sub-customers
- estimates
- invoices
- payments
- credit memos

Payroll, employees, bank accounts, payment-card data, journal entries, bills,
vendors, and purchase data are excluded.

## One-time Intuit developer setup

Create a Spray-Tec-owned QuickBooks Online app in the Intuit Developer portal.
Configure the Accounting scope and register this exact production redirect URI:

`https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io/v1/integrations/quickbooks/callback`

Add the following values as Azure Container App secrets/environment variables;
never commit them:

```text
QUICKBOOKS_ENVIRONMENT=production
QUICKBOOKS_EXPECTED_COMPANY_NAME=Spray-Tec Inc.
QUICKBOOKS_CLIENT_ID=<Intuit app client ID>
QUICKBOOKS_CLIENT_SECRET=<Intuit app client secret>
QUICKBOOKS_REDIRECT_URI=https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io/v1/integrations/quickbooks/callback
QUICKBOOKS_TOKEN_ENCRYPTION_KEY=<Fernet key>
QUICKBOOKS_OAUTH_STATE_SECRET=<at least 32 random bytes>
```

Generate the two local secrets without printing or committing them elsewhere:

```bash
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
openssl rand -base64 48
```

## Administrator handoff

After deployment, an API administrator requests a single-use authorization URL:

```bash
curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer $ESTIMATOR_API_KEY" \
  "https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io/v1/integrations/quickbooks/authorize"
```

Send only the returned Intuit URL to the QuickBooks Primary Admin or Company
Admin. The administrator signs in to Intuit, selects **Spray-Tec Inc.**, reviews
the Accounting permission, and approves. They should not give Spray-Tec's
password or verification code to a developer. The callback rejects a different
company name and stores rotating OAuth tokens encrypted at rest.

The OAuth callback automatically starts the initial full synchronization after
approval. It can be safely rerun manually if its status reports an error:

```bash
curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer $ESTIMATOR_API_KEY" \
  "https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io/v1/integrations/quickbooks/sync?full=true"
```

Normal refreshes use the same endpoint without `full=true`; each entity retains
its source timestamp and a five-minute overlap for safe idempotent upserts.
On the office refresh host, set `RUN_QUICKBOOKS_SYNC=1` after authorization to
include an incremental QuickBooks sync in `scripts/daily_refresh.sh`.

## Assistant actions

- `getQuickBooksAccountingSummary`
- `getQuickBooksAccountingExceptions`
- `getQuickBooksCustomerContext`

All three are read-only and expose sync freshness and warnings. OAuth and sync
administration routes are deliberately excluded from the GPT action schema.
