"""Writes to this application's own tables go as one MetaTables transaction (issue #46)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from alpaca_connectors.portfolios import configurations
from alpaca_connectors.portfolios.configurations import (
    STATEMENT_LIST_LIMIT,
    AlpacaPortfolioConfigurationTable,
    EndedPortfolioTable,
    OwnTableWrites,
    ended_portfolio_rows,
)

DIALECTS = {"postgresql": postgresql.dialect(), "sqlite": sqlite.dialect()}


def _sent(writes: OwnTableWrites, dialect: str = "postgresql") -> list[list]:
    """Run the writes; answer each transaction's statements, as ``run`` sends them."""
    transactions: list[list] = []

    def compile_list(statements, **kwargs):
        assert kwargs["operation"] == "delete" and kwargs["dialect"] == dialect
        return list(statements)

    with (
        patch.object(configurations, "_start_runtime"),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context="c")),
        patch("msm.repositories.base.resolve_markets_sql_target", return_value=("source", dialect)),
        patch("msm.repositories.base.compile_markets_statements", side_effect=compile_list),
        patch(
            "msm.repositories.base.execute_markets_write",
            side_effect=lambda write, **kwargs: transactions.append(write),
        ),
    ):
        writes.run()
    return transactions


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
def test_the_writes_go_in_order_as_one_transaction(dialect: str) -> None:
    configuration = uuid.UUID(int=1)
    records = ended_portfolio_rows(
        [
            {
                "account_uid": uuid.UUID(int=2),
                "portfolio_uid": uuid.UUID(int=3),
                "portfolio_configuration_uid": configuration,
                "signal_uid": "signal",
            }
        ]
    )
    writes = OwnTableWrites()
    writes.insert_missing(EndedPortfolioTable, records, conflict_columns=("portfolio_uid",))
    writes.delete_in(AlpacaPortfolioConfigurationTable, "uid", [configuration])

    [statements] = _sent(writes, dialect)

    insert, delete = (str(s.compile(dialect=DIALECTS[dialect])) for s in statements)
    # A portfolio recorded already keeps its stored record; then the configuration goes.
    assert insert.startswith("INSERT INTO alpaca_connectors__ended_portfolio")
    assert "ON CONFLICT (portfolio_uid) DO NOTHING" in insert
    assert delete.startswith("DELETE FROM alpaca_connectors__portfolio_configuration")
    assert " IN (" in delete
    assert [kind for kind, *_ in writes.entries] == ["insert", "delete"]


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
def test_a_create_inserts_its_configuration_then_its_own_bars(dialect: str) -> None:
    """The provided-weights create's last write: two plain inserts, in one transaction."""
    from alpaca_connectors.market_data.configurations import (
        AlpacaBarsConfigurationTable,
        portfolio_bar_configuration_values,
    )
    from alpaca_connectors.portfolios.configurations import portfolio_configuration_values

    configuration = portfolio_configuration_values(
        uid=uuid.UUID(int=1),
        name="Thesis basket",
        account_uid=uuid.UUID(int=2),
        signal_definition_uid=uuid.UUID(int=3),
        rebalance_configuration_uid=uuid.UUID(int=4),
        owner_gateway_uid="owner",
        owner_user_uid="owner",
    )
    configuration["portfolio_uid"] = uuid.UUID(int=5)
    bars = portfolio_bar_configuration_values(
        portfolio_configuration_uid=configuration["uid"],
        name="Thesis basket prices",
        frequency_id="1d",
        feed="sip",
        adjustment="all",
    )
    writes = OwnTableWrites().insert(AlpacaPortfolioConfigurationTable, [configuration])
    writes.insert(AlpacaBarsConfigurationTable, [bars])

    [statements] = _sent(writes, dialect)

    first, second = (str(s.compile(dialect=DIALECTS[dialect])) for s in statements)
    assert first.startswith("INSERT INTO alpaca_connectors__portfolio_configuration ")
    assert second.startswith("INSERT INTO alpaca_connectors__bars_configuration ")
    assert "ON CONFLICT" not in first + second
    assert [entry[:2] for entry in writes.entries] == [
        ("insert", "AlpacaPortfolioConfigurationTable"),
        ("insert", "AlpacaBarsConfigurationTable"),
    ]


def test_nothing_to_write_sends_nothing() -> None:
    assert _sent(OwnTableWrites()) == []


def test_a_list_longer_than_a_transaction_holds_goes_as_several_in_order() -> None:
    writes = OwnTableWrites()
    for index in range(STATEMENT_LIST_LIMIT + 3):
        writes.delete_in(AlpacaPortfolioConfigurationTable, "uid", [uuid.UUID(int=index)])

    transactions = _sent(writes)

    assert [len(statements) for statements in transactions] == [STATEMENT_LIST_LIMIT, 3]


def test_extend_adds_the_other_writes_after_these() -> None:
    first = OwnTableWrites().delete_in(AlpacaPortfolioConfigurationTable, "uid", ["a"])
    second = OwnTableWrites().delete_in(EndedPortfolioTable, "portfolio_uid", ["b"])

    first.extend(second)

    assert [table for _, table, _, _ in first.entries] == [
        "AlpacaPortfolioConfigurationTable",
        "EndedPortfolioTable",
    ]
    assert len(first) == 2
