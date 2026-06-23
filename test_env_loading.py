from __future__ import annotations

import os

from jobscan.env import GRAPH_ENV_KEYS, graph_env_status, load_project_env


def test_load_project_env_loads_graph_keys_from_dotenv(tmp_path, monkeypatch) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "MS_TENANT_ID=test-tenant\n"
        "MS_CLIENT_ID=test-client\n"
        "MS_CLIENT_SECRET=test-secret\n",
        encoding="utf-8",
    )
    for key in GRAPH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    assert load_project_env(dotenv_path) is True

    assert os.environ["MS_TENANT_ID"] == "test-tenant"
    assert os.environ["MS_CLIENT_ID"] == "test-client"
    assert os.environ["MS_CLIENT_SECRET"] == "test-secret"


def test_graph_env_status_hides_secret_values() -> None:
    status = graph_env_status(
        {
            "MS_TENANT_ID": "tenant-value",
            "MS_CLIENT_ID": "client-value",
            "MS_CLIENT_SECRET": "super-secret-value",
        }
    )

    assert status == {
        "MS_TENANT_ID": "FOUND",
        "MS_CLIENT_ID": "FOUND",
        "MS_CLIENT_SECRET": "FOUND",
    }
    assert "super-secret-value" not in str(status)


def test_graph_env_status_reports_missing_values() -> None:
    status = graph_env_status({"MS_TENANT_ID": "tenant-value"})

    assert status["MS_TENANT_ID"] == "FOUND"
    assert status["MS_CLIENT_ID"] == "MISSING"
    assert status["MS_CLIENT_SECRET"] == "MISSING"
