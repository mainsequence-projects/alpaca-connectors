"""Main Sequence FastAPI resource entry point.

The deployed resource is discovered at this path, so it stays here while the application lives in
the installed `alpaca_connectors.api` package. This file is not part of the wheel.
"""

from alpaca_connectors.api.app.main import app

__all__ = ["app"]
