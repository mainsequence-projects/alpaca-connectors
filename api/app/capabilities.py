"""FastAPI-facing catalog of the capabilities implemented by this repository.

The catalog describes existing behavior only. It is shared by API and presentation
surfaces so navigation does not become coupled to CLI commands, jobs, or examples.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

CapabilityAvailability = Literal["available", "partial"]


@dataclass(frozen=True, slots=True)
class Capability:
    """One user-facing repository capability and its current supported behavior."""

    key: str
    name: str
    purpose: str
    contents: tuple[str, ...]
    actions: tuple[str, ...]
    surfaces: tuple[str, ...]
    availability: CapabilityAvailability = "available"

    def summary(self) -> dict[str, object]:
        return asdict(self)


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="project_state",
        name="Project State",
        purpose="Expose API health and the configuration of existing capabilities.",
        contents=(
            "API health",
            "documented runtime prerequisites",
            "provider configuration",
            "supported capability catalog",
        ),
        actions=("inspect health", "inspect configured provider and universe metadata"),
        surfaces=("api",),
        availability="available",
    ),
    Capability(
        key="assets",
        name="Assets",
        purpose="Resolve and register canonical Alpaca-backed market instruments.",
        contents=(
            "Alpaca symbols",
            "OpenFIGI identity",
            "Main Sequence asset identity",
            "registration eligibility and blockers",
        ),
        actions=("discover", "resolve", "plan registration", "register"),
        surfaces=("cli", "api", "python"),
    ),
    Capability(
        key="universes",
        name="Universes",
        purpose="Build reusable collections of registered assets from supported inputs.",
        contents=(
            "user-maintained extraction sources",
            "provider-derived constituents",
            "asset registration coverage",
            "AssetCategory membership",
        ),
        actions=(
            "manage sources",
            "preview extraction",
            "validate membership",
            "sync",
            "inspect or remove materialized universes",
        ),
        surfaces=("cli", "api", "python"),
    ),
    Capability(
        key="market_data",
        name="Market Data",
        purpose="Publish and update Alpaca OHLCV observations for registered assets.",
        contents=("bar datasets", "cadence", "feed", "adjustment", "covered assets"),
        actions=("list datasets", "query prices", "plan update", "run update"),
        surfaces=("cli", "api", "python"),
    ),
    Capability(
        key="accounts",
        name="Accounts",
        purpose="Represent and refresh an Alpaca brokerage account in ms-markets.",
        contents=(
            "account identity",
            "paper or live environment",
            "account status",
            "balances and buying power",
            "Alpaca account details",
        ),
        actions=("plan registration", "register", "list", "update", "refresh", "remove"),
        surfaces=("cli", "api", "python"),
    ),
    Capability(
        key="holdings",
        name="Holdings",
        purpose="Represent asset exposure from brokerage positions or fund composition.",
        contents=(
            "dated account positions",
            "cash position",
            "externally extracted symbols and weights",
            "registered asset references",
        ),
        actions=("capture account snapshot", "list", "inspect immutable snapshot"),
        surfaces=("cli", "api", "python"),
    ),
    Capability(
        key="portfolios",
        name="Portfolios",
        purpose="Construct analytical portfolios from resolved weights and market data.",
        contents=(
            "portfolio identity",
            "component weights",
            "market-data binding",
            "interpolated prices",
            "signals and calculation results",
        ),
        actions=("plan construction", "build", "run calculation"),
        surfaces=("python",),
        availability="partial",
    ),
    Capability(
        key="operations",
        name="Operations",
        purpose="Expose plans, executions, validation failures, and runtime outcomes.",
        contents=(
            "dry-run plans",
            "manual executions",
            "validation blockers",
            "runtime results",
            "on-demand bars JobRuns",
        ),
        actions=("inspect plan", "submit supported action", "inspect JobRun"),
        surfaces=("cli", "api", "python"),
        availability="partial",
    ),
)


def capability_summaries() -> list[dict[str, object]]:
    """Return JSON-safe summaries in control-surface navigation order."""
    return [capability.summary() for capability in CAPABILITIES]
