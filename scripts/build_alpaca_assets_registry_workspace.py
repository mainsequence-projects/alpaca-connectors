from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from src.command_center import (
    ALPACA_ASSETS_REGISTRY_WORKSPACE_ID,
    build_alpaca_assets_registry_workspace_update,
)

DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "command_center"
    / "workspaces"
    / "alpaca_assets_registry.workspace.yaml"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the workspace update payload for the Alpaca Assets Registry "
            "AppComponent widget."
        )
    )
    parser.add_argument(
        "--fastapi-release-id",
        type=int,
        required=True,
        help="MainSequence FastAPI resource release id used by the AppComponent widget.",
    )
    parser.add_argument(
        "--workspace-id",
        type=int,
        default=ALPACA_ASSETS_REGISTRY_WORKSPACE_ID,
        help="Workspace id this file targets. Defaults to the Alpaca Assets Registry workspace.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path where the workspace update YAML should be written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_alpaca_assets_registry_workspace_update(
        fastapi_release_id=args.fastapi_release_id,
    )
    payload["id"] = args.workspace_id

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )

    print(f"Wrote workspace update file to {args.output}")
    print(f"Workspace id: {args.workspace_id}")
    print("Target widget: app-component")
    print(
        "Apply later with: "
        f"mainsequence cc workspace update {args.workspace_id} --file {args.output}"
    )


if __name__ == "__main__":
    main()
