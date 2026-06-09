from __future__ import annotations

from unittest.mock import Mock, patch

from jobscan.graph_client import GraphClient


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.text = str(self._body)
        self.closed = False

    def json(self) -> dict:
        return self._body

    def close(self) -> None:
        self.closed = True


def _client() -> GraphClient:
    return GraphClient(tenant_id="tenant", client_id="client", client_secret="secret")


def test_refreshes_before_expiry() -> None:
    token_results = [
        {"access_token": "old", "expires_in": 3600},
        {"access_token": "new", "expires_in": 3600},
    ]

    with patch("jobscan.graph_client.msal.ConfidentialClientApplication") as app_cls, patch("jobscan.graph_client.time.time") as now:
        app_cls.return_value.acquire_token_for_client.side_effect = token_results
        now.side_effect = [1000, 4301, 4301]
        client = _client()

        assert client.token() == "old"
        assert client.token() == "new"
        assert app_cls.return_value.acquire_token_for_client.call_count == 2


def test_retries_once_after_invalid_auth_token() -> None:
    token_results = [
        {"access_token": "old", "expires_in": 3600},
        {"access_token": "new", "expires_in": 3600},
    ]
    first = FakeResponse(401, {"error": {"code": "InvalidAuthenticationToken"}})
    second = FakeResponse(200, {"ok": True})
    request_mock = Mock(side_effect=[first, second])

    with patch("jobscan.graph_client.msal.ConfidentialClientApplication") as app_cls, patch("jobscan.graph_client.requests.request", request_mock):
        app_cls.return_value.acquire_token_for_client.side_effect = token_results
        client = _client()
        response = client.request("GET", "/sites/example")

    assert response is second
    assert first.closed is True
    assert request_mock.call_count == 2
    assert request_mock.call_args_list[0].kwargs["headers"]["Authorization"] == "Bearer old"
    assert request_mock.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer new"


if __name__ == "__main__":
    test_refreshes_before_expiry()
    test_retries_once_after_invalid_auth_token()
    print("graph client token refresh ok")
