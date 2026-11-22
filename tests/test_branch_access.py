"""Every workload of this branch gets its roles from the migration (ADR 0030)."""

from __future__ import annotations

from alpaca_connectors.settings import BRANCH_ACCESS, PROJECT_NAMESPACE


def test_the_branch_holds_editor_on_its_one_namespace() -> None:
    # Markets reads no table of this application, so there is no second namespace (ADR 0031).
    assert BRANCH_ACCESS == {PROJECT_NAMESPACE: "editor"}


def test_the_one_provider_declares_the_branch_access() -> None:
    # A provider's declaration replaces the branch's previous one (MetaTables ADR 0029). Since
    # ADR 0029 this application has one migration provider, so it declares everything.
    from alpaca_connectors.migrations import migration

    assert migration.branch_access == BRANCH_ACCESS
