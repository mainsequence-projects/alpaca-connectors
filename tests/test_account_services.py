from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from msm.constants import ASSET_TYPE_CURRENCY, ASSET_TYPE_CURRENCY_DEFINITION

from src.account.credentials import ResolvedAlpacaCredentials
from src.account.services import (
    build_account_balance_values,
    build_account_detail_values,
    ensure_cash_currency_asset,
    plan_alpaca_account,
    prepare_alpaca_position_assets,
    register_alpaca_account,
    update_account_registration,
)
from src.holdings import build_account_holdings_rows
from src.holdings.services import (
    AccountHoldingsRegistryError,
    _latest_holdings_set_uid_statement,
    publish_account_holdings_snapshot,
    resolve_complete_account_holdings_rows,
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
            asset_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        ),
        SimpleNamespace(
            symbol="BTCUSD",
            asset_class=SimpleNamespace(value="crypto"),
            side=SimpleNamespace(value="long"),
            qty="0.5",
            asset_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        ),
    ]


def test_latest_holdings_set_query_is_account_scoped_and_deterministic() -> None:
    account_uid = uuid.uuid4()
    statement = _latest_holdings_set_uid_statement(account_uid)
    compiled = statement.compile()

    assert account_uid in compiled.params.values()
    sql = str(compiled)
    assert "account_uid" in sql
    assert "time_index DESC" in sql
    assert "uid DESC" in sql
    assert "LIMIT" in sql


class StubTradingClient:
    def __init__(self, account, positions, raw):
        self._account = account
        self._positions = positions
        self._raw = raw
        self.position_reads = 0

    def get_account(self):
        return self._account

    def get_account_configurations(self):
        return None

    def get_all_positions(self):
        self.position_reads += 1
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
        raw = {
            "cash": "9.9",
            "effective_buying_power": "100",
            "bod_dtbp": "200",
            "daytrade_count": 3,
        }
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
            api_key_secret_name="ALPACA_PAPER_API_KEY",
            secret_key_secret_name="ALPACA_PAPER_SECRET_KEY",
            is_paper=True,
        )
        self.assertEqual(values["status"], "ACTIVE")
        self.assertEqual(values["dtbp_check"], "both")
        self.assertEqual(values["fractional_trading"], True)
        self.assertEqual(values["options_trading_level"], 2)
        self.assertEqual(values["api_key_fingerprint"], "abc123")
        self.assertEqual(values["api_key_secret_name"], "ALPACA_PAPER_API_KEY")
        self.assertEqual(values["secret_key_secret_name"], "ALPACA_PAPER_SECRET_KEY")
        # current financials are merged onto the same detail row
        self.assertEqual(str(values["cash"]), "1000.50")
        self.assertEqual(str(values["equity"]), "2500.00")

    def test_holdings_rows_include_every_alpaca_asset_class_plus_cash(self) -> None:
        rows, unresolved = build_account_holdings_rows(
            positions=_stub_positions(),
            cash="1000.50",
            resolve_asset=lambda position: f"ALPACA::{position.asset_id}",
            currency_identifier="USD",  # an existing currency asset
        )
        identifiers = [r["asset_identifier"] for r in rows]
        self.assertEqual(
            identifiers,
            [
                "ALPACA::11111111-1111-4111-8111-111111111111",
                "ALPACA::22222222-2222-4222-8222-222222222222",
                "USD",
            ],
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(rows[0]["direction"], 1)
        self.assertEqual(str(rows[0]["quantity"]), "10")

    def test_holdings_rows_omit_cash_without_a_currency_identifier(self) -> None:
        rows, _unresolved = build_account_holdings_rows(
            positions=[],
            cash="1000.50",
            resolve_asset=lambda _position: None,
            currency_identifier=None,
        )
        self.assertEqual(rows, [])

    def test_cash_currency_asset_uses_shared_ms_markets_identity(self) -> None:
        asset = SimpleNamespace(unique_identifier="USD")
        with (
            patch("msm.api.assets.AssetType.upsert") as asset_type_upsert,
            patch("msm.api.assets.Asset.upsert", return_value=asset) as asset_upsert,
            patch("msm.api.assets.CurrencySpot.upsert") as currency_spot_upsert,
            patch(
                "src.assets.alpaca_asset_details.upsert_alpaca_asset_details"
            ) as alpaca_details_upsert,
        ):
            identifier = ensure_cash_currency_asset("usd")

        self.assertEqual(identifier, "USD")
        asset_type_upsert.assert_called_once_with(**ASSET_TYPE_CURRENCY_DEFINITION.as_payload())
        asset_upsert.assert_called_once_with(
            unique_identifier="USD",
            asset_type=ASSET_TYPE_CURRENCY,
        )
        currency_spot_upsert.assert_not_called()
        alpaca_details_upsert.assert_not_called()

    def test_position_identity_uses_alpaca_asset_uuid(self) -> None:
        from src.account.services import canonical_identifier_for_position

        self.assertEqual(
            canonical_identifier_for_position(_stub_positions()[0]),
            "ALPACA::11111111-1111-4111-8111-111111111111",
        )

    def test_position_resolver_registers_missing_asset_without_figi_requirement(self) -> None:
        from src.account.services import make_position_resolver

        provider_asset = SimpleNamespace(
            id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            symbol="AAPL",
            name="Apple Inc.",
            exchange=SimpleNamespace(value="NASDAQ"),
            asset_class=SimpleNamespace(value="us_equity"),
            status=SimpleNamespace(value="active"),
            tradable=True,
            marginable=True,
            shortable=True,
            easy_to_borrow=True,
            fractionable=True,
            attributes=[],
            model_dump=lambda mode=None: {"id": "11111111-1111-4111-8111-111111111111"},
        )
        client = SimpleNamespace(get_asset=lambda _asset_id: provider_asset)
        with (
            patch("msm.api.assets.Asset.get_by_unique_identifier", return_value=None),
            patch(
                "src.assets.alpaca_us_equities.classify_alpaca_us_equities",
                return_value="PLAN",
            ),
            patch(
                "src.assets.alpaca_us_equities.resolve_alpaca_us_equity_registration_plan",
                return_value="RESOLUTION",
            ),
            patch(
                "src.assets.alpaca_us_equities.register_alpaca_us_equity_assets",
                return_value={"assets": {"AAPL": "ALPACA::11111111-1111-4111-8111-111111111111"}},
            ) as register,
        ):
            result = make_position_resolver(
                trading_client=client,
                register_missing=True,
            )(_stub_positions()[0])

        self.assertEqual(result, "ALPACA::11111111-1111-4111-8111-111111111111")
        register.assert_called_once_with(registration_resolution="RESOLUTION")

    def test_position_identity_mismatch_names_asset_and_both_uuids(self) -> None:
        from src.account.services import make_position_resolver

        provider_asset = SimpleNamespace(
            id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
            symbol="WRONG",
            name="Wrong asset",
            exchange=SimpleNamespace(value="NASDAQ"),
            asset_class=SimpleNamespace(value="us_equity"),
            status=SimpleNamespace(value="active"),
            tradable=True,
            marginable=True,
            shortable=True,
            easy_to_borrow=True,
            fractionable=True,
            attributes=[],
            model_dump=lambda mode=None: {"id": "33333333-3333-4333-8333-333333333333"},
        )
        client = SimpleNamespace(get_asset=lambda _asset_id: provider_asset)

        with patch("msm.api.assets.Asset.get_by_unique_identifier", return_value=None):
            with self.assertRaises(ValueError) as raised:
                make_position_resolver(
                    trading_client=client,
                    register_missing=True,
                )(_stub_positions()[0])

        message = str(raised.exception)
        self.assertIn("AAPL", message)
        self.assertIn("11111111-1111-4111-8111-111111111111", message)
        self.assertIn("WRONG", message)
        self.assertIn("33333333-3333-4333-8333-333333333333", message)

    def test_position_set_uses_one_provider_catalog_and_one_registration_batch(self) -> None:
        positions = _stub_positions()
        provider_assets = [
            SimpleNamespace(
                id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
                symbol="AAPL",
                name="Apple Inc.",
                exchange=SimpleNamespace(value="NASDAQ"),
                asset_class=SimpleNamespace(value="us_equity"),
                status=SimpleNamespace(value="active"),
                tradable=True,
                marginable=True,
                shortable=True,
                easy_to_borrow=True,
                fractionable=True,
                attributes=[],
            ),
            SimpleNamespace(
                id=uuid.UUID("276e2673-764b-4ab6-a611-caf665ca6340"),
                symbol="BTC/USD",
                name="Bitcoin / US Dollar",
                exchange=SimpleNamespace(value="CRYPTO"),
                asset_class=SimpleNamespace(value="crypto"),
                status=SimpleNamespace(value="active"),
                tradable=True,
                marginable=False,
                shortable=False,
                easy_to_borrow=False,
                fractionable=True,
                attributes=[],
            ),
        ]
        client = MagicMock()
        client.get_all_assets.return_value = provider_assets
        resolution = SimpleNamespace(missing_assets=[])

        with (
            patch(
                "src.assets.alpaca_us_equities.resolve_alpaca_us_equity_registration_plan",
                return_value=resolution,
            ) as resolve_registration,
            patch(
                "src.assets.alpaca_us_equities.register_alpaca_us_equity_assets"
            ) as register_assets,
        ):
            identifiers, result_resolution = prepare_alpaca_position_assets(
                trading_client=client,
                positions=positions,
                register_missing=True,
            )

        self.assertIs(result_resolution, resolution)
        self.assertEqual(
            identifiers,
            {
                id(positions[0]): "ALPACA::11111111-1111-4111-8111-111111111111",
                id(positions[1]): "ALPACA::276e2673-764b-4ab6-a611-caf665ca6340",
            },
        )
        client.get_all_assets.assert_called_once_with()
        client.get_asset.assert_not_called()
        resolve_registration.assert_called_once()
        self.assertEqual(len(resolve_registration.call_args.args[0].alpaca_assets), 2)
        register_assets.assert_called_once_with(registration_resolution=resolution)

    def test_crypto_position_resolves_catalog_uuid_by_symbol(self) -> None:
        from src.account.services import make_position_resolver

        provider_asset = SimpleNamespace(
            id=uuid.UUID("276e2673-764b-4ab6-a611-caf665ca6340"),
            symbol="BTC/USD",
            name="Bitcoin / US Dollar",
            exchange=SimpleNamespace(value="CRYPTO"),
            asset_class=SimpleNamespace(value="crypto"),
            status=SimpleNamespace(value="active"),
            tradable=True,
            marginable=False,
            shortable=False,
            easy_to_borrow=False,
            fractionable=True,
            attributes=[],
            model_dump=lambda mode=None: {"id": "276e2673-764b-4ab6-a611-caf665ca6340"},
        )
        client = MagicMock()
        client.get_asset.return_value = provider_asset
        canonical_identifier = "ALPACA::276e2673-764b-4ab6-a611-caf665ca6340"

        with (
            patch("msm.api.assets.Asset.get_by_unique_identifier", return_value=None),
            patch(
                "src.assets.alpaca_us_equities.resolve_alpaca_us_equity_registration_plan",
                return_value="RESOLUTION",
            ),
            patch(
                "src.assets.alpaca_us_equities.register_alpaca_us_equity_assets",
                return_value={"assets": {"BTC/USD": canonical_identifier}},
            ),
        ):
            result = make_position_resolver(
                trading_client=client,
                register_missing=True,
            )(_stub_positions()[1])

        self.assertEqual(result, canonical_identifier)
        client.get_asset.assert_called_once_with("BTCUSD")

    def test_unresolved_position_blocks_snapshot_before_storage(self) -> None:
        snapshot_time = SimpleNamespace()
        with (
            patch("src.account.services.ensure_cash_currency_asset", return_value="USD"),
            patch("msm.api.accounts.AccountHoldingsSet") as holdings_set,
        ):
            with self.assertRaisesRegex(
                AccountHoldingsRegistryError,
                "11111111-1111-4111-8111-111111111111",
            ):
                publish_account_holdings_snapshot(
                    account_uid="account-uid",
                    positions=[_stub_positions()[0]],
                    cash="0",
                    snapshot_time=snapshot_time,
                    asset_resolver=lambda _position: None,
                )

        holdings_set.upsert.assert_not_called()

    def test_crypto_position_is_not_rejected_by_class(self) -> None:
        with patch("src.account.services.ensure_cash_currency_asset", return_value="USD"):
            rows = resolve_complete_account_holdings_rows(
                positions=[_stub_positions()[1]],
                cash="0",
                asset_resolver=lambda position: f"ALPACA::{position.asset_id}",
            )
        self.assertEqual(
            rows[0]["asset_identifier"], "ALPACA::22222222-2222-4222-8222-222222222222"
        )

    def test_nonzero_cash_ensures_and_uses_canonical_currency_asset(self) -> None:
        with patch(
            "src.account.services.ensure_cash_currency_asset", return_value="USD"
        ) as ensure_cash:
            rows = resolve_complete_account_holdings_rows(
                positions=[],
                cash="1000.50",
                asset_resolver=lambda _position: None,
            )

        ensure_cash.assert_called_once_with("USD")
        self.assertEqual(rows[0]["asset_identifier"], "USD")
        self.assertEqual(rows[0]["extra_details"], {"kind": "cash"})

    def test_account_plan_reports_missing_cash_asset_without_writing_it(self) -> None:
        client = StubTradingClient(_stub_account(), [], raw={"cash": "1000.50"})
        with (
            patch("src.runtime.start_markets_engine"),
            patch(
                "src.account.services.resolve_alpaca_credentials",
                return_value=ResolvedAlpacaCredentials(api_key="PKTEST", secret_key="SKTEST"),
            ),
            patch("src.account.services.cash_asset_exists", return_value=False),
            patch("src.account.services.ensure_cash_currency_asset") as ensure_cash,
        ):
            plan = plan_alpaca_account(
                api_key_secret_name="ALPACA_PAPER_API_KEY",
                secret_key_secret_name="ALPACA_PAPER_SECRET_KEY",
                client=client,
            )

        self.assertEqual(plan["cash_asset_identifiers_to_ensure"], ["USD"])
        self.assertEqual(plan["would_write_holdings"], 1)
        self.assertEqual(plan["unresolved_symbols"], [])
        ensure_cash.assert_not_called()

    def test_holdings_plan_treats_missing_cash_asset_as_an_execution_write(self) -> None:
        from src.holdings.services import plan_alpaca_account_holdings

        client = StubTradingClient(_stub_account(), [], raw={"cash": "1000.50"})
        registration = {
            "uid": "account-uid",
            "alpaca_account_id": "aid-uuid",
        }
        with (
            patch("src.runtime.start_markets_engine"),
            patch("src.account.services.get_account_registration", return_value=registration),
            patch("src.account.services.cash_asset_exists", return_value=False),
        ):
            plan = plan_alpaca_account_holdings("account-uid", client=client)

        self.assertTrue(plan["allowed"])
        self.assertEqual(plan["cash_asset_identifiers_to_ensure"], ["USD"])
        self.assertEqual(plan["would_write_holdings"], 1)
        self.assertEqual(plan["blockers"], [])
        self.assertIn("canonical cash currency Asset 'USD'", plan["warnings"][0])


class RegisterAlpacaAccountTests(unittest.TestCase):
    def test_register_flow_writes_account_detail_balances_and_holdings(self) -> None:
        _FakeNode.frames = []
        account_row = SimpleNamespace(uid="acc-uid-1")
        fake_account = MagicMock()
        fake_account.upsert.return_value = account_row
        upsert_model = MagicMock()
        runtime = SimpleNamespace(context=SimpleNamespace())
        capture_result = SimpleNamespace(
            holdings_rows=2,
            unresolved_symbols=[],
        )

        client = StubTradingClient(
            _stub_account(), [_stub_positions()[0]], raw={"cash": "1000.50", "equity": "2500.00"}
        )
        resolved_rows = [
            {"asset_identifier": "ALPACA::11111111-1111-4111-8111-111111111111"},
            {"asset_identifier": "USD"},
        ]

        with (
            patch("src.runtime.start_markets_engine", return_value=runtime),
            patch("msm.api.accounts.Account", fake_account),
            patch("msm.repositories.crud.upsert_model", upsert_model),
            patch(
                "src.account.services.resolve_alpaca_credentials",
                return_value=ResolvedAlpacaCredentials(
                    api_key="PKTEST",
                    secret_key="SKTEST",
                ),
            ),
            patch(
                "src.holdings.services.resolve_complete_account_holdings_rows",
                return_value=resolved_rows,
            ) as resolve_holdings,
            patch(
                "src.holdings.services.publish_resolved_account_holdings_snapshot",
                return_value=capture_result,
            ) as publish_holdings,
        ):
            result = register_alpaca_account(
                api_key_secret_name="ALPACA_PAPER_API_KEY",
                secret_key_secret_name="ALPACA_PAPER_SECRET_KEY",
                paper=True,
                client=client,
                asset_resolver=lambda position: f"ALPACA::{position.asset_id}",
            )

        self.assertEqual(result.account_unique_identifier, "010203ABCD__ALPACA_PAPER")
        self.assertEqual(result.account_uid, "acc-uid-1")
        self.assertTrue(result.is_paper)
        self.assertEqual(result.holdings_rows, 2)  # AAPL + USD cash
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
        self.assertEqual(
            detail_kwargs["values"]["api_key_secret_name"],
            "ALPACA_PAPER_API_KEY",
        )
        self.assertNotIn("PKTEST", str(detail_kwargs["values"]))

        resolve_holdings.assert_called_once()
        self.assertEqual(publish_holdings.call_args.kwargs["rows"], resolved_rows)

    def test_initial_holdings_registry_failure_prevents_account_creation(self) -> None:
        fake_account = MagicMock()
        runtime = SimpleNamespace(context=SimpleNamespace())
        registry_error = AccountHoldingsRegistryError(
            unresolved_symbols=["AAPL"],
        )
        client = StubTradingClient(_stub_account(), [_stub_positions()[0]], raw={"cash": "1000.50"})

        with (
            patch("src.runtime.start_markets_engine", return_value=runtime),
            patch("msm.api.accounts.Account", fake_account),
            patch("msm.repositories.crud.upsert_model") as upsert_model,
            patch(
                "src.account.services.resolve_alpaca_credentials",
                return_value=ResolvedAlpacaCredentials(api_key="PKTEST", secret_key="SKTEST"),
            ),
            patch(
                "src.holdings.services.resolve_complete_account_holdings_rows",
                side_effect=registry_error,
            ),
            patch(
                "src.account.services.prepare_alpaca_position_assets",
                return_value=({}, SimpleNamespace()),
            ),
            patch(
                "src.holdings.services.publish_resolved_account_holdings_snapshot"
            ) as publish_holdings,
        ):
            with self.assertRaises(AccountHoldingsRegistryError):
                register_alpaca_account(
                    api_key_secret_name="ALPACA_PAPER_API_KEY",
                    secret_key_secret_name="ALPACA_PAPER_SECRET_KEY",
                    client=client,
                )

        fake_account.upsert.assert_not_called()
        upsert_model.assert_not_called()
        publish_holdings.assert_not_called()

    def test_secret_name_rotation_rejects_a_different_alpaca_account(self) -> None:
        current = {
            "uid": "acc-uid-1",
            "account_uid": "acc-uid-1",
            "alpaca_account_id": "expected-alpaca-id",
            "api_key_secret_name": "OLD_API_KEY",
            "secret_key_secret_name": "OLD_SECRET_KEY",
            "is_paper": True,
        }
        candidate_client = SimpleNamespace(
            get_account=lambda: SimpleNamespace(id="different-alpaca-id")
        )
        with (
            patch("src.account.services.get_account_registration", return_value=current),
            patch("src.runtime.start_markets_engine", return_value=SimpleNamespace(context=None)),
            patch(
                "src.account.services.resolve_alpaca_credentials",
                return_value=ResolvedAlpacaCredentials(api_key="rotated", secret_key="secret"),
            ),
            patch(
                "src.account.services.build_alpaca_trading_client",
                return_value=candidate_client,
            ),
            patch("msm.repositories.crud.update_model") as update_model,
        ):
            with self.assertRaisesRegex(ValueError, "different Alpaca account"):
                update_account_registration(
                    "acc-uid-1",
                    api_key_secret_name="NEW_API_KEY",
                    secret_key_secret_name="NEW_SECRET_KEY",
                )

        update_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
