from __future__ import annotations

import unittest

from etf_extraction.extractors.common import ExpandedSymbolUniverse
from etf_extraction.holdings_categories import (
    build_holdings_asset_category_plan,
    build_holdings_asset_category_unique_identifier,
    infer_holdings_component_provider,
    HoldingsAssetCategoryPlan,
)


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

        captured: dict[str, object] = {}

        def build_component_extractor_fn(provider: str, *, timeout: float):
            captured["provider"] = provider
            return StubExtractor()

        def resolve_existing_assets_by_ticker_fn(*, component_symbols):
            captured["symbols"] = component_symbols
            return {"AAPL": 1, "MSFT": 2}, [], []

        plan = build_holdings_asset_category_plan(
            etf_ticker="IVV",
            component_provider="ishares",
            include_non_tradable=True,
            timeout=12.0,
            build_component_extractor_fn=build_component_extractor_fn,
            resolve_existing_assets_by_ticker_fn=resolve_existing_assets_by_ticker_fn,
        )

        self.assertEqual(captured["provider"], "ishares")
        self.assertEqual(captured["symbols"], ["AAPL", "MSFT"])
        self.assertEqual(plan.component_symbols, ["AAPL", "MSFT"])
        self.assertEqual(plan.existing_asset_ids_by_symbol, {"AAPL": 1, "MSFT": 2})
        self.assertEqual(plan.category_unique_identifier, "HOLDINGS__IVV")

    def test_plan_has_blockers_for_missing_or_ambiguous_registered_symbols(self) -> None:
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
            component_symbols=["AAPL", "IVV", "FWONK"],
            existing_asset_ids_by_symbol={"AAPL": 1, "IVV": 2},
            missing_registered_symbols=["FWONK"],
            ambiguous_registered_symbols=[],
        )

        self.assertTrue(plan.has_blockers())


if __name__ == "__main__":
    unittest.main()
