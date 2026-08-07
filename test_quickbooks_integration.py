from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from jobscan.quickbooks.client import OAuthTokens, QuickBooksClient
from jobscan.quickbooks.oauth import (
    QuickBooksCompanyMismatchError,
    complete_admin_authorization,
    create_admin_authorization,
)
from jobscan.quickbooks.repository import (
    connections,
    ensure_tables,
    get_connection,
    transactions,
    utc_now,
)
from jobscan.quickbooks.security import QuickBooksStateError, build_oauth_state, verify_oauth_state
from jobscan.quickbooks.service import get_accounting_summary, get_customer_context
from jobscan.quickbooks.sync import sync_quickbooks


@pytest.fixture
def engine():
    value = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_tables(value)
    return value


@pytest.fixture(autouse=True)
def quickbooks_env(monkeypatch):
    monkeypatch.setenv("QUICKBOOKS_CLIENT_ID", "test-client")
    monkeypatch.setenv("QUICKBOOKS_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("QUICKBOOKS_REDIRECT_URI", "https://api.example.test/v1/integrations/quickbooks/callback")
    monkeypatch.setenv("QUICKBOOKS_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("QUICKBOOKS_OAUTH_STATE_SECRET", "test-state-secret-with-enough-entropy")
    monkeypatch.setenv("QUICKBOOKS_EXPECTED_COMPANY_NAME", "Spray-Tec Inc.")
    monkeypatch.setenv("QUICKBOOKS_ENVIRONMENT", "sandbox")


class FakeOAuthClient:
    def __init__(self, company_name: str = "Spray-Tec Inc."):
        self.realm_id = ""
        self.access_token = ""
        self.company_name = company_name

    def exchange_code(self, code: str) -> OAuthTokens:
        assert code == "authorization-code"
        return OAuthTokens("access-plain", "refresh-plain", 3600, 8_726_400, "bearer")

    def company_info(self):
        return {"CompanyName": self.company_name}


def test_oauth_state_is_signed_and_expires():
    state = build_oauth_state(ttl_seconds=60)
    payload = verify_oauth_state(state)
    assert payload["nonce"]
    with pytest.raises(QuickBooksStateError):
        verify_oauth_state(state + "tampered")
    with pytest.raises(QuickBooksStateError):
        verify_oauth_state(state, now=payload["exp"] + 1)


def test_admin_authorization_is_one_time_and_tokens_are_encrypted(engine):
    url = create_admin_authorization(engine)
    state = parse_qs(urlsplit(url).query)["state"][0]
    result = complete_admin_authorization(
        engine,
        state=state,
        code="authorization-code",
        realm_id="realm-123",
        client=FakeOAuthClient(),
    )
    assert result["company_name"] == "Spray-Tec Inc."
    with engine.connect() as db:
        stored = dict(db.execute(select(connections)).mappings().one())
    assert "access-plain" not in stored["access_token_encrypted"]
    assert "refresh-plain" not in stored["refresh_token_encrypted"]
    assert get_connection(engine)["refresh_token"] == "refresh-plain"
    with pytest.raises(QuickBooksStateError):
        complete_admin_authorization(
            engine,
            state=state,
            code="authorization-code",
            realm_id="realm-123",
            client=FakeOAuthClient(),
        )


def test_wrong_company_is_rejected(engine):
    url = create_admin_authorization(engine)
    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(QuickBooksCompanyMismatchError):
        complete_admin_authorization(
            engine,
            state=state,
            code="authorization-code",
            realm_id="wrong-realm",
            client=FakeOAuthClient("Another Company"),
        )


class FakeSyncClient:
    access_token = "access-plain"

    def __init__(self):
        self.updated_after = []

    def query_entities(self, entity: str, *, updated_after: str = ""):
        self.updated_after.append((entity, updated_after))
        records = {
            "Customer": [{
                "Id": "10", "SyncToken": "1", "DisplayName": "ABC School",
                "CompanyName": "ABC School", "Active": True, "Balance": 250.0,
                "PrimaryEmailAddr": {"Address": "billing@example.test"},
                "MetaData": {"LastUpdatedTime": "2026-08-07T12:00:00Z"},
            }],
            "Estimate": [{
                "Id": "20", "SyncToken": "0", "TxnDate": "2026-08-01", "DocNumber": "E-20",
                "CustomerRef": {"value": "10", "name": "ABC School"}, "TotalAmt": 5000,
                "MetaData": {"LastUpdatedTime": "2026-08-07T12:01:00Z"},
            }],
            "Invoice": [{
                "Id": "30", "SyncToken": "2", "TxnDate": "2026-07-01", "DueDate": "2026-07-31",
                "DocNumber": "I-30", "CustomerRef": {"value": "10", "name": "ABC School"},
                "TotalAmt": 5000, "Balance": 250, "MetaData": {"LastUpdatedTime": "2026-08-07T12:02:00Z"},
            }],
            "Payment": [{
                "Id": "40", "SyncToken": "0", "TxnDate": "2026-08-02",
                "CustomerRef": {"value": "10", "name": "ABC School"}, "TotalAmt": 4750,
                "UnappliedAmt": 0, "MetaData": {"LastUpdatedTime": "2026-08-07T12:03:00Z"},
            }],
            "CreditMemo": [],
        }
        yield from records[entity]


class FakeRefreshClient(FakeSyncClient):
    def refresh(self, refresh_token: str):
        assert refresh_token == "refresh-plain"
        return OAuthTokens("new-access", "new-refresh", 3600, 8_726_400, "bearer")


def _authorized_connection(engine):
    from jobscan.quickbooks.repository import store_connection

    now = utc_now()
    store_connection(
        engine,
        realm_id="realm-123",
        company_name="Spray-Tec Inc.",
        environment="sandbox",
        access_token="access-plain",
        refresh_token="refresh-plain",
        access_token_expires_at=now + timedelta(hours=1),
        refresh_token_expires_at=now + timedelta(days=100),
        scope="com.intuit.quickbooks.accounting",
    )


def test_sync_is_idempotent_and_services_read_normalized_data(engine):
    _authorized_connection(engine)
    client = FakeSyncClient()
    first = sync_quickbooks(engine, client=client)
    second = sync_quickbooks(engine, client=client)
    assert first["records"]["Invoice"] == 1
    with engine.connect() as db:
        assert len(db.execute(select(transactions)).all()) == 2
    assert all(updated_after == "" for _, updated_after in client.updated_after[:5])
    assert any(updated_after for _, updated_after in client.updated_after[5:])
    summary = get_accounting_summary(engine=engine)
    assert summary["headline_metrics"]["open_accounts_receivable"] == 250.0
    assert summary["headline_metrics"]["overdue_invoice_count"] == 1
    context = get_customer_context(engine=engine, customer_query="ABC")
    assert context["headline_metrics"]["matched_customers"] == 1
    assert {row["entity_type"] for row in context["records"]} == {"Estimate", "Invoice"}


def test_refresh_token_rotation_persists_only_the_new_encrypted_tokens(engine):
    _authorized_connection(engine)
    with engine.begin() as db:
        db.execute(
            connections.update().values(access_token_expires_at=utc_now() - timedelta(minutes=1))
        )
    sync_quickbooks(engine, client=FakeRefreshClient())
    decrypted = get_connection(engine)
    assert decrypted["access_token"] == "new-access"
    assert decrypted["refresh_token"] == "new-refresh"
    with engine.connect() as db:
        raw = dict(db.execute(select(connections)).mappings().one())
    assert "new-access" not in raw["access_token_encrypted"]
    assert "new-refresh" not in raw["refresh_token_encrypted"]


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class RetrySession:
    def __init__(self):
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return FakeResponse(429, headers={"Retry-After": "2"})
        return FakeResponse(200, {"QueryResponse": {"Invoice": [{"Id": "1"}]}})


def test_client_honors_retry_after_and_paginates():
    sleeps = []
    session = RetrySession()
    client = QuickBooksClient(
        realm_id="realm", access_token="token", session=session, sleep=sleeps.append
    )
    rows = list(client.query_entities("Invoice", page_size=1000))
    assert rows == [{"Id": "1"}]
    assert sleeps == [2.0]
