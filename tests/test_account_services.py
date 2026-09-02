from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.account.services import (
    build_account_balance_values,
    build_account_detail_values,
    build_holdings_rows,
    register_alpaca_account,
)


def _stub_account() -> SimpleNamespace:
    return SimpleNamespace(
        id="aid-uuid",
        account_number="010203ABCD",
        status=SimpleNamespace(value="ACTIVE"),
        crypto_status=None,
        currency="USD",
        multiplier="4",
        cash="1000.50",
        equity="2500.00",
        pattern_day_trader=False,
        shorting_enabled=True,
        trading_blocked=False,
        transfers_blocked=False,
        account_blocked=False,
        trade_suspended_by_user=False,
        options_approved_level=2,
        options_trading_level=2,
        created_at=None,
    )


def _stub_positions() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            symbol="AAPL",
            asset_class=SimpleNamespace(value="us_equity"),
            side=SimpleNamespace(value="long"),
            qty="10",
            avg_entry_price="150",
            market_value="1600",
            cost_basis="1500",
            unrealized_pl="100",
            current_price="160",
            exchange=SimpleNamespace(value="NASDAQ"),
            asset_id="aid-1",
        ),
        SimpleNamespace(
            symbol="BTCUSD",
            asset_class=SimpleNamespace(value="crypto"),
            side=SimpleNamespace(value="long"),
            qty="0.5",
            asset_id="aid-2",
        ),
    ]


class StubTradingClient:
    def __init__(self, account, positions, raw):
        self._account = account
        self._positions = positions
        self._raw = raw

    def get_account(self):
        return self._account

    def get_account_configurations(self):
        return None

    def get_all_positions(self):
        return self._positions

    def get(self, path):
        return self._raw if path == "/account" else {}


class _FakeNode:
    """Stand-in for AccountHoldings / AlpacaAccountBalancesNode (avoids backend storage binding)."""

    frames: list = []

    def __init__(self, config=None):
        pass

    @classmethod
    def default_config(cls):
        return None

    def set_frame(self, frame):
        type(self).frames.append(frame)
        return self

    def run(self, **kwargs):
        return (False, None)


class AccountServiceShapingTests(unittest.TestCase):
    def test_balance_values_prefer_raw_for_docs_only_fields(self) -> None:
        raw = {"cash": "9.9", "effective_buying_power": "100", "bod_dtbp": "200", "daytrade_count": 3}
        values = build_account_balance_values(account=_stub_account(), raw_account=raw)
        self.assertEqual(str(values["effective_buying_power"]), "100")
        self.assertEqual(str(values["bod_dtbp"]), "200")
        self.assertEqual(values["daytrade_count"], 3)
        # current financials live on the detail row, not a bespoke balances payload column
        self.assertNotIn("raw_balances_payload", values)

    def test_detail_values_map_enums_and_levels(self) -> None:
        values = build_account_detail_values(
            account=_stub_account(),
            configuration=SimpleNamespace(
                dtbp_check=SimpleNamespace(value="both"),
                pdt_check=None,
                fractional_trading=True,
                max_margin_multiplier="4",
                no_shorting=False,
                suspend_trade=False,
                trade_confirm_email=SimpleNamespace(value="all"),
                max_options_trading_level=3,
                model_dump=lambda mode=None: {"dtbp_check": "both"},
            ),
            unique_identifier="010203ABCD__ALPACA_PAPER",
            key_fingerprint="abc123",
            is_paper=True,
        )
        self.assertEqual(values["status"], "ACTIVE")
        self.assertEqual(values["dtbp_check"], "both")
        self.assertEqual(values["fractional_trading"], True)
        self.assertEqual(values["options_trading_level"], 2)
        self.assertEqual(values["api_key_fingerprint"], "abc123")
        # current financials are merged onto the same detail row
        self.assertEqual(str(values["cash"]), "1000.50")
        self.assertEqual(str(values["equity"]), "2500.00")

    def test_holdings_rows_equity_plus_cash_skip_non_equity(self) -> None:
        rows, unresolved, skipped = build_holdings_rows(
            positions=_stub_positions(),
            cash="1000.50",
            resolve_symbol=lambda s: {"AAPL": "BBG000B9XRY4"}.get(s),
            currency_identifier="USD",  # an existing currency asset
        )
        identifiers = [r["asset_identifier"] for r in rows]
        self.assertEqual(identifiers, ["BBG000B9XRY4", "USD"])
        self.assertEqual(skipped, ["BTCUSD"])
        self.assertEqual(unresolved, [])
        self.assertEqual(rows[0]["direction"], 1)
        self.assertEqual(str(rows[0]["quantity"]), "10")

    def test_holdings_rows_skip_cash_when_no_currency_asset(self) -> None:
        rows, _unresolved, _skipped = build_holdings_rows(
            positions=[],
            cash="1000.50",
            resolve_symbol=lambda s: None,
            currency_identifier=None,  # USD asset does not exist -> cash is not invented
        )
        self.assertEqual(rows, [])

    def test_symbol_resolver_plan_mode_does_not_register(self) -> None:
        # With register_missing=False (e.g. --plan-only) an unregistered symbol stays unresolved.
        from src.account.services import _make_symbol_resolver

        with patch("src.assets.resolution.assets_for_ticker", return_value=[]):
            self.assertIsNone(_make_symbol_resolver(register_missing=False)("ZZZZ"))

    def test_symbol_resolver_registers_figi_backed_missing_equity(self) -> None:
        # register_missing=True: a held equity that resolves to a FIGI is registered then used.
        from src.account.services import _make_symbol_resolver

        calls = {"n": 0}

        def fake_assets_for_ticker(symbol):
            calls["n"] += 1
            return [] if calls["n"] == 1 else [SimpleNamespace(unique_identifier="BBG000B9XRY4")]

        with (
            patch("src.assets.resolution.assets_for_ticker", side_effect=fake_assets_for_ticker),
            patch(
                "src.assets.alpaca_us_equities.build_alpaca_us_equity_registration_plan",
                return_value="PLAN",
            ),
            patch("src.assets.alpaca_us_equities.register_alpaca_us_equity_assets") as register,
        ):
            result = _make_symbol_resolver(register_missing=True)("AAPL")

        self.assertEqual(result, "BBG000B9XRY4")
        register.assert_called_once()


class RegisterAlpacaAccountTests(unittest.TestCase):
    def test_register_flow_writes_account_detail_balances_and_holdings(self) -> None:
        _FakeNode.frames = []
        account_row = SimpleNamespace(uid="acc-uid-1")
        holdings_set_row = SimpleNamespace(uid="hset-uid-1")
        fake_account = MagicMock()
        fake_account.upsert.return_value = account_row
        fake_holdings_set = MagicMock()
        fake_holdings_set.upsert.return_value = holdings_set_row
        upsert_model = MagicMock()
        build_holdings_frame = MagicMock(return_value="HOLDINGS_FRAME")
        runtime = SimpleNamespace(context=SimpleNamespace())

        client = StubTradingClient(
            _stub_account(), _stub_positions(), raw={"cash": "1000.50", "equity": "2500.00"}
        )

        with (
            patch("src.runtime.start_markets_engine", return_value=runtime),
            patch("msm.api.accounts.Account", fake_account),
            patch("msm.api.accounts.AccountHoldingsSet", fake_holdings_set),
            patch("msm.data_nodes.accounts.AccountHoldings", _FakeNode),
            patch("msm.repositories.crud.upsert_model", upsert_model),
            patch("msm.services.build_account_holdings_frame", build_holdings_frame),
            patch("src.account.services._cash_asset_exists", return_value=True),
        ):
            result = register_alpaca_account(
                api_key="PKTEST",
                secret_key="SKTEST",
                paper=True,
                client=client,
                symbol_resolver=lambda s: {"AAPL": "BBG000B9XRY4"}.get(s),
            )

        self.assertEqual(result.account_unique_identifier, "010203ABCD__ALPACA_PAPER")
        self.assertEqual(result.account_uid, "acc-uid-1")
        self.assertTrue(result.is_paper)
        self.assertEqual(result.holdings_rows, 2)  # AAPL + USD cash
        self.assertEqual(result.skipped_non_equity_symbols, ["BTCUSD"])
        # no bespoke balances time-series table/node is written
        self.assertFalse(hasattr(result, "balances_written"))

        # Account upserted with the stable identity + paper + active status.
        _, account_kwargs = fake_account.upsert.call_args
        self.assertEqual(account_kwargs["unique_identifier"], "010203ABCD__ALPACA_PAPER")
        self.assertTrue(account_kwargs["is_paper"])
        self.assertTrue(account_kwargs["account_is_active"])

        # Detail sidecar upserted once on account_uid, carrying the current financials.
        upsert_model.assert_called_once()
        _, detail_kwargs = upsert_model.call_args
        self.assertEqual(detail_kwargs["values"]["account_uid"], "acc-uid-1")
        self.assertEqual(detail_kwargs["conflict_columns"], ("account_uid",))
        self.assertEqual(str(detail_kwargs["values"]["cash"]), "1000.50")
        self.assertEqual(str(detail_kwargs["values"]["equity"]), "2500.00")

        # Holdings set + frame built with both rows.
        fake_holdings_set.upsert.assert_called_once()
        _, frame_kwargs = build_holdings_frame.call_args
        self.assertEqual(len(frame_kwargs["positions"]), 2)
        self.assertEqual(frame_kwargs["holdings_set_uid"], "hset-uid-1")


if __name__ == "__main__":
    unittest.main()
