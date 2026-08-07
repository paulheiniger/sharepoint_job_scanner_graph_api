from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENTRYPOINT = ROOT / "scripts" / "azure_scanner_entrypoint.sh"


def scanner_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": "postgresql://scanner:placeholder@localhost/scanner",
        "MS_TENANT_ID": "tenant",
        "MS_CLIENT_ID": "client",
        "MS_CLIENT_SECRET": "secret",
        "SCANNER_PERSISTENT_ROOT": str(tmp_path / "persistent"),
    }


def run_entrypoint(tmp_path: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = scanner_env(tmp_path)
    env.update(overrides)
    return subprocess.run(
        ["bash", str(ENTRYPOINT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_entrypoint_refuses_unseeded_persistent_cache(tmp_path: Path) -> None:
    result = run_entrypoint(tmp_path, SCANNER_PREFLIGHT_ONLY="1")

    assert result.returncode == 78
    assert "has not been seeded" in result.stderr


def test_entrypoint_maps_all_mutable_paths_to_persistent_root(tmp_path: Path) -> None:
    manifest = (
        tmp_path
        / "persistent"
        / "cache"
        / "sharepoint"
        / "Data"
        / "2026_ROOFING"
        / ".jobscan_manifest.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    env_output = tmp_path / "entrypoint-env.txt"
    refresh_stub = tmp_path / "refresh-stub.sh"
    refresh_stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        f"printf '%s\\n' \"$CACHE_ROOT\" \"$TIMESHEET_CACHE_ROOT\" \"$OUTPUT_DIR\" \"$LOG_DIR\" \"$LOCK_DIR\" > {env_output}\n",
        encoding="utf-8",
    )
    refresh_stub.chmod(0o755)

    result = run_entrypoint(tmp_path, DAILY_REFRESH_SCRIPT=str(refresh_stub))

    assert result.returncode == 0, result.stderr
    persistent = tmp_path / "persistent"
    assert env_output.read_text(encoding="utf-8").splitlines() == [
        str(persistent / "cache" / "sharepoint"),
        str(persistent / "cache" / "office_timesheets" / "Data" / "Timesheets"),
        str(persistent / "output"),
        str(persistent / "output" / "refresh_logs"),
        str(persistent / "locks" / "daily_refresh.lock"),
    ]


def test_entrypoint_supports_image_preflight_without_seed(tmp_path: Path) -> None:
    result = run_entrypoint(
        tmp_path,
        SCANNER_REQUIRE_SEEDED_CACHE="0",
        SCANNER_PREFLIGHT_ONLY="1",
    )

    assert result.returncode == 0, result.stderr
    assert "preflight passed" in result.stdout.lower()
