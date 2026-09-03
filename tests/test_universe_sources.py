from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import src.universes.sources as universe_sources
from src.universes.sources import (
    UniverseSourceTable,
    load_default_universe_sources,
    normalize_source_values,
)


class UniverseSourceTests(unittest.TestCase):
    def test_contract_uses_uuid_identity_and_no_fund_or_portfolio_foreign_keys(self) -> None:
        table = UniverseSourceTable.__table__
        self.assertEqual([column.name for column in table.primary_key], ["uid"])
        self.assertEqual(
            {column.name for column in table.columns},
            {"uid", "name", "symbol", "source_url", "enabled", "created_at", "updated_at"},
        )
        self.assertEqual(list(table.foreign_keys), [])

    def test_source_values_normalize_symbol_and_require_absolute_url(self) -> None:
        values = normalize_source_values(
            name=" Index source ",
            symbol=" ivv ",
            source_url=load_default_universe_sources()[0]["source_url"],
        )
        self.assertEqual(values["name"], "Index source")
        self.assertEqual(values["symbol"], "IVV")
        with self.assertRaisesRegex(ValueError, "absolute HTTP"):
            normalize_source_values(name="Bad", symbol="BAD", source_url="relative/path")

    def test_packaged_default_is_data_with_stable_uuid(self) -> None:
        defaults = load_default_universe_sources()
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["symbol"], "IVV")
        self.assertEqual(str(defaults[0]["uid"]), "28f98530-9380-4a3b-b70e-3da8b972c205")
        self.assertEqual(
            defaults[0]["source_url"],
            "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf",
        )

    def test_seed_reconciles_existing_row_by_stable_uuid(self) -> None:
        desired = load_default_universe_sources()[0]
        existing = SimpleNamespace(
            **{
                **desired,
                "source_url": "https://example.com/obsolete-source",
            }
        )
        updated = SimpleNamespace(**desired)

        with (
            patch.object(universe_sources, "load_default_universe_sources", return_value=[desired]),
            patch.object(universe_sources, "get_universe_source", return_value=existing),
            patch.object(universe_sources, "create_universe_source") as create_source,
            patch.object(
                universe_sources,
                "update_universe_source",
                return_value=updated,
            ) as update_source,
        ):
            seeded = universe_sources.seed_default_universe_sources()

        self.assertEqual(seeded, [updated])
        create_source.assert_not_called()
        update_source.assert_called_once_with(
            desired["uid"],
            name=desired["name"],
            symbol=desired["symbol"],
            source_url=desired["source_url"],
            enabled=desired["enabled"],
        )

    def test_seed_does_not_write_when_existing_row_matches(self) -> None:
        desired = load_default_universe_sources()[0]
        existing = SimpleNamespace(**desired)

        with (
            patch.object(universe_sources, "load_default_universe_sources", return_value=[desired]),
            patch.object(universe_sources, "get_universe_source", return_value=existing),
            patch.object(universe_sources, "create_universe_source") as create_source,
            patch.object(universe_sources, "update_universe_source") as update_source,
        ):
            seeded = universe_sources.seed_default_universe_sources()

        self.assertEqual(seeded, [existing])
        create_source.assert_not_called()
        update_source.assert_not_called()


if __name__ == "__main__":
    unittest.main()
