"""Every workload of this branch gets its roles from the migration (ADR 0030)."""

from __future__ import annotations

from alpaca_connectors.settings import BRANCH_ACCESS, PROJECT_NAMESPACE


def test_the_branch_holds_editor_on_its_one_namespace() -> None:
    # Markets reads no table of this application, so there is no second namespace (ADR 0031).
    assert BRANCH_ACCESS == {PROJECT_NAMESPACE: "editor"}


def test_every_provider_declares_the_same_branch_access(monkeypatch) -> None:
    # A provider's declaration replaces the branch's previous one, so a provider declaring less
    # would remove what the other declared (MetaTables ADR 0029).
    import importlib
    import sys

    from alpaca_connectors.migrations import migration
    from alpaca_connectors.portfolios import interpolated_prices_schema

    # The interpolated-prices provider builds its models from the environment the
    # prepare-interpolated-prices command sets; none are needed to read its declaration.
    monkeypatch.setattr(interpolated_prices_schema, "dynamic_storage_models_from_env", list)
    name = "alpaca_connectors.portfolios.interpolated_prices_migration"
    monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        interpolated = importlib.import_module(name)
        assert interpolated.migration.branch_access == BRANCH_ACCESS
    finally:
        sys.modules.pop(name, None)

    assert migration.branch_access == BRANCH_ACCESS
