"""Universe-backed Alpaca ETF holdings signal.

The signal producer is connector-owned because executing it needs both the durable
``AssetUniverse`` configuration and a registered Alpaca account.  Its output uses the
canonical ms-markets ``SignalWeightsStorage`` table; this module does not define another
storage model.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
from msm_portfolios.configuration import PortfolioConfigBaseModel
from msm_portfolios.data_nodes import SignalWeights
from msm_portfolios.data_nodes.constants import ASSET_IDENTIFIER
from pydantic import field_validator


class AlpacaETFHoldingsSignalConfig(PortfolioConfigBaseModel):
    """Runtime producer configuration for one Universe-backed signal.

    ``universe_uid`` is business identity. ``account_uid`` resolves Alpaca Secret names
    and registers assets at execution time. The owning signal Job configuration persists
    both values, while ms-markets deliberately excludes ``signal_configuration`` from the
    canonical ``SignalWeights`` update hash. ``account_uid`` is additionally excluded from
    the final signal UID.
    """

    universe_uid: str
    account_uid: str

    @field_validator("universe_uid", "account_uid")
    @classmethod
    def _require_uid(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("UID values must not be empty.")
        return normalized


class AlpacaETFHoldingsSignal(SignalWeights):
    """Publish each successful Universe extraction as one signal observation.

    Every update reuses the connector's extraction, bulk asset registration, and bulk
    category-membership materialization workflow.  The observation timestamp records when
    the provider result was seen; it does not claim that the weights became economically
    effective at that exact instant.
    """

    SIGNAL_VALIDITY_DAYS = 90

    @property
    def alpaca_etf_config(self) -> AlpacaETFHoldingsSignalConfig:
        config = self.signal_configuration
        if not isinstance(config, AlpacaETFHoldingsSignalConfig):
            raise TypeError(
                "AlpacaETFHoldingsSignal requires AlpacaETFHoldingsSignalConfig as "
                "signal_configuration."
            )
        return config

    def _signal_uid_payload(self) -> dict[str, Any]:
        """Hash only Universe business identity into the final signal UID."""
        payload = super()._signal_uid_payload()
        payload["config"] = {"universe_uid": self.alpaca_etf_config.universe_uid}
        return payload

    def maximum_forward_fill(self) -> dt.timedelta:
        return dt.timedelta(days=self.SIGNAL_VALIDITY_DAYS)

    def dependencies(self) -> dict[str, Any]:
        return {}

    def get_asset_list(self) -> list[str] | None:
        """Return current/prepared Universe scope without changing signal identity."""
        prepared_plan = getattr(self, "_prepared_universe_plan", None)
        if prepared_plan is not None:
            from src.universes.services import asset_identifiers_for_universe_plan

            return asset_identifiers_for_universe_plan(prepared_plan)

        last_materialization = getattr(self, "_last_universe_materialization", None)
        if last_materialization is not None:
            return sorted(set(last_materialization.asset_identifiers_by_symbol.values()))

        from src.assets.resolution import asset_unique_identifiers_for_category
        from src.universes.materialized import require_asset_universe_links

        _universe, _source, category = require_asset_universe_links(
            self.alpaca_etf_config.universe_uid
        )
        identifiers = asset_unique_identifiers_for_category(category.unique_identifier)
        return identifiers or None

    def set_prepared_universe_plan(self, plan: Any) -> AlpacaETFHoldingsSignal:
        """Attach a one-shot read-only plan without adding it to serialized config."""
        if str(plan.universe_uid) != self.alpaca_etf_config.universe_uid:
            raise ValueError("Prepared Universe plan does not match signal universe_uid.")
        if str(plan.account_uid) != self.alpaca_etf_config.account_uid:
            raise ValueError("Prepared Universe plan does not match signal account_uid.")
        self._prepared_universe_plan = plan
        return self

    @property
    def last_universe_materialization(self) -> Any | None:
        return getattr(self, "_last_universe_materialization", None)

    def get_explanation(self) -> str:
        return (
            f"Observes Asset Universe {self.alpaca_etf_config.universe_uid} and publishes "
            "its extracted component weights as canonical signal observations. The observation "
            "timestamp does not guarantee the weights' exact economic effective time."
        )

    def _calculate_signal_weights(self) -> pd.DataFrame:
        from src.universes.services import materialize_asset_universe

        plan = getattr(self, "_prepared_universe_plan", None)
        if hasattr(self, "_prepared_universe_plan"):
            del self._prepared_universe_plan
        materialization = materialize_asset_universe(
            self.alpaca_etf_config.universe_uid,
            account_uid=self.alpaca_etf_config.account_uid,
            plan=plan,
        )
        self._last_universe_materialization = materialization

        weights_by_identifier: dict[str, float] = {}
        for symbol, provider_weight in materialization.component_weights_by_symbol.items():
            identifier = materialization.asset_identifiers_by_symbol[symbol]
            weights_by_identifier[identifier] = (
                weights_by_identifier.get(identifier, 0.0) + float(provider_weight) / 100.0
            )

        total_weight = sum(weights_by_identifier.values())
        if total_weight <= 0:
            raise RuntimeError(
                f"Universe {self.alpaca_etf_config.universe_uid} extracted weights that sum "
                f"to {total_weight}; refusing to publish an invalid signal observation."
            )
        normalized_weights = {
            identifier: weight / total_weight
            for identifier, weight in weights_by_identifier.items()
        }
        return pd.DataFrame(
            {"signal_weight": list(normalized_weights.values())},
            index=pd.MultiIndex.from_arrays(
                [
                    [materialization.observed_at] * len(normalized_weights),
                    list(normalized_weights),
                ],
                names=["time_index", ASSET_IDENTIFIER],
            ),
        ).sort_index()


def build_alpaca_etf_holdings_signal(
    *,
    universe_uid: str,
    account_uid: str,
    prepared_plan: Any | None = None,
) -> AlpacaETFHoldingsSignal:
    """Construct the serializable signal and optionally attach one prepared execution plan."""
    signal = AlpacaETFHoldingsSignal.from_signal_configuration(
        AlpacaETFHoldingsSignalConfig(
            universe_uid=universe_uid,
            account_uid=account_uid,
        )
    )
    if prepared_plan is not None:
        signal.set_prepared_universe_plan(prepared_plan)
    return signal


__all__ = [
    "AlpacaETFHoldingsSignal",
    "AlpacaETFHoldingsSignalConfig",
    "build_alpaca_etf_holdings_signal",
]
