from __future__ import annotations

import unittest

from src.account.alpaca_account_details import (
    ACCOUNT_BALANCE_NUMERIC_COLUMNS,
    AlpacaAccountDetails,
    project_account_models,
)


class AccountStorageTests(unittest.TestCase):
    def test_details_keyed_one_to_one_on_account_uid(self) -> None:
        table = AlpacaAccountDetails.__table__
        self.assertEqual([c.name for c in table.primary_key.columns], ["account_uid"])
        fk_targets = {fk.target_fullname for fk in table.foreign_keys}
        self.assertTrue(any(t.endswith("account.uid") for t in fk_targets), fk_targets)

    def test_details_has_static_metadata_and_config_columns(self) -> None:
        table = AlpacaAccountDetails.__table__
        for column in (
            "alpaca_account_id",
            "account_number",
            "api_key_fingerprint",
            "api_key_secret_name",
            "secret_key_secret_name",
            "is_paper",
            "status",
            "multiplier",
            "pattern_day_trader",
            "options_trading_level",
            "dtbp_check",
            "pdt_check",
            "fractional_trading",
            "snapshot_time",
            "raw_account_payload",
        ):
            self.assertIn(column, table.columns, f"missing {column}")

    def test_details_has_current_financials_as_numeric_columns(self) -> None:
        # Current account financials live on the detail row (no bespoke time-series table); the
        # canonical point-in-time store for positions + cash is the ms-markets AccountHoldingsStorage.
        table = AlpacaAccountDetails.__table__
        for column in ACCOUNT_BALANCE_NUMERIC_COLUMNS:
            self.assertIn(column, table.columns, f"missing {column}")
            self.assertIn("NUMERIC", str(table.columns[column].type).upper())
        for column in ("effective_buying_power", "bod_dtbp", "position_market_value"):
            self.assertIn(column, table.columns)
        self.assertIn("daytrade_count", table.columns)
        self.assertIn("balance_asof", table.columns)

    def test_project_account_models_is_only_the_detail_table(self) -> None:
        self.assertEqual([m.__name__ for m in project_account_models()], ["AlpacaAccountDetails"])

    def test_no_bespoke_balances_storage_table_exists(self) -> None:
        import src.account.alpaca_account_details as module

        self.assertFalse(hasattr(module, "AlpacaAccountBalancesStorage"))
        self.assertFalse(hasattr(module, "AlpacaAccountBalancesNode"))


if __name__ == "__main__":
    unittest.main()
