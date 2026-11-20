"""Give the Markets workloads their read access on this application's contract instances.

Every Markets contract instance is also in ``alpaca-connectors-markets`` (ADR 0025). The migration
Job creates that namespace when it first declares the instances there, and its branch declaration
gives this repository's workloads their role on it (ADR 0030). The Markets workloads belong to
another repository, and giving them access is sharing, which stays with people (MetaTables
ADR 0029): a person who owns the namespace, or an Organization admin, grants the
``MainSequenceMarkets`` branch ``data_viewer`` on it once per Environment. Every Markets workload
of that branch keeps the role through its renames and redeployments.
"""

from __future__ import annotations

import uuid
from typing import Any

from alpaca_connectors.settings import MARKETS_INSTANCES_NAMESPACE

MARKETS_ROLE = "data_viewer"
MARKETS_PRINCIPAL_KIND = "branch"


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


def grant_markets_access(*, markets_branch_uid: str, execute: bool = False) -> dict[str, Any]:
    """Report, and with ``execute`` apply, the Markets branch's role on the Markets namespace.

    The namespace is the migration Job's to create: before the first deployment in an
    Environment there is nothing to grant on, and the report says so.
    """
    principal_uid = str(uuid.UUID(str(markets_branch_uid)))
    namespace = _find_namespace(MARKETS_INSTANCES_NAMESPACE)
    if namespace is None:
        return {
            "succeeded": False,
            "executed": execute,
            "namespace": MARKETS_INSTANCES_NAMESPACE,
            "namespace_exists": False,
            "detail": (
                f"{MARKETS_INSTANCES_NAMESPACE} does not exist in this Environment yet; the "
                "migration Job creates it on the first deployment."
            ),
        }

    current = _bound_role(namespace, MARKETS_PRINCIPAL_KIND, principal_uid)
    granted = False
    if execute and current != MARKETS_ROLE:
        namespace.grant_role(principal_uid, MARKETS_ROLE, principal_kind=MARKETS_PRINCIPAL_KIND)
        granted = True

    memberships = _instance_memberships()
    return {
        "succeeded": True,
        "executed": execute,
        "namespace": MARKETS_INSTANCES_NAMESPACE,
        "namespace_exists": True,
        "grant": {
            "principal_kind": MARKETS_PRINCIPAL_KIND,
            "principal_uid": principal_uid,
            "role": MARKETS_ROLE,
            "role_before": current,
            "granted": granted,
        },
        "instances_in_namespace": memberships,
        "instances_missing_from_namespace": sorted(
            name for name, present in memberships.items() if not present
        ),
    }


__all__ = ["MARKETS_ROLE", "grant_markets_access", "markets_instances_report"]
