# Spray-Tec Estimator API

This service exposes the estimator's curated evidence and deterministic tools to
Microsoft 365 Copilot agents. The service does not call OpenAI when building a
context package. Copilot supplies the conversational model and the API supplies
company evidence.

## Current endpoint

`POST /v1/estimating/context`

The request should contain the job facts Copilot extracted from the current
conversation:

```json
{
  "raw_notes": "30x40 metal building with walls and roof deck included.",
  "template_type": "insulation",
  "scope": {
    "building_type": "metal building",
    "building_footprint_length_ft": 40,
    "building_footprint_width_ft": 30,
    "wall_height_ft": 9,
    "outside_walls_included": true,
    "ceiling_included": true,
    "site_address": "314 E Aberdeen Drive, Trenton, OH"
  },
  "reference_job_ids": []
}
```

The response includes:

- route mileage;
- workbook decision menu and formula requirements;
- historical foam-yield evidence;
- current pricing candidates;
- relevant approved memories;
- complete comparable manifests and decision evidence;
- the strongest historical scope pattern and validated relationships; and
- relevant product guidance.

## Local run

Install the service dependencies in the repository environment:

```bash
python -m pip install -r services/estimator_api/requirements.txt
```

Set `NEON_DATABASE_URL` or `DATABASE_URL`, then run:

```bash
python -m services.estimator_api.server
```

The service listens on `http://127.0.0.1:8770` by default:

```bash
curl http://127.0.0.1:8770/health
```

FastAPI publishes the OpenAPI contract at:

```text
http://127.0.0.1:8770/openapi.json
```

## Authentication

Local development does not require authentication. In Azure, enable Microsoft
Entra authentication through App Service Authentication ("Easy Auth"), choose
**Require authentication**, and set:

```text
ESTIMATOR_API_REQUIRE_AUTH=true
```

When enabled, the service requires an authenticated principal header supplied
by Azure. This header check is defense in depth, not standalone authentication:
clients must not be able to bypass Easy Auth and reach the application directly.
Do not expose the service publicly with authentication disabled.

For a Copilot action, register the API as a single-tenant Microsoft Entra
resource, expose a delegated read scope, and configure the plugin or custom
connector to request that scope. Import the deployed `/openapi.json` contract
and select only the `getEstimatorContext` operation for Phase 1.

## Planned operations

After the read-only context endpoint is validated:

- `POST /v1/estimating/validate`
- `POST /v1/estimating/workbook`
- `POST /v1/estimating/feedback`

Validation and workbook generation should reuse the existing workbench and
session logic. Feedback must create pending memory candidates rather than
automatically approving institutional memory.
