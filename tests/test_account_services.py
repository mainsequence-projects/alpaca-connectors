from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from msm.constants import ASSET_TYPE_CURRENCY, ASSET_TYPE_CURRENCY_DEFINITION

from alpaca_connectors.account import api_key_fingerprint
from alpaca_connectors.account.credentials import (
    CREDENTIAL_SOURCE_IMPORTED,
    CREDENTIAL_SOURCE_MANAGED,
    LEGACY_SECRET_REFERENCE_COLUMNS,
    AlpacaCredentialsRejectedError,
    AlpacaSecretNames,
    ResolvedAlpacaCredentials,
)
from alpaca_connectors.account.legacy_secrets import LegacySecretReferences
from alpaca_connectors.account.services import (
    build_account_balance_values,
    build_account_detail_values,
    ensure_cash_currency_asset,
    plan_alpaca_account,
    prepare_alpaca_position_assets,
    register_alpaca_account,
    remove_account_registration,
    update_account_registration,
)
from alpaca_connectors.holdings import build_account_holdings_rows
from alpaca_connectors.holdings.services import (
    AccountHoldingsRegistryError,
    _holdings_sets,
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


def _stub_registration() -> dict[str, object]:
    """A registration whose keys are stored on the row (ADR 0017)."""
    return {
        "uid": "acc-uid-1",
        "account_uid": "acc-uid-1",
        "unique_identifier": "010203ABCD__ALPACA_PAPER",
        "alpaca_account_id": "expected-alpaca-id",
        "credential_source": CREDENTIAL_SOURCE_MANAGED,
        "credentials_stored": True,
        "api_key_fingerprint": "abc123",
        **{column: None for column in LEGACY_SECRET_REFERENCE_COLUMNS},
        "is_paper": True,
    }


def _legacy_registration() -> dict[str, object]:
    """A registration made before revision 0018 that still references managed Secrets."""
    return {
        **_stub_registration(),
        "credentials_stored": False,
        "api_key_secret_name": "ALPACA_CONNECTORS__010203ABCD__ALPACA_PAPER__API_KEY",
        "secret_key_secret_name": "ALPACA_CONNECTORS__010203ABCD__ALPACA_PAPER__SECRET_KEY",
        "api_key_secret_uid": "managed-api-uid",
        "secret_key_secret_uid": "managed-secret-uid",
    }


EXTERNAL_NAMES = AlpacaSecretNames(
    api_key_secret_name="ALPACA_PAPER_API_KEY",
    secret_key_secret_name="ALPACA_PAPER_SECRET_KEY",
)
SUBMITTED = ResolvedAlpacaCredentials(api_key="PKSUBMITTED", secret_key="SKSUBMITTED")
IMPORTED = ResolvedAlpacaCredentials(api_key="PKTEST", secret_key="SKTEST")


def _imported(credentials=IMPORTED):
    """Patch the one-time import of Secret names so no Main Sequence Secret is read."""
    return patch(
        "alpaca_connectors.account.services.resolve_credential_input",
        side_effect=lambda value: (
            (credentials, CREDENTIAL_SOURCE_IMPORTED)
            if isinstance(value, AlpacaSecretNames)
            else (value, CREDENTIAL_SOURCE_MANAGED)
        ),
    )


def test_latest_holdings_set_query_is_account_scoped_and_deterministic() -> None:
    from msm.api.accounts import AccountHoldingsSet

    account_uid = uuid.uuid4()
    calls: list[dict] = []
    with patch.object(
        AccountHoldingsSet,
        "filter",
        classmethod(lambda cls, **kw: calls.append(kw) or []),
    ):
        _holdings_sets(account_uid, limit=1)

    assert calls == [
        {"account_uid": str(account_uid), "order_by": ["-time_index", "-uid"], "limit": 1}
    ]


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
            is_paper=True,
        )
        self.assertEqual(values["status"], "ACTIVE")
        self.assertEqual(values["dtbp_check"], "both")
        self.assertEqual(values["fractional_trading"], True)
        self.assertEqual(values["options_trading_level"], 2)
        self.assertEqual(values["api_key_fingerprint"], "abc123")
        # The keys themselves are added by stored_credential_values, never here.
        self.assertNotIn("api_key", values)
        self.assertNotIn("secret_key", values)
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
                "alpaca_connectors.assets.alpaca_asset_details.upsert_alpaca_asset_details"
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
        from alpaca_connectors.account.services import canonical_identifier_for_position

        self.assertEqual(
            canonical_identifier_for_position(_stub_positions()[0]),
            "ALPACA::11111111-1111-4111-8111-111111111111",
        )

    def test_position_resolver_registers_missing_asset_without_figi_requirement(self) -> None:
        from alpaca_connectors.account.services import make_position_resolver

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
                "alpaca_connectors.assets.alpaca_us_equities.classify_alpaca_us_equities",
                return_value="PLAN",
            ),
            patch(
                "alpaca_connectors.assets.alpaca_us_equities.resolve_alpaca_us_equity_registration_plan",
                return_value="RESOLUTION",
            ),
            patch(
                "alpaca_connectors.assets.alpaca_us_equities.register_alpaca_us_equity_assets",
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
        from alpaca_connectors.account.services import make_position_resolver

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
                "alpaca_connectors.assets.alpaca_us_equities.resolve_alpaca_us_equity_registration_plan",
                return_value=resolution,
            ) as resolve_registration,
            patch(
                "alpaca_connectors.assets.alpaca_us_equities.register_alpaca_us_equity_assets"
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
        from alpaca_connectors.account.services import make_position_resolver

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
                "alpaca_connectors.assets.alpaca_us_equities.resolve_alpaca_us_equity_registration_plan",
                return_value="RESOLUTION",
            ),
            patch(
                "alpaca_connectors.assets.alpaca_us_equities.register_alpaca_us_equity_assets",
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
            patch(
                "alpaca_connectors.account.services.ensure_cash_currency_asset", return_value="USD"
            ),
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
        with patch(
            "alpaca_connectors.account.services.ensure_cash_currency_asset", return_value="USD"
        ):
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
            "alpaca_connectors.account.services.ensure_cash_currency_asset", return_value="USD"
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
            patch("alpaca_connectors.runtime.start_markets_engine"),
            _imported(),
            patch("alpaca_connectors.account.services.cash_asset_exists", return_value=False),
            patch("alpaca_connectors.account.services.ensure_cash_currency_asset") as ensure_cash,
        ):
            plan = plan_alpaca_account(credentials=EXTERNAL_NAMES, client=client)

        self.assertEqual(plan["cash_asset_identifiers_to_ensure"], ["USD"])
        self.assertEqual(plan["would_write_holdings"], 1)
        self.assertEqual(plan["unresolved_symbols"], [])
        self.assertEqual(plan["credential_source"], CREDENTIAL_SOURCE_IMPORTED)
        self.assertNotIn("secret_writes", plan)
        self.assertNotIn("ALPACA_PAPER_API_KEY", str(plan))
        self.assertNotIn("PKTEST", str(plan))
        ensure_cash.assert_not_called()

    def test_managed_plan_reports_the_source_without_storing_or_showing_values(self) -> None:
        client = StubTradingClient(_stub_account(), [], raw={"cash": "1000.50"})
        with (
            patch("alpaca_connectors.runtime.start_markets_engine"),
            patch("alpaca_connectors.account.services.cash_asset_exists", return_value=True),
            patch("alpaca_connectors.account.services.write_stored_credentials") as write,
            patch("msm.repositories.crud.upsert_model") as upsert_model,
        ):
            plan = plan_alpaca_account(credentials=SUBMITTED, client=client)

        write.assert_not_called()
        upsert_model.assert_not_called()
        self.assertEqual(plan["credential_source"], CREDENTIAL_SOURCE_MANAGED)
        self.assertNotIn("PKSUBMITTED", str(plan))
        self.assertNotIn("SKSUBMITTED", str(plan))

    def test_holdings_plan_treats_missing_cash_asset_as_an_execution_write(self) -> None:
        from alpaca_connectors.holdings.services import plan_alpaca_account_holdings

        client = StubTradingClient(_stub_account(), [], raw={"cash": "1000.50"})
        registration = {
            "uid": "account-uid",
            "alpaca_account_id": "aid-uuid",
        }
        with (
            patch("alpaca_connectors.runtime.start_markets_engine"),
            patch(
                "alpaca_connectors.account.services.get_account_registration",
                return_value=registration,
            ),
            patch("alpaca_connectors.account.services.cash_asset_exists", return_value=False),
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
            patch("alpaca_connectors.runtime.start_markets_engine", return_value=runtime),
            patch("msm.api.accounts.Account", fake_account),
            patch("msm.repositories.crud.upsert_model", upsert_model),
            _imported(),
            patch(
                "alpaca_connectors.account.services._previous_legacy_references", return_value=None
            ),
            patch(
                "alpaca_connectors.holdings.services.resolve_complete_account_holdings_rows",
                return_value=resolved_rows,
            ) as resolve_holdings,
            patch(
                "alpaca_connectors.holdings.services.publish_resolved_account_holdings_snapshot",
                return_value=capture_result,
            ) as publish_holdings,
        ):
            result = register_alpaca_account(
                credentials=EXTERNAL_NAMES,
                paper=True,
                client=client,
                asset_resolver=lambda position: f"ALPACA::{position.asset_id}",
                actor_user_uid="user-uid",
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
        # The keys travel in the same upsert as the rest of the row; the model's encrypted
        # columns seal them when the statement is compiled, and no Secret name is kept.
        self.assertEqual(detail_kwargs["values"]["api_key"], "PKTEST")
        self.assertEqual(detail_kwargs["values"]["secret_key"], "SKTEST")
        self.assertEqual(
            detail_kwargs["values"]["api_key_fingerprint"], api_key_fingerprint("PKTEST")
        )
        self.assertEqual(detail_kwargs["values"]["credential_source"], CREDENTIAL_SOURCE_IMPORTED)
        self.assertEqual(detail_kwargs["values"]["credentials_updated_by_user_uid"], "user-uid")
        for column in LEGACY_SECRET_REFERENCE_COLUMNS:
            self.assertIsNone(detail_kwargs["values"][column])
        self.assertNotIn("ALPACA_PAPER_API_KEY", str(detail_kwargs["values"]))
        self.assertEqual(result.credential_source, CREDENTIAL_SOURCE_IMPORTED)
        self.assertFalse(hasattr(result, "api_key_secret_name"))

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
            patch("alpaca_connectors.runtime.start_markets_engine", return_value=runtime),
            patch("msm.api.accounts.Account", fake_account),
            patch("msm.repositories.crud.upsert_model") as upsert_model,
            _imported(),
            patch(
                "alpaca_connectors.holdings.services.resolve_complete_account_holdings_rows",
                side_effect=registry_error,
            ),
            patch(
                "alpaca_connectors.account.services.prepare_alpaca_position_assets",
                return_value=({}, SimpleNamespace()),
            ),
            patch(
                "alpaca_connectors.holdings.services.publish_resolved_account_holdings_snapshot"
            ) as publish_holdings,
        ):
            with self.assertRaises(AccountHoldingsRegistryError):
                register_alpaca_account(credentials=EXTERNAL_NAMES, client=client)

        fake_account.upsert.assert_not_called()
        upsert_model.assert_not_called()
        publish_holdings.assert_not_called()

    def test_import_rotation_rejects_a_different_alpaca_account(self) -> None:
        candidate_client = SimpleNamespace(
            get_account=lambda: SimpleNamespace(id="different-alpaca-id")
        )
        with (
            patch(
                "alpaca_connectors.account.services.get_account_registration",
                return_value=_stub_registration(),
            ),
            patch(
                "alpaca_connectors.runtime.start_markets_engine",
                return_value=SimpleNamespace(context=None),
            ),
            _imported(ResolvedAlpacaCredentials(api_key="rotated", secret_key="secret")),
            patch(
                "alpaca_connectors.account.services.build_alpaca_trading_client",
                return_value=candidate_client,
            ),
            patch("alpaca_connectors.account.services.write_stored_credentials") as write,
        ):
            with self.assertRaisesRegex(ValueError, "different Alpaca account"):
                update_account_registration(
                    "acc-uid-1",
                    credential_secret_names=AlpacaSecretNames("NEW_API_KEY", "NEW_SECRET_KEY"),
                )

        write.assert_not_called()

    def test_name_and_active_changes_never_read_or_write_credentials(self) -> None:
        fake_account = MagicMock()
        with (
            patch(
                "alpaca_connectors.account.services.get_account_registration",
                return_value=_stub_registration(),
            ),
            patch(
                "alpaca_connectors.runtime.start_markets_engine",
                return_value=SimpleNamespace(context=None),
            ),
            patch("msm.api.accounts.Account", fake_account),
            patch("alpaca_connectors.account.services.resolve_credential_input") as resolve,
            patch("alpaca_connectors.account.services.build_alpaca_trading_client") as build_client,
            patch("alpaca_connectors.account.services.write_stored_credentials") as write,
        ):
            result = update_account_registration(
                "acc-uid-1",
                account_name="Renamed",
                account_is_active=False,
            )

        fake_account.update.assert_called_once_with(
            "acc-uid-1", {"account_name": "Renamed", "account_is_active": False}
        )
        resolve.assert_not_called()
        build_client.assert_not_called()
        write.assert_not_called()
        self.assertEqual(result["account_uid"], "acc-uid-1")

    def test_import_rotation_validates_then_stores_the_imported_keys(self) -> None:
        credentials = ResolvedAlpacaCredentials(api_key="rotated", secret_key="secret")
        candidate_client = SimpleNamespace(
            get_account=lambda: SimpleNamespace(id="expected-alpaca-id")
        )
        with (
            patch(
                "alpaca_connectors.account.services.get_account_registration",
                return_value=_stub_registration(),
            ),
            patch(
                "alpaca_connectors.runtime.start_markets_engine",
                return_value=SimpleNamespace(context="context"),
            ),
            _imported(credentials) as resolve,
            patch(
                "alpaca_connectors.account.services.build_alpaca_trading_client",
                return_value=candidate_client,
            ) as build_client,
            patch("alpaca_connectors.account.services.write_stored_credentials") as write,
            patch(
                "alpaca_connectors.account.services.delete_legacy_managed_secrets"
            ) as delete_legacy,
        ):
            update_account_registration(
                "acc-uid-1",
                credential_secret_names=AlpacaSecretNames("NEW_API_KEY", "OLD_SECRET_KEY"),
                actor_user_uid="user-uid",
            )

        resolve.assert_called_once_with(AlpacaSecretNames("NEW_API_KEY", "OLD_SECRET_KEY"))
        build_client.assert_called_once_with(credentials=credentials, paper=True)
        write.assert_called_once()
        self.assertEqual(write.call_args.args, ("context",))
        self.assertEqual(write.call_args.kwargs["account_uid"], "acc-uid-1")
        values = dict(write.call_args.kwargs["values"])
        self.assertIsNotNone(values.pop("credentials_updated_at"))
        self.assertEqual(
            values,
            {
                "api_key": "rotated",
                "secret_key": "secret",
                "api_key_fingerprint": api_key_fingerprint("rotated"),
                "credential_source": CREDENTIAL_SOURCE_IMPORTED,
                "credentials_updated_by_user_uid": "user-uid",
                **{column: None for column in LEGACY_SECRET_REFERENCE_COLUMNS},
            },
        )
        # A migrated registration references no Secret, so nothing is deleted.
        delete_legacy.assert_not_called()


class _RejectingTradingClient(StubTradingClient):
    def get_account(self):
        from alpaca.common.exceptions import APIError

        raise APIError(
            '{"message": "forbidden"}',
            http_error=SimpleNamespace(response=SimpleNamespace(status_code=403)),
        )


def _legacy_managed_references() -> LegacySecretReferences:
    legacy = _legacy_registration()
    return LegacySecretReferences(
        api_key_secret_name=str(legacy["api_key_secret_name"]),
        secret_key_secret_name=str(legacy["secret_key_secret_name"]),
        api_key_secret_uid="managed-api-uid",
        secret_key_secret_uid="managed-secret-uid",
        credential_source=CREDENTIAL_SOURCE_MANAGED,
    )


class ManagedCredentialRegistrationTests(unittest.TestCase):
    def _register(self, client, **patches):
        """Run a managed registration with storage and holdings mocked."""
        events: list[str] = []
        fake_account = MagicMock()
        fake_account.upsert.side_effect = patches.get(
            "account_upsert",
            lambda **kwargs: events.append("account") or SimpleNamespace(uid="acc-uid-1"),
        )
        upsert_model = MagicMock(side_effect=lambda *args, **kwargs: events.append("detail"))
        with (
            patch(
                "alpaca_connectors.runtime.start_markets_engine",
                return_value=SimpleNamespace(context=SimpleNamespace()),
            ),
            patch("msm.api.accounts.Account", fake_account),
            patch("msm.repositories.crud.upsert_model", upsert_model),
            patch(
                "alpaca_connectors.holdings.services.resolve_complete_account_holdings_rows",
                side_effect=lambda **kwargs: events.append("holdings") or [],
            ),
            patch(
                "alpaca_connectors.holdings.services.publish_resolved_account_holdings_snapshot",
                return_value=SimpleNamespace(holdings_rows=1, unresolved_symbols=[]),
            ),
            patch(
                "alpaca_connectors.account.services._previous_legacy_references",
                return_value=patches.get("previous_references"),
            ),
            patch(
                "alpaca_connectors.account.services.delete_legacy_managed_secrets",
                return_value=patches.get("deleted", ([], [])),
            ) as delete_legacy,
            patch("alpaca_connectors.platform_secrets.read_platform_secret_value") as read_secret,
        ):
            outcome: object
            try:
                outcome = register_alpaca_account(
                    credentials=SUBMITTED,
                    paper=True,
                    client=client,
                    asset_resolver=lambda position: None,
                    actor_user_uid="user-uid",
                )
            except Exception as exc:  # noqa: BLE001 - returned for assertions
                outcome = exc
        return outcome, events, fake_account, upsert_model, delete_legacy, read_secret

    def test_managed_registration_stores_the_keys_after_alpaca_and_holdings_checks(self) -> None:
        client = StubTradingClient(_stub_account(), [], raw={"cash": "1000.50"})
        result, events, _, upsert_model, delete_legacy, read_secret = self._register(client)

        self.assertEqual(events, ["holdings", "account", "detail"])
        read_secret.assert_not_called()
        delete_legacy.assert_not_called()
        values = upsert_model.call_args.kwargs["values"]
        self.assertEqual(values["credential_source"], CREDENTIAL_SOURCE_MANAGED)
        self.assertEqual(values["api_key"], "PKSUBMITTED")
        self.assertEqual(values["secret_key"], "SKSUBMITTED")
        self.assertEqual(values["api_key_fingerprint"], api_key_fingerprint("PKSUBMITTED"))
        self.assertEqual(values["credentials_updated_by_user_uid"], "user-uid")
        for column in LEGACY_SECRET_REFERENCE_COLUMNS:
            self.assertIsNone(values[column])
        self.assertEqual(result.credential_source, CREDENTIAL_SOURCE_MANAGED)
        self.assertNotIn("PKSUBMITTED", str(result))

    def test_rejected_credentials_write_nothing(self) -> None:
        client = _RejectingTradingClient(_stub_account(), [], raw={})
        outcome, events, fake_account, upsert_model, _, _ = self._register(client)

        self.assertIsInstance(outcome, AlpacaCredentialsRejectedError)
        self.assertEqual(events, [])
        fake_account.upsert.assert_not_called()
        upsert_model.assert_not_called()

    def test_a_failed_account_write_stores_no_keys(self) -> None:
        def failing_upsert(**kwargs):
            raise RuntimeError("account write failed")

        client = StubTradingClient(_stub_account(), [], raw={})
        outcome, _, _, upsert_model, _, _ = self._register(client, account_upsert=failing_upsert)

        self.assertIsInstance(outcome, RuntimeError)
        upsert_model.assert_not_called()

    def test_reregistering_a_legacy_identity_deletes_the_secrets_it_still_referenced(self) -> None:
        previous = _legacy_managed_references()
        client = StubTradingClient(_stub_account(), [], raw={})
        result, _, _, upsert_model, delete_legacy, _ = self._register(
            client,
            previous_references=previous,
            deleted=([], ["Managed Secret 'X' could not be deleted."]),
        )

        delete_legacy.assert_called_once_with(previous)
        self.assertEqual(result.warnings, ["Managed Secret 'X' could not be deleted."])
        # The new keys replace the references whatever happened to the old Secrets.
        values = upsert_model.call_args.kwargs["values"]
        self.assertIsNone(values["api_key_secret_uid"])


class RegistrationSealingTests(unittest.TestCase):
    """The upsert that stores a registration leaves the process with the keys sealed."""

    def setUp(self) -> None:
        from metatables.encryption import Keyring, set_keyring_source

        self.ring = Keyring.new("test")
        set_keyring_source(lambda name: self.ring.render())

    def tearDown(self) -> None:
        from metatables.encryption import set_keyring_source

        set_keyring_source(None)

    def test_the_registration_upsert_carries_envelopes_not_values(self) -> None:
        from metatables.compiled_sql.v1 import compile_sqlalchemy_statement
        from metatables.encryption import is_envelope

        operations = []

        def offline_compile(statement, *, context, operation, dialect=None):
            return compile_sqlalchemy_statement(
                statement, operation=operation, data_source_uid="ds", dialect="postgresql"
            )

        client = StubTradingClient(_stub_account(), [], raw={"cash": "1000.50"})
        fake_account = MagicMock()
        fake_account.upsert.return_value = SimpleNamespace(uid="acc-uid-1")
        with (
            patch(
                "alpaca_connectors.runtime.start_markets_engine",
                return_value=SimpleNamespace(context=SimpleNamespace()),
            ),
            patch("msm.api.accounts.Account", fake_account),
            patch("msm.repositories.crud.compile_markets_statement", offline_compile),
            patch(
                "msm.repositories.crud.execute_markets_operation",
                side_effect=lambda operation, context: operations.append(operation) or {},
            ),
            patch(
                "alpaca_connectors.holdings.services.resolve_complete_account_holdings_rows",
                return_value=[],
            ),
            patch(
                "alpaca_connectors.holdings.services.publish_resolved_account_holdings_snapshot",
                return_value=SimpleNamespace(holdings_rows=0, unresolved_symbols=[]),
            ),
            patch(
                "alpaca_connectors.account.services._previous_legacy_references", return_value=None
            ),
        ):
            register_alpaca_account(
                credentials=SUBMITTED,
                paper=True,
                client=client,
                asset_resolver=lambda position: None,
            )

        (operation,) = operations
        serialized = str(operation.model_dump(mode="json"))
        self.assertNotIn("PKSUBMITTED", serialized)
        self.assertNotIn("SKSUBMITTED", serialized)
        parameters = operation.statement.parameters
        sealed = [parameters["api_key"], parameters["secret_key"]]
        self.assertTrue(all(is_envelope(value) for value in sealed))
        self.assertFalse(is_envelope(parameters["api_key_fingerprint"]))
        self.assertIn("ON CONFLICT", operation.statement.sql.upper())


class AccountCredentialUpdateTests(unittest.TestCase):
    def _update(self, current, *, alpaca_account_id="expected-alpaca-id", **kwargs):
        candidate_client = SimpleNamespace(
            get_account=lambda: SimpleNamespace(id=alpaca_account_id)
        )
        with (
            patch(
                "alpaca_connectors.account.services.get_account_registration", return_value=current
            ),
            patch(
                "alpaca_connectors.runtime.start_markets_engine",
                return_value=SimpleNamespace(context="context"),
            ),
            patch("msm.api.accounts.Account"),
            patch(
                "alpaca_connectors.account.services.build_alpaca_trading_client",
                return_value=candidate_client,
            ) as build_client,
            _imported(ResolvedAlpacaCredentials(api_key="PKNEW", secret_key="SKNEW")),
            patch("alpaca_connectors.account.services.write_stored_credentials") as write,
            patch(
                "alpaca_connectors.account.services.delete_legacy_managed_secrets",
                return_value=([], []),
            ) as delete_legacy,
        ):
            try:
                outcome: object = update_account_registration("acc-uid-1", **kwargs)
            except Exception as exc:  # noqa: BLE001 - returned for assertions
                outcome = exc
        return outcome, build_client, write, delete_legacy

    def test_managed_rotation_validates_then_replaces_the_stored_keys(self) -> None:
        _, build_client, write, delete_legacy = self._update(
            _stub_registration(),
            credential_values=SUBMITTED,
            actor_user_uid="user-uid",
        )

        build_client.assert_called_once_with(credentials=SUBMITTED, paper=True)
        values = write.call_args.kwargs["values"]
        self.assertEqual(values["api_key"], "PKSUBMITTED")
        self.assertEqual(values["credential_source"], CREDENTIAL_SOURCE_MANAGED)
        self.assertEqual(values["api_key_fingerprint"], api_key_fingerprint("PKSUBMITTED"))
        self.assertEqual(values["credentials_updated_by_user_uid"], "user-uid")
        delete_legacy.assert_not_called()

    def test_rotating_a_legacy_registration_deletes_its_managed_secrets(self) -> None:
        _, _, write, delete_legacy = self._update(
            _legacy_registration(),
            credential_values=SUBMITTED,
        )

        values = write.call_args.kwargs["values"]
        for column in LEGACY_SECRET_REFERENCE_COLUMNS:
            self.assertIsNone(values[column])
        delete_legacy.assert_called_once()
        self.assertEqual(delete_legacy.call_args.args[0], _legacy_managed_references())

    def test_rotation_to_another_alpaca_account_writes_nothing(self) -> None:
        outcome, _, write, _ = self._update(
            _stub_registration(),
            alpaca_account_id="someone-else",
            credential_values=SUBMITTED,
        )

        self.assertIsInstance(outcome, ValueError)
        self.assertIn("different Alpaca account", str(outcome))
        write.assert_not_called()

    def test_importing_from_secrets_records_the_imported_source(self) -> None:
        _, _, write, _ = self._update(
            _stub_registration(),
            credential_secret_names=AlpacaSecretNames("NEW_API_KEY", "NEW_SECRET_KEY"),
        )

        values = write.call_args.kwargs["values"]
        self.assertEqual(values["credential_source"], CREDENTIAL_SOURCE_IMPORTED)
        self.assertEqual(values["api_key"], "PKNEW")
        self.assertNotIn("NEW_API_KEY", str(values))

    def test_credential_values_and_secret_names_cannot_be_combined(self) -> None:
        outcome, build_client, write, _ = self._update(
            _stub_registration(),
            credential_values=SUBMITTED,
            credential_secret_names=AlpacaSecretNames("NEW_API_KEY", "NEW_SECRET_KEY"),
        )

        self.assertIsInstance(outcome, ValueError)
        build_client.assert_not_called()
        write.assert_not_called()


class AccountRemovalTests(unittest.TestCase):
    def _remove(self, current):
        with (
            patch(
                "alpaca_connectors.account.services.get_account_registration", return_value=current
            ),
            patch(
                "alpaca_connectors.operations.signal_job_configurations.signal_job_configurations_for_account",
                return_value=[],
            ),
            patch(
                "alpaca_connectors.runtime.start_markets_engine",
                return_value=SimpleNamespace(context=None),
            ),
            patch("msm.api.accounts.Account"),
            patch("msm.api.accounts.AccountHoldingsSet") as holdings_sets,
            patch("msm.repositories.crud.delete_model") as delete_model,
            patch(
                "alpaca_connectors.account.services.delete_legacy_managed_secrets",
                return_value=(["A", "B"], []),
            ) as delete_legacy,
        ):
            holdings_sets.filter.return_value = []
            result = remove_account_registration("acc-uid-1")
        return result, delete_model, delete_legacy

    def test_removing_a_registration_deletes_the_row_and_nothing_else(self) -> None:
        result, delete_model, delete_legacy = self._remove(_stub_registration())

        delete_model.assert_called_once()
        self.assertEqual(delete_model.call_args.kwargs["uid"], "acc-uid-1")
        delete_legacy.assert_not_called()
        self.assertEqual(result["deleted_secrets"], [])
        self.assertTrue(result["registration_removed"])

    def test_removing_a_legacy_registration_deletes_its_managed_secrets(self) -> None:
        result, _, delete_legacy = self._remove(_legacy_registration())

        delete_legacy.assert_called_once_with(_legacy_managed_references())
        self.assertEqual(result["deleted_secrets"], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
