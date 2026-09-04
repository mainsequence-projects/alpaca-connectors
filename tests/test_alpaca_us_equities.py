from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from requests import Timeout as RequestsTimeout

from src.assets.alpaca_asset_details import (
    asset_type_from_alpaca_class,
    build_alpaca_unique_identifier,
)
from src.assets.alpaca_us_equities import (
    AlpacaAssetRecord,
    AlpacaEquityClassificationPass,
    OpenFigiMatch,
    RegisteredAlpacaAssetReference,
    _query_existing_assets_by_alpaca_id,
    _register_alpaca_asset,
    _register_alpaca_assets_batch,
    _resolve_requested_symbols_to_alpaca_assets,
    build_alpaca_us_equity_registration_plan,
    build_alpaca_us_equity_trading_client,
    classify_alpaca_us_equities,
    fetch_alpaca_us_equities,
    register_alpaca_us_equity_assets,
    resolve_alpaca_us_equity_registration_plan,
)

AAPL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SPY_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
BRKB_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
BFB_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
HOLX_ID = uuid.UUID("c31e63a5-b5a2-417e-9ac3-278b4ff35cb5")


def alpaca_asset(
    symbol: str,
    asset_id: uuid.UUID,
    *,
    asset_class: str = "us_equity",
) -> AlpacaAssetRecord:
    return AlpacaAssetRecord(
        alpaca_asset_id=asset_id,
        symbol=symbol,
        name=f"{symbol} Name",
        exchange="NASDAQ",
        asset_class=asset_class,
        status="active",
        tradable=True,
        marginable=True,
        shortable=True,
        easy_to_borrow=True,
        fractionable=True,
    )


class AlpacaAssetRegistrationTests(unittest.TestCase):
    def test_batch_registration_uses_bulk_metatable_operations_and_one_snapshot_run(
        self,
    ) -> None:
        from msm.models import AssetTable, AssetTypeTable

        from src.assets.alpaca_asset_details import AlpacaAssetDetailsTable

        assets = [alpaca_asset("AAPL", AAPL_ID), alpaca_asset("SPY", SPY_ID)]
        asset_uids = {
            build_alpaca_unique_identifier(AAPL_ID): "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            build_alpaca_unique_identifier(SPY_ID): "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        }

        def bulk_upsert(_context, *, model, values, conflict_columns):
            if model is AssetTable:
                return {
                    "rows": [{**row, "uid": asset_uids[row["unique_identifier"]]} for row in values]
                }
            return {"rows": list(values)}

        snapshot = Mock()
        snapshot.set_snapshots.return_value = snapshot
        snapshot.run.return_value = (False, None)
        with (
            patch(
                "msm.bootstrap.resolve_runtime",
                return_value=SimpleNamespace(context="runtime-context"),
            ),
            patch(
                "msm.repositories.crud.bulk_upsert_model",
                side_effect=bulk_upsert,
            ) as bulk_upsert_model,
            patch("msm.data_nodes.assets.AssetSnapshot", return_value=snapshot),
        ):
            result = _register_alpaca_assets_batch(assets, {})

        self.assertEqual(
            result,
            {
                "AAPL": asset_uids[build_alpaca_unique_identifier(AAPL_ID)],
                "SPY": asset_uids[build_alpaca_unique_identifier(SPY_ID)],
            },
        )
        self.assertEqual(bulk_upsert_model.call_count, 3)
        type_call, asset_call, details_call = bulk_upsert_model.call_args_list
        self.assertIs(type_call.kwargs["model"], AssetTypeTable)
        self.assertEqual(len(type_call.kwargs["values"]), 1)
        self.assertIs(asset_call.kwargs["model"], AssetTable)
        self.assertEqual(len(asset_call.kwargs["values"]), 2)
        self.assertIs(details_call.kwargs["model"], AlpacaAssetDetailsTable)
        self.assertEqual(len(details_call.kwargs["values"]), 2)
        snapshot_rows = snapshot.set_snapshots.call_args.args[0]
        self.assertEqual(len(snapshot_rows), 2)
        self.assertEqual(len({row["time_index"] for row in snapshot_rows}), 1)
        snapshot.run.assert_called_once_with()

    def test_registration_execution_delegates_the_complete_plan_to_one_batch(self) -> None:
        assets = [alpaca_asset("AAPL", AAPL_ID), alpaca_asset("SPY", SPY_ID)]
        plan = classify_alpaca_us_equities(
            assets,
            classification_passes=(),
            query_openfigi_fn=lambda **_: ({}, {}),
        )
        existing_uid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        created_uid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        resolution = resolve_alpaca_us_equity_registration_plan(
            plan,
            query_existing_assets_fn=lambda *_args, **_kwargs: {
                str(AAPL_ID): Mock(uid=existing_uid)
            },
        )

        with patch(
            "src.assets.alpaca_us_equities._register_alpaca_assets_batch",
            return_value={"AAPL": existing_uid, "SPY": created_uid},
        ) as register_batch:
            result = register_alpaca_us_equity_assets(registration_resolution=resolution)

        register_batch.assert_called_once_with(assets, {})
        self.assertEqual(result["assets"], {"AAPL": existing_uid, "SPY": created_uid})
        self.assertEqual(result["existing_assets"], {"AAPL": existing_uid})
        self.assertEqual(result["created_assets"], {"SPY": created_uid})

    def test_registration_resolves_inactive_and_non_tradable_assets(self) -> None:
        active_non_tradable = SimpleNamespace(
            id=AAPL_ID,
            symbol="AAPL",
            name="Apple Inc.",
            exchange="NASDAQ",
            asset_class="us_equity",
            status="active",
            tradable=False,
            marginable=False,
            shortable=False,
            easy_to_borrow=False,
            fractionable=False,
            attributes=[],
        )
        inactive_non_tradable = SimpleNamespace(
            id=HOLX_ID,
            symbol="HOLX",
            name="Hologic, Inc. Common Stock",
            exchange="NASDAQ",
            asset_class="us_equity",
            status="inactive",
            tradable=False,
            marginable=False,
            shortable=False,
            easy_to_borrow=False,
            fractionable=False,
            attributes=[],
        )
        trading_client = Mock()
        trading_client.get_all_assets.return_value = [
            active_non_tradable,
            inactive_non_tradable,
        ]

        assets = fetch_alpaca_us_equities(
            trading_client=trading_client,
            symbols=["AAPL", "HOLX"],
        )

        self.assertEqual([asset.symbol for asset in assets], ["AAPL", "HOLX"])
        self.assertFalse(assets[0].tradable)
        self.assertEqual(assets[1].status, "inactive")
        self.assertFalse(assets[1].tradable)
        trading_client.get_all_assets.assert_called_once()
        trading_client.get_asset.assert_not_called()

        plan = build_alpaca_us_equity_registration_plan(
            account_uid="account-uid",
            trading_client=trading_client,
            symbols=["AAPL", "HOLX"],
            enrich_openfigi=False,
        )

        self.assertEqual([asset.symbol for asset in plan.alpaca_assets], ["AAPL", "HOLX"])
        self.assertEqual(plan.missing_symbols_from_alpaca, [])

    def test_bulk_identity_lookup_reuses_application_runtime(self) -> None:
        runtime = SimpleNamespace(context=object())
        with (
            patch("src.runtime.start_markets_engine", return_value=runtime) as start_engine,
            patch(
                "msm.repositories.base.compile_markets_statement",
                return_value="compiled-operation",
            ),
            patch(
                "msm.repositories.base.execute_markets_operation",
                return_value={"rows": []},
            ),
        ):
            result = _query_existing_assets_by_alpaca_id([AAPL_ID, SPY_ID])

        self.assertEqual(result, {})
        start_engine.assert_called_once_with()

    def test_registration_client_resolves_registered_account_secret_references(self) -> None:
        registration = {
            "uid": "account-uid",
            "api_key_secret_name": "ALPACA_PAPER_API_KEY",
            "secret_key_secret_name": "ALPACA_PAPER_SECRET_KEY",
            "is_paper": True,
        }
        client = Mock()
        with (
            patch(
                "src.account.services.get_account_registration",
                return_value=registration,
            ) as get_registration,
            patch(
                "src.account.services.build_registered_account_client",
                return_value=client,
            ) as build_client,
        ):
            result = build_alpaca_us_equity_trading_client(account_uid="account-uid")

        self.assertIs(result, client)
        get_registration.assert_called_once_with("account-uid")
        build_client.assert_called_once_with(registration)

    def test_registration_client_rejects_unknown_account(self) -> None:
        with (
            patch("src.account.services.get_account_registration", return_value=None),
            self.assertRaisesRegex(LookupError, "account registration account-uid does not exist"),
        ):
            build_alpaca_us_equity_trading_client(account_uid="account-uid")

    def test_requested_symbols_resolve_share_class_aliases(self) -> None:
        resolved, missing, aliases = _resolve_requested_symbols_to_alpaca_assets(
            alpaca_assets=[alpaca_asset("BRK.B", BRKB_ID), alpaca_asset("BF.B", BFB_ID)],
            requested_symbols=["BRKB", "BFB", "AAPL"],
        )

        self.assertEqual([asset.symbol for asset in resolved], ["BF.B", "BRK.B"])
        self.assertEqual(missing, ["AAPL"])
        self.assertEqual(aliases, {"BFB": "BF.B", "BRKB": "BRK.B"})

    def test_missing_figi_is_optional_enrichment_not_an_identity_blocker(self) -> None:
        def no_matches(**kwargs):
            return {}, {ticker: "No identifier found." for ticker in kwargs["tickers"]}

        plan = classify_alpaca_us_equities(
            [alpaca_asset("AAPL", AAPL_ID)],
            classification_passes=(
                AlpacaEquityClassificationPass(name="common_stock", security_type="Common Stock"),
            ),
            query_openfigi_fn=no_matches,
        )

        self.assertEqual(plan.openfigi_unmatched_symbols, ["AAPL"])
        self.assertEqual(plan.warnings_by_symbol, {"AAPL": "No identifier found."})
        self.assertEqual(plan.missing_symbols_from_alpaca, [])

    def test_openfigi_timeout_becomes_visible_optional_warning(self) -> None:
        def timeout(**_kwargs):
            raise RequestsTimeout("private transport details")

        plan = classify_alpaca_us_equities(
            [alpaca_asset("AAPL", AAPL_ID)],
            classification_passes=(
                AlpacaEquityClassificationPass(name="common_stock", security_type="Common Stock"),
            ),
            timeout=17,
            query_openfigi_fn=timeout,
        )

        self.assertEqual(plan.openfigi_unmatched_symbols, ["AAPL"])
        self.assertEqual(
            plan.warnings_by_symbol,
            {"AAPL": "OpenFIGI did not respond within the configured 17-second timeout."},
        )

    def test_resolution_checks_canonical_alpaca_ids(self) -> None:
        assets = [alpaca_asset("AAPL", AAPL_ID), alpaca_asset("SPY", SPY_ID)]
        plan = classify_alpaca_us_equities(
            assets,
            classification_passes=(),
            query_openfigi_fn=lambda **_: ({}, {}),
        )

        existing_asset = Mock(uid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        query = Mock(return_value={str(AAPL_ID): existing_asset})
        resolution = resolve_alpaca_us_equity_registration_plan(
            plan,
            query_existing_assets_fn=query,
        )

        self.assertEqual(query.call_args.args[0], [AAPL_ID, SPY_ID])
        self.assertEqual(
            resolution.existing_assets_by_alpaca_id,
            {str(AAPL_ID): str(existing_asset.uid)},
        )
        self.assertEqual([asset.symbol for asset in resolution.missing_assets], ["SPY"])

    def test_registration_uses_alpaca_uuid_and_optional_figi_details(self) -> None:
        asset_record = alpaca_asset("AAPL", AAPL_ID)
        match = OpenFigiMatch(
            symbol="AAPL",
            figi="BBG000B9XRY4",
            name="Apple Inc.",
            ticker="AAPL",
            exchange_code="US",
            security_type="Common Stock",
            security_type_2="Common Stock",
            security_market_sector="Equity",
            composite_figi="BBG000B9XRY4",
            share_class_figi="BBG001S5N8V8",
            classification_pass_name="common_stock",
        )
        registered = Mock(uid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        snapshot = Mock()
        snapshot.set_snapshots.return_value = snapshot
        snapshot.run.return_value = (False, None)

        with (
            patch("msm.api.assets.AssetType.upsert") as asset_type_upsert,
            patch("msm.api.assets.Asset.upsert", return_value=registered) as asset_upsert,
            patch(
                "src.assets.alpaca_asset_details.upsert_alpaca_asset_details"
            ) as alpaca_details_upsert,
            patch("msm.api.assets.OpenFigiDetails.upsert") as figi_details_upsert,
            patch("msm.data_nodes.assets.AssetSnapshot", return_value=snapshot),
        ):
            asset_uid = _register_alpaca_asset(asset_record, match)

        self.assertEqual(asset_uid, str(registered.uid))
        self.assertEqual(asset_type_upsert.call_args.kwargs["asset_type"], "equity")
        self.assertEqual(
            asset_upsert.call_args.kwargs["unique_identifier"],
            build_alpaca_unique_identifier(AAPL_ID),
        )
        self.assertEqual(
            alpaca_details_upsert.call_args.kwargs["values"]["alpaca_asset_id"], AAPL_ID
        )
        self.assertEqual(figi_details_upsert.call_args.kwargs["figi"], "BBG000B9XRY4")
        payload = snapshot.set_snapshots.call_args.args[0]
        self.assertEqual(payload["asset_identifier"], build_alpaca_unique_identifier(AAPL_ID))
        self.assertEqual(payload["ticker"], "AAPL")

    def test_figi_less_asset_is_still_registered(self) -> None:
        asset_record = alpaca_asset("AAPL", AAPL_ID)
        plan = classify_alpaca_us_equities(
            [asset_record],
            classification_passes=(),
            query_openfigi_fn=lambda **_: ({}, {}),
        )
        resolution = resolve_alpaca_us_equity_registration_plan(
            plan,
            query_existing_assets_fn=lambda *_args, **_kwargs: {},
        )
        asset_uid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        with patch(
            "src.assets.alpaca_us_equities._register_alpaca_assets_batch",
            return_value={"AAPL": asset_uid},
        ) as register_batch:
            result = register_alpaca_us_equity_assets(registration_resolution=resolution)

        register_batch.assert_called_once_with([asset_record], {})
        self.assertEqual(result["created_assets"], {"AAPL": asset_uid})
        self.assertEqual(result["openfigi_unmatched_symbols"], ["AAPL"])

    def test_missing_alpaca_symbol_blocks_execution_before_any_write(self) -> None:
        plan = classify_alpaca_us_equities(
            [alpaca_asset("AAPL", AAPL_ID)],
            classification_passes=(),
            query_openfigi_fn=lambda **_: ({}, {}),
        ).model_copy(update={"missing_symbols_from_alpaca": ["UNKNOWN"]})
        resolution = resolve_alpaca_us_equity_registration_plan(
            plan,
            query_existing_assets_fn=lambda *_args, **_kwargs: {},
            query_registered_symbols_fn=lambda *_args, **_kwargs: {},
        )
        with patch("src.assets.alpaca_us_equities._register_alpaca_assets_batch") as register_batch:
            with self.assertRaisesRegex(
                ValueError,
                "Alpaca did not resolve these symbols: UNKNOWN",
            ):
                register_alpaca_us_equity_assets(registration_resolution=resolution)

        register_batch.assert_not_called()

    def test_off_catalog_symbol_reuses_its_registered_canonical_alpaca_identity(self) -> None:
        plan = classify_alpaca_us_equities(
            [alpaca_asset("AAPL", AAPL_ID)],
            classification_passes=(),
            query_openfigi_fn=lambda **_: ({}, {}),
        ).model_copy(update={"missing_symbols_from_alpaca": ["HOLX"]})
        registered_uid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        registered_reference = RegisteredAlpacaAssetReference(
            asset_uid=registered_uid,
            unique_identifier=build_alpaca_unique_identifier(HOLX_ID),
            alpaca_asset_id=HOLX_ID,
            symbol="HOLX",
        )
        query_registered = Mock(return_value={"HOLX": registered_reference})

        resolution = resolve_alpaca_us_equity_registration_plan(
            plan,
            query_existing_assets_fn=lambda *_args, **_kwargs: {},
            query_registered_symbols_fn=query_registered,
        )

        self.assertEqual(resolution.unresolved_symbols_from_alpaca, [])
        self.assertEqual(
            resolution.existing_off_catalog_assets_by_symbol,
            {"HOLX": registered_reference},
        )
        query_registered.assert_called_once_with(["HOLX"], timeout=None)

        with patch(
            "src.assets.alpaca_us_equities._register_alpaca_assets_batch",
            return_value={"AAPL": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
        ):
            result = register_alpaca_us_equity_assets(registration_resolution=resolution)

        self.assertEqual(result["assets"]["HOLX"], registered_uid)
        self.assertEqual(result["existing_assets"]["HOLX"], registered_uid)
        self.assertEqual(result["not_registered_missing_alpaca_symbols"], [])

    def test_asset_type_is_derived_from_alpaca_class(self) -> None:
        self.assertEqual(asset_type_from_alpaca_class("us_equity"), "equity")
        self.assertEqual(asset_type_from_alpaca_class("crypto"), "crypto")
        with self.assertRaisesRegex(ValueError, "Unsupported Alpaca asset_class"):
            asset_type_from_alpaca_class("future")


if __name__ == "__main__":
    unittest.main()
