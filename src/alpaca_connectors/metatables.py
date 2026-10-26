"""Project-owned ms-markets MetaTable mixins for alpaca-connectors.

These register every project-owned table in this project's MetaTables namespace,
``alpaca-connectors`` (ms-markets ADR 0047, decision 4), and set the physical-table *app segment*
so tables are named ``alpaca_connectors__<table>`` instead of the library default
``ms_markets__<table>``. ms-markets prefixes each authored identifier with the namespace
(``alpaca-connectors.<identifier>``); physical names, schema and constraint naming are unchanged.
``MSM_AUTO_REGISTER_NAMESPACE``, when set, replaces the declared namespace (tests and examples).

Inheriting ``mainsequence.markets`` from the ms-markets mixins was the extension defect ADR 0047
withdraws: it made these tables Markets tables, so the remote row backend would have sent their
row calls to the Markets API.

Use:
- ``AlpacaMarketsMetaTableMixin`` for regular (non-time-indexed) MetaTables (e.g. account detail
  sidecars).
- ``AlpacaMarketsTimeIndexMetaTableMixin`` for storage-first time-indexed DataNode storage classes.
- ``ProjectStorageNameMixin`` (optional, placed *before* the Markets mixin in the base list) to give
  a short explicit physical ``__tablename__`` from ``__project_storage_concept__`` instead of the
  SDK's namespaced-identifier-derived name.
"""

from __future__ import annotations

from typing import ClassVar

from msm.base import MarketsMetaTableMixin, MarketsTimeIndexMetaTableMixin
from msm.settings import markets_auto_register_namespace, markets_configured_namespace
from sqlalchemy.orm import declared_attr

from alpaca_connectors.settings import PROJECT_NAMESPACE, PROJECT_NAMESPACE_SLUG
from metatables import schema_table_name

# Physical-table app segment for this project's MetaTables.
PROJECT_TABLE_APP = PROJECT_NAMESPACE_SLUG


class AlpacaMarketsMetaTableMixin(MarketsMetaTableMixin):
    """Regular markets MetaTable mixin in this project's namespace and table-name app segment."""

    __abstract__ = True
    __metatable_namespace__: ClassVar[str] = markets_configured_namespace(PROJECT_NAMESPACE)
    __markets_storage_app__: ClassVar[str] = PROJECT_TABLE_APP


class AlpacaMarketsTimeIndexMetaTableMixin(MarketsTimeIndexMetaTableMixin):
    """Time-indexed (storage-first) markets MetaTable mixin in this project's namespace."""

    __abstract__ = True
    __metatable_namespace__: ClassVar[str] = markets_configured_namespace(PROJECT_NAMESPACE)
    __markets_storage_app__: ClassVar[str] = PROJECT_TABLE_APP


class ProjectStorageNameMixin:
    """Override the physical ``__tablename__`` with a short explicit concept name.

    Placed *before* the Markets mixin in the base class list so it wins MRO. Leaves logical
    ``__metatable_identifier__`` (catalog identity) untouched.
    """

    __project_storage_concept__: ClassVar[str]

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805 - SQLAlchemy declared_attr receives the class
        concept = cls.__project_storage_concept__
        cadence = getattr(cls, "__cadence__", None)
        if cadence:
            concept = f"{concept}_{cadence}"
        return schema_table_name(
            PROJECT_TABLE_APP,
            concept,
            suffix=markets_auto_register_namespace(),
        )


__all__ = [
    "PROJECT_NAMESPACE",
    "PROJECT_TABLE_APP",
    "AlpacaMarketsMetaTableMixin",
    "AlpacaMarketsTimeIndexMetaTableMixin",
    "ProjectStorageNameMixin",
]
