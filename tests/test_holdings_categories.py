from __future__ import annotations

import unittest

from etf_extraction.extractors.common import ExpandedSymbolUniverse
from src.holdings_categories import (
    build_holdings_asset_category_plan,
    build_holdings_asset_category_unique_identifier,
    infer_holdings_component_provider,
    HoldingsAssetCategoryPlan,
)
from src.assets.alpaca_us_equities import AlpacaUsEquity


class HoldingsCategoryTests(unittest.TestCase):
    def test_build_holdings_asset_category_unique_identifier(self) -> None:
        self.assertEqual(
            build_holdings_asset_category_unique_identifier("ivv"),
            "HOLDINGS__IVV",
        )

    def test_infer_holdings_component_provider(self) -> None:
        self.assertEqual(infer_holdings_component_provider("spy"), "state_street")
        self.assertEqual(infer_holdings_component_provider("ivv"), "ishares")

    def test_build_holdings_asset_category_plan_uses_components_only(self) -> None:
        class StubExtractor:
            def expand_seed_symbols(self, seed_symbols):
                return ExpandedSymbolUniverse(
                    seed_symbols=["IVV"],
                    expanded_symbols=["AAPL", "IVV", "MSFT"],
                    component_symbols_by_seed={"IVV": ["AAPL", "MSFT"]},
                    unsupported_seed_symbols=[],
                )

        class StubResolution:
            def __init__(self):
                self.existing_assets_by_symbol = {"AAPL": 1, "MSFT": 2}
                self.missing_matches = []

        class StubRegistrationPlan:
            alpaca_assets = [
                AlpacaUsEquity(
                    symbol="AAPL",
                    name="Apple Inc.",
                    exchange="NASDAQ",
                    asset_class="us_equity",
                    status="active",
                    tradable=True,
                    marginable=True,
                    shortable=True,
                    easy_to_borrow=True,
                    fractionable=True,
                ),
                AlpacaUsEquity(
                    symbol="MSFT",
                    name="Microsoft Corp.",
                    exchange="NASDAQ",
                    asset_class="us_equity",
                    status="active",
                    tradable=True,
                    marginable=True,
                    shortable=True,
                    easy_to_borrow=True,
                    fractionable=True,
                ),
            ]
            unresolved_symbols = []
            missing_symbols_from_alpaca = []
            requested_symbol_aliases = {}
            warnings_by_symbol = {}

        captured: dict[str, object] = {}

        def build_component_extractor_fn(provider: str, *, timeout: float):
            captured["provider"] = provider
            return StubExtractor()

        def build_registration_plan_fn(*, symbols, include_non_tradable, timeout):
            captured["symbols"] = symbols
            captured["include_non_tradable"] = include_non_tradable
            return StubRegistrationPlan()

        def resolve_registration_plan_fn(plan, *, timeout):
            captured["resolved_plan"] = plan
            return StubResolution()

        plan = build_holdings_asset_category_plan(
            etf_ticker="IVV",
            component_provider="ishares",
            include_non_tradable=True,
            timeout=12.0,
            build_component_extractor_fn=build_component_extractor_fn,
            build_registration_plan_fn=build_registration_plan_fn,
            resolve_registration_plan_fn=resolve_registration_plan_fn,
        )

        self.assertEqual(captured["provider"], "ishares")
        self.assertEqual(captured["symbols"], ["AAPL", "MSFT"])
        self.assertEqual(captured["include_non_tradable"], True)
        self.assertEqual(plan.component_symbols, ["AAPL", "MSFT"])
        self.assertEqual(plan.existing_asset_ids_by_symbol, {"AAPL": 1, "MSFT": 2})
        self.assertEqual(plan.category_unique_identifier, "HOLDINGS__IVV")

    def test_plan_has_blockers_ignores_unresolved_symbols(self) -> None:
        plan = HoldingsAssetCategoryPlan(
            etf_ticker="IVV",
            provider="ishares",
            category_unique_identifier="HOLDINGS__IVV",
            expansion=ExpandedSymbolUniverse(
                seed_symbols=["IVV"],
                expanded_symbols=["AAPL", "IVV", "FWONK"],
                component_symbols_by_seed={"IVV": ["AAPL", "IVV", "FWONK"]},
                unsupported_seed_symbols=[],
            ),
            registration_plan=type(
                "RegistrationPlan",
                (),
                {
                    "unresolved_symbols": ["FWONK"],
                    "missing_symbols_from_alpaca": [],
                    "warnings_by_symbol": {"FWONK": "No FIGI match"},
                },
            )(),
            registration_resolution=type("RegistrationResolution", (), {"missing_matches": []})(),
            component_symbols=["AAPL", "IVV", "FWONK"],
            existing_asset_ids_by_symbol={"AAPL": 1, "IVV": 2},
            missing_registered_symbols=[],
        )

        self.assertFalse(plan.has_blockers())


if __name__ == "__main__":
    unittest.main()
