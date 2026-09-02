from __future__ import annotations

from typing import Any

from msm.base import MarketsBase

from mainsequence.meta_tables.migrations import build_metatable_model_registry


def _metatable_model_sources() -> list[type[Any]]:
    return [
        # Add project-owned MetaTable model classes here, or expand package
        # functions that return model classes.
    ]


def metatable_provider_models() -> list[type[Any]]:
    return build_metatable_model_registry(_metatable_model_sources(), base=MarketsBase)


__all__ = ["metatable_provider_models"]
