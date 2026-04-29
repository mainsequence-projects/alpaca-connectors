from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.cli import main


if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "bars",
                "run",
                "--asset-category-unique-identifier",
                "HOLDINGS__IVV",
                "--frequency-id",
                "1d",
                "--feed",
                "sip",
                "--adjustment",
                "all",
            ]
        )
    )
