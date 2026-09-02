"""Project-owned ms-markets MetaTable mixins for alpaca-connectors.

These set the physical-table *app segment* to this project's namespace so project-owned tables are
named ``alpaca_connectors__<table>`` instead of the library default ``ms_markets__<table>``. Logical
identity (``__metatable_identifier__``), registration, schema, namespace, and constraint naming are
all inherited from the ms-markets base mixins unchanged.

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
from msm.settings import markets_auto_register_namespace
from sqlalchemy.orm import declared_attr

from mainsequence.meta_tables import schema_table_name
from src.settings import PROJECT_NAMESPACE_SLUG

# Physical-table app segment for this project's MetaTables.
PROJECT_TABLE_APP = PROJECT_NAMESPACE_SLUG


class AlpacaMarketsMetaTableMixin(MarketsMetaTableMixin):
    """Regular markets MetaTable mixin using this project's table-name app segment."""

    __abstract__ = True
    __markets_storage_app__: ClassVar[str] = PROJECT_TABLE_APP


class AlpacaMarketsTimeIndexMetaTableMixin(MarketsTimeIndexMetaTableMixin):
    """Time-indexed (storage-first) markets MetaTable mixin using this project's app segment."""

    __abstract__ = True
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
    "PROJECT_TABLE_APP",
    "AlpacaMarketsMetaTableMixin",
    "AlpacaMarketsTimeIndexMetaTableMixin",
    "ProjectStorageNameMixin",
]
