"""`alpaca-connectors instances grant-markets-access` (ADR 0025)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from alpaca_connectors.operations import markets_access

MARKETS = "11111111-1111-4111-8111-111111111111"
TEAM = "22222222-2222-4222-8222-222222222222"


class _Namespace:
    def __init__(self, bindings=()):
        self.name = "alpaca-connectors-markets"
        self.bindings = list(bindings)
        self.granted: list[tuple[str, str, str]] = []

    def get_access(self):
        return {"bindings": self.bindings}

    def grant_role(self, principal_uid, role, *, principal_kind="user"):
        self.granted.append((principal_kind, principal_uid, role))


@pytest.fixture
def catalog(monkeypatch):
    state = SimpleNamespace(namespace=None, created=[])

    def create(name, *, description=None):
        state.created.append(name)
        state.namespace = _Namespace()
        return state.namespace

    import metatables.models

    monkeypatch.setattr(metatables.models.Namespace, "create", staticmethod(create))
    monkeypatch.setattr(markets_access, "_find_namespace", lambda name: state.namespace)
    monkeypatch.setattr(
        markets_access,
        "_instance_memberships",
        lambda: {"alpaca_connectors__ordersts__main": False},
    )
    return state


def test_dry_run_changes_nothing(catalog) -> None:
    report = markets_access.grant_markets_access(markets_workload_uid=MARKETS)

    assert report["executed"] is False
    assert report["namespace_exists"] is False
    assert catalog.created == []
    assert report["grants"] == [
        {
            "principal_kind": "user",
            "principal_uid": MARKETS,
            "role": "data_viewer",
            "role_before": None,
            "granted": False,
        }
    ]
    assert report["instances_missing_from_namespace"] == ["alpaca_connectors__ordersts__main"]


def test_execute_creates_the_namespace_and_grants(catalog) -> None:
    report = markets_access.grant_markets_access(
        markets_workload_uid=MARKETS, editor_team_uid=TEAM, execute=True
    )

    assert catalog.created == ["alpaca-connectors-markets"]
    assert report["namespace_created"] is True
    assert catalog.namespace.granted == [
        ("user", MARKETS, "data_viewer"),
        ("team", TEAM, "editor"),
    ]
    assert [grant["granted"] for grant in report["grants"]] == [True, True]


def test_a_role_already_held_is_not_granted_again(catalog) -> None:
    catalog.namespace = _Namespace(
        [{"principal_kind": "user", "principal_uid": MARKETS, "role": "data_viewer"}]
    )

    report = markets_access.grant_markets_access(markets_workload_uid=MARKETS, execute=True)

    assert catalog.created == []
    assert catalog.namespace.granted == []
    assert report["grants"][0]["role_before"] == "data_viewer"
    assert report["grants"][0]["granted"] is False


def test_a_malformed_uid_is_refused(catalog) -> None:
    with pytest.raises(ValueError):
        markets_access.grant_markets_access(markets_workload_uid="not-a-uid", execute=True)
    assert catalog.created == []


def test_the_deploy_report_lists_the_instances_markets_cannot_read(monkeypatch) -> None:
    monkeypatch.setattr(
        markets_access,
        "_instance_memberships",
        lambda: {
            "alpaca_connectors__ordersts__main": True,
            "alpaca_connectors__signalweightsts__main": False,
        },
    )

    assert markets_access.markets_instances_report() == {
        "checked": True,
        "namespace": "alpaca-connectors-markets",
        "instances_missing_from_namespace": ["alpaca_connectors__signalweightsts__main"],
    }


def test_the_deploy_report_never_fails_the_deploy(monkeypatch) -> None:
    def unreadable():
        raise PermissionError("the catalog refused")

    monkeypatch.setattr(markets_access, "_instance_memberships", unreadable)

    assert markets_access.markets_instances_report() == {
        "checked": False,
        "error": "PermissionError",
    }


def test_an_instance_with_no_table_yet_counts_as_unreadable(monkeypatch) -> None:
    import alpaca_connectors.instances
    import metatables.models

    models = [SimpleNamespace(__table__=SimpleNamespace(name=name)) for name in ("b_ts", "a_ts")]
    monkeypatch.setattr(alpaca_connectors.instances, "project_instance_models", lambda: models)
    shared = SimpleNamespace(
        physical_table_name="a_ts",
        namespaces=[SimpleNamespace(name="alpaca-connectors-markets")],
    )
    monkeypatch.setattr(
        metatables.models.MetaTable, "filter_by_body", staticmethod(lambda **kwargs: [shared])
    )

    assert markets_access._instance_memberships() == {"a_ts": True, "b_ts": False}
