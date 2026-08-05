# Estimator API on Azure Container Apps

The development deployment uses Azure Container Apps with a private Azure
Container Registry and Azure Key Vault. The API remains protected by the same
bearer token used by the Custom GPT. Entra/Easy Auth is intentionally disabled
until tenant administration is available.

## Current resources

- Resource group: `spraytec-ai-rg` (`eastus2`)
- Container app: `spraytec-business-api`
- Container Apps environment: `spraytec-ai-env`
- Registry: `spraytecaiacr9cf94090.azurecr.io`
- Key Vault: `spraytec-ai-kv-9cf94090`
- Pull/runtime identity: `spraytec-api-pull-id`
- Public URL: `https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io`

The app is temporarily configured for one warm replica for the live
presentation. Estimator context source data is cached in-process for one hour
with `ESTIMATOR_CONTEXT_DATA_CACHE_TTL_SECONDS=3600`; estimate answers are not
cached. After the presentation, the minimum may be returned to zero and the
cache TTL shortened to reduce development cost and data staleness.

## Build and deploy a new image

Choose an immutable version tag and run from the repository root:

```bash
IMAGE_TAG=0.20.0

docker build \
  --platform linux/amd64 \
  --file services/estimator_api/Dockerfile \
  --tag spraytecaiacr9cf94090.azurecr.io/spraytec-business-api:${IMAGE_TAG} \
  .

docker run \
  --rm \
  --platform linux/amd64 \
  spraytecaiacr9cf94090.azurecr.io/spraytec-business-api:${IMAGE_TAG} \
  python -c 'from services.estimator_api.server import app; import shutil; print(app.version); print(shutil.which("soffice"))'

az acr login \
  --name spraytecaiacr9cf94090

docker push \
  spraytecaiacr9cf94090.azurecr.io/spraytec-business-api:${IMAGE_TAG}

az containerapp update \
  --name spraytec-business-api \
  --resource-group spraytec-ai-rg \
  --image spraytecaiacr9cf94090.azurecr.io/spraytec-business-api:${IMAGE_TAG} \
  --set-env-vars \
    ESTIMATOR_API_PUBLIC_BASE_URL=https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io
```

Do not use a mutable `latest` tag. Confirm the new revision is healthy before
removing an older image or revision.

## Rotate secrets

The Container App references unversioned Key Vault secret names including
`neon-database-url`, `estimator-api-key`, and `mapbox-access-token`. Do not put
secret values in shell
arguments, source code, documentation, or Azure Container App environment
values. Stream the value to Key Vault from a protected source instead.

The Container App currently exposes `mapbox-access-token` as
`MAPBOX_ACCESS_TOKEN`, which the roof context service accepts. The temporary
roof context currently uses the container's artifact directory, so keep the app
at one active replica unless that directory is moved to shared storage.

Optional SAM2 refinement requires `SAM2_SEGMENTATION_URL` to be an HTTPS
`/segment` endpoint reachable from the Container App. A Mac Studio service
bound to `127.0.0.1` is not reachable from Azure. Use a private authenticated
tunnel or private network path; do not expose the unauthenticated SAM2 port
directly to the internet. Store a separate `sam2-api-key` in Key Vault and
expose only its secret reference as `SAM2_API_KEY` to both services.

The SharePoint document fetch action also needs the scanner's existing Graph
application credentials exposed to the container as `MS_TENANT_ID`,
`MS_CLIENT_ID`, and `MS_CLIENT_SECRET`. Store all three in Key Vault and use
Container App secret references; do not copy their values into the image,
deployment command, action schema, logs, or documentation. The application
must already have read access to the Data site. No additional delegated-user
connection is used by this route.

After rotating `estimator-api-key`, update the Custom GPT action's API Key
authentication value to the same token and publish the GPT. Keep the auth type
as Bearer.

## Verification

```bash
API_BASE=https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io

curl \
  --fail-with-body \
  --silent \
  --show-error \
  "${API_BASE}/"

curl \
  --fail-with-body \
  --silent \
  --show-error \
  "${API_BASE}/health"

curl \
  --fail-with-body \
  --silent \
  --show-error \
  "${API_BASE}/privacy"

curl \
  --silent \
  --show-error \
  --output /dev/null \
  --write-out 'HTTP %{http_code}\n' \
  --header 'Content-Type: application/json' \
  --data '{"dataset":"sales_pipeline_by_stage"}' \
  "${API_BASE}/v1/reporting/chart-data"
```

The final request must return `401` without a bearer token. Run authenticated
smoke tests from a secret-aware script so the token cannot appear in terminal
history or process arguments.

An authenticated workbook smoke test must additionally assert that the returned
`download_url` starts with `https://`, downloads with HTTP 200, uses the XLSX
content type, and begins with the ZIP/XLSX `PK` signature.

Inspect the active revision and logs with:

```bash
az containerapp revision list \
  --name spraytec-business-api \
  --resource-group spraytec-ai-rg \
  --output table

az containerapp logs show \
  --name spraytec-business-api \
  --resource-group spraytec-ai-rg \
  --type console \
  --tail 100 \
  --format text
```

## Custom GPT action

Regenerate the checked-in action schema whenever the server contract or public
hostname changes:

```bash
python -m services.estimator_api.generate_openapi \
  --server-url https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io \
  --output services/estimator_api/openapi.json
```

The published GPT action was verified with GPT-5.6 Thinking. If the Custom GPT
recommendation selector does not expose that model, leave it unset and tell
testers to select GPT-5.6 Thinking for action-backed requests. Keep workbook
endpoints consequential and require explicit estimator confirmation. Do not
choose a blanket permanent approval for workbook-generating actions during
read-only testing.

For public link sharing, configure the action's Privacy Policy URL as:

```text
https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io/privacy
```

The privacy route is intentionally public, excluded from the action OpenAPI
contract, and does not disclose API credentials or internal data.
