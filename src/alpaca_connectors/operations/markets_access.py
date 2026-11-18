"""Give the Markets workload its read access on this application's contract instances (ADR 0025).

Every Markets contract instance is also in ``alpaca-connectors-markets``. A person who owns that
namespace, or an Organization admin, runs this once per Environment: it creates the namespace
when it does not exist yet, grants the Markets workload ``data_viewer`` on it, and can grant a
Team ``editor`` on it so that Team's migration Job may add the instances to it.
"""

from __future__ import annotations

import uuid
from typing import Any

from alpaca_connectors.settings import MARKETS_INSTANCES_NAMESPACE

MARKETS_ROLE = "data_viewer"
MIGRATION_ROLE = "editor"
NAMESPACE_DESCRIPTION = (
    "The Markets time-series contract instances of alpaca-connectors, which the Markets "
    "workload reads."
)


def _find_namespace(name: str) -> Any | None:
    from metatables.models import Namespace

    return next((item for item in Namespace.filter(search=name) if item.name == name), None)


def _bound_role(namespace: Any, principal_kind: str, principal_uid: str) -> str | None:
    for binding in namespace.get_access().get("bindings") or []:
        if (
            binding.get("principal_kind") == principal_kind
            and str(binding.get("principal_uid")) == principal_uid
        ):
            return binding.get("role")
    return None


def _instance_memberships() -> dict[str, bool]:
    from alpaca_connectors.instances import project_instance_models
    from metatables.models import MetaTable

    names = sorted(model.__table__.name for model in project_instance_models())
    rows = MetaTable.filter_by_body(physical_table_name__in=names, limit=max(len(names) * 2, 1))
    # An instance with no table yet is not readable either.
    memberships = dict.fromkeys(names, False)
    for row in rows:
        memberships[row.physical_table_name] = any(
            item.name == MARKETS_INSTANCES_NAMESPACE for item in row.namespaces
        )
    return dict(sorted(memberships.items()))


def markets_instances_report() -> dict[str, Any]:
    """The contract instances Markets cannot read yet, for the migration Job's log (issue #37).

    An instance outside ``alpaca-connectors-markets`` is invisible to the Markets workload, so the
    first call that needs it would be refused; the deploy says so first. Reported, never raised:
    the release still rolls out, and the runbook's grant fixes it (ADR 0025).
    """
    try:
        memberships = _instance_memberships()
    except Exception as exc:  # noqa: BLE001 - a report never fails the deploy
        return {"checked": False, "error": type(exc).__name__}
    return {
        "checked": True,
        "namespace": MARKETS_INSTANCES_NAMESPACE,
        "instances_missing_from_namespace": sorted(
            name for name, present in memberships.items() if not present
        ),
    }


def grant_markets_access(
    *,
    markets_workload_uid: str,
    editor_team_uid: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Report, and with ``execute`` apply, the roles on ``alpaca-connectors-markets``."""
    from metatables.models import Namespace

    wanted: list[tuple[str, str, str]] = [
        ("user", str(uuid.UUID(str(markets_workload_uid))), MARKETS_ROLE)
    ]
    if editor_team_uid:
        wanted.append(("team", str(uuid.UUID(str(editor_team_uid))), MIGRATION_ROLE))

    namespace = _find_namespace(MARKETS_INSTANCES_NAMESPACE)
    created = False
    if namespace is None and execute:
        namespace = Namespace.create(MARKETS_INSTANCES_NAMESPACE, description=NAMESPACE_DESCRIPTION)
        created = True

    grants: list[dict[str, Any]] = []
    for principal_kind, principal_uid, role in wanted:
        current = (
            None if namespace is None else _bound_role(namespace, principal_kind, principal_uid)
        )
        granted = False
        if execute and current != role:
            namespace.grant_role(principal_uid, role, principal_kind=principal_kind)
            granted = True
        grants.append(
            {
                "principal_kind": principal_kind,
                "principal_uid": principal_uid,
                "role": role,
                "role_before": current,
                "granted": granted,
            }
        )

    memberships = _instance_memberships()
    return {
        "succeeded": True,
        "executed": execute,
        "namespace": MARKETS_INSTANCES_NAMESPACE,
        "namespace_exists": namespace is not None,
        "namespace_created": created,
        "grants": grants,
        "instances_in_namespace": memberships,
        "instances_missing_from_namespace": sorted(
            name for name, present in memberships.items() if not present
        ),
    }


__all__ = ["MARKETS_ROLE", "MIGRATION_ROLE", "grant_markets_access", "markets_instances_report"]
