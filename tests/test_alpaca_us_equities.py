from __future__ import annotations

import unittest

from src.assets.alpaca_us_equities import (
    AlpacaEquityClassificationPass,
    AlpacaUsEquity,
    OpenFigiMatch,
    _resolve_requested_symbols_to_alpaca_assets,
    classify_alpaca_us_equities,
    resolve_alpaca_us_equity_registration_plan,
)


class AlpacaAssetRegistrationTests(unittest.TestCase):
    def test_requested_symbols_resolve_share_class_aliases(self) -> None:
        resolved_assets, missing_symbols, requested_symbol_aliases = (
            _resolve_requested_symbols_to_alpaca_assets(
                alpaca_assets=[
                    AlpacaUsEquity(
                        symbol="BRK.B",
                        name="Berkshire Hathaway Inc. Class B",
                        exchange="NYSE",
                        asset_class="us_equity",
                        status="active",
                        tradable=True,
                        marginable=True,
                        shortable=True,
                        easy_to_borrow=True,
                        fractionable=True,
                    ),
                    AlpacaUsEquity(
                        symbol="BF.B",
                        name="Brown-Forman Corp. Class B",
                        exchange="NYSE",
                        asset_class="us_equity",
                        status="active",
                        tradable=True,
                        marginable=True,
                        shortable=True,
                        easy_to_borrow=True,
                        fractionable=True,
                    ),
                ],
                requested_symbols=["BRKB", "BFB", "AAPL"],
            )
        )

        self.assertEqual([asset.symbol for asset in resolved_assets], ["BF.B", "BRK.B"])
        self.assertEqual(missing_symbols, ["AAPL"])
        self.assertEqual(
            requested_symbol_aliases,
            {"BFB": "BF.B", "BRKB": "BRK.B"},
        )

    def test_classification_uses_stock_then_etp_then_reit_passes(self) -> None:
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
                symbol="SPY",
                name="SPDR S&P 500 ETF Trust",
                exchange="ARCA",
                asset_class="us_equity",
                status="active",
                tradable=True,
                marginable=True,
                shortable=True,
                easy_to_borrow=True,
                fractionable=True,
            ),
            AlpacaUsEquity(
                symbol="VNQ",
                name="Vanguard Real Estate ETF",
                exchange="ARCA",
                asset_class="us_equity",
                status="active",
                tradable=True,
                marginable=True,
                shortable=True,
                easy_to_borrow=True,
                fractionable=True,
            ),
            AlpacaUsEquity(
                symbol="UNKNOWN",
                name="Unknown",
                exchange="NYSE",
                asset_class="us_equity",
                status="active",
                tradable=True,
                marginable=False,
                shortable=False,
                easy_to_borrow=False,
                fractionable=False,
            ),
        ]

        def fake_query_openfigi_fn(**kwargs):
            security_type = kwargs["security_type"]
            tickers = kwargs["tickers"]
            pass_name = kwargs["classification_pass_name"]
            matches = {}
            warnings = {}
            for ticker in tickers:
                if security_type == "Common Stock" and ticker == "AAPL":
                    matches[ticker] = OpenFigiMatch(
                        symbol=ticker,
                        figi="FIGI_AAPL",
                        ticker=ticker,
                        exchange_code="US",
                        security_type=security_type,
                        security_type_2="Common Stock",
                        security_market_sector="Equity",
                        classification_pass_name=pass_name,
                    )
                elif security_type == "ETP" and ticker == "SPY":
                    matches[ticker] = OpenFigiMatch(
                        symbol=ticker,
                        figi="FIGI_SPY",
                        ticker=ticker,
                        exchange_code="US",
                        security_type=security_type,
                        security_type_2="Mutual Fund",
                        security_market_sector="Equity",
                        classification_pass_name=pass_name,
                    )
                elif security_type == "REIT" and ticker == "VNQ":
                    matches[ticker] = OpenFigiMatch(
                        symbol=ticker,
                        figi="FIGI_VNQ",
                        ticker=ticker,
                        exchange_code="US",
                        security_type=security_type,
                        security_type_2="REIT",
                        security_market_sector="Equity",
                        classification_pass_name=pass_name,
                    )
                else:
                    warnings[ticker] = "No identifier found."
            return matches, warnings

        plan = classify_alpaca_us_equities(
            alpaca_assets,
            classification_passes=(
                AlpacaEquityClassificationPass(
                    name="common_stock",
                    security_type="Common Stock",
                ),
                AlpacaEquityClassificationPass(
                    name="etp",
                    security_type="ETP",
                ),
                AlpacaEquityClassificationPass(
                    name="reit",
                    security_type="REIT",
                ),
            ),
            query_openfigi_fn=fake_query_openfigi_fn,
        )

        self.assertEqual(plan.matches_by_symbol["AAPL"].security_type, "Common Stock")
        self.assertEqual(plan.matches_by_symbol["SPY"].security_type, "ETP")
        self.assertEqual(plan.matches_by_symbol["VNQ"].security_type, "REIT")
        self.assertEqual(plan.unresolved_symbols, ["UNKNOWN"])
        self.assertEqual(plan.warnings_by_symbol, {"UNKNOWN": "No identifier found."})

    def test_resolution_only_marks_missing_figis_for_registration(self) -> None:
        plan = classify_alpaca_us_equities(
            [
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
                    symbol="SPY",
                    name="SPDR S&P 500 ETF Trust",
                    exchange="ARCA",
                    asset_class="us_equity",
                    status="active",
                    tradable=True,
                    marginable=True,
                    shortable=True,
                    easy_to_borrow=True,
                    fractionable=True,
                ),
            ],
            classification_passes=(
                AlpacaEquityClassificationPass(
                    name="common_stock",
                    security_type="Common Stock",
                ),
                AlpacaEquityClassificationPass(
                    name="etp",
                    security_type="ETP",
                ),
            ),
            query_openfigi_fn=lambda **_: (
                {
                    "AAPL": OpenFigiMatch(
                        symbol="AAPL",
                        figi="FIGI_AAPL",
                        ticker="AAPL",
                        exchange_code="US",
                        security_type="Common Stock",
                        security_type_2="Common Stock",
                        security_market_sector="Equity",
                        classification_pass_name="common_stock",
                    ),
                    "SPY": OpenFigiMatch(
                        symbol="SPY",
                        figi="FIGI_SPY",
                        ticker="SPY",
                        exchange_code="US",
                        security_type="ETP",
                        security_type_2="Mutual Fund",
                        security_market_sector="Equity",
                        classification_pass_name="etp",
                    ),
                },
                {},
            ),
        )

        class ExistingAsset:
            def __init__(self, asset_id: int):
                self.id = asset_id

        resolution = resolve_alpaca_us_equity_registration_plan(
            plan,
            query_existing_assets_fn=lambda figis, timeout=None: {
                "FIGI_AAPL": ExistingAsset(101),
            },
        )

        self.assertEqual(resolution.existing_assets_by_symbol, {"AAPL": 101})
        self.assertEqual([match.symbol for match in resolution.missing_matches], ["SPY"])


if __name__ == "__main__":
    unittest.main()
