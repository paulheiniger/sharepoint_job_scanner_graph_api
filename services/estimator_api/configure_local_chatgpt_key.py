from __future__ import annotations

import argparse
import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = PROJECT_ROOT / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure the private API key used by the local ChatGPT action.",
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Replace an existing key without printing the new secret.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    values = dotenv_values(DOTENV_PATH)
    if values.get("ESTIMATOR_API_KEY") and not args.rotate:
        print("Existing ESTIMATOR_API_KEY preserved.")
    else:
        set_key(
            DOTENV_PATH,
            "ESTIMATOR_API_KEY",
            secrets.token_hex(32),
            quote_mode="never",
        )
        print(
            "Rotated ESTIMATOR_API_KEY in the repository .env file."
            if args.rotate
            else "Created ESTIMATOR_API_KEY in the repository .env file."
        )
    if "ESTIMATOR_API_REQUIRE_AUTH" not in values:
        set_key(
            DOTENV_PATH,
            "ESTIMATOR_API_REQUIRE_AUTH",
            "false",
            quote_mode="never",
        )
        print("Configured API-key-only local testing.")
    DOTENV_PATH.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
