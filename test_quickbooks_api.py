from fastapi.testclient import TestClient

from services.estimator_api.generate_openapi import build_action_openapi
from services.estimator_api.server import app


client = TestClient(app)


def _response(schema_version: str):
    return {
        "schema_version": schema_version,
        "as_of": "2026-08-07T12:00:00+00:00",
        "connection": {"company_name": "Spray-Tec Inc.", "status": "connected"},
        "filters_applied": {},
        "headline_metrics": {"open_accounts_receivable": 250.0},
        "records": [],
        "source_tables": ["quickbooks_sales_transactions"],
        "data_freshness": {},
        "warnings": [],
    }


def test_quickbooks_assistant_routes_are_read_only_and_in_action_schema(monkeypatch):
    monkeypatch.delenv("ESTIMATOR_API_KEY", raising=False)
    monkeypatch.delenv("ESTIMATOR_API_REQUIRE_AUTH", raising=False)
    monkeypatch.setattr(
        "services.estimator_api.server.get_accounting_summary",
        lambda **kwargs: _response("spraytec.quickbooks.accounting_summary.v1"),
    )
    monkeypatch.setattr(
        "services.estimator_api.server.get_customer_context",
        lambda **kwargs: _response("spraytec.quickbooks.customer_context.v1"),
    )
    monkeypatch.setattr(
        "services.estimator_api.server.get_accounting_exceptions",
        lambda **kwargs: _response("spraytec.quickbooks.accounting_exceptions.v1"),
    )
    assert client.post("/v1/accounting/summary", json={}).status_code == 200
    assert client.post("/v1/accounting/customer-context", json={"customer_query": "ABC"}).status_code == 200
    assert client.post("/v1/accounting/exceptions", json={}).status_code == 200
    schema = build_action_openapi("https://api.example.test")
    for path in (
        "/v1/accounting/summary",
        "/v1/accounting/customer-context",
        "/v1/accounting/exceptions",
    ):
        assert path in schema["paths"]
        assert schema["paths"][path]["post"]["x-openai-isConsequential"] is False


def test_quickbooks_admin_routes_are_not_exposed_to_gpt_actions():
    schema = build_action_openapi("https://api.example.test")
    assert not any(path.startswith("/v1/integrations/quickbooks") for path in schema["paths"])


def test_quickbooks_assistant_routes_use_shared_auth(monkeypatch):
    monkeypatch.setenv("ESTIMATOR_API_KEY", "secret")
    assert client.post("/v1/accounting/summary", json={}).status_code == 401
