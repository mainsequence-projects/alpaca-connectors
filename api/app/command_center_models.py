from __future__ import annotations

from functools import lru_cache
from importlib import util as importlib_util
from importlib.metadata import distribution
from pathlib import Path
from types import ModuleType


@lru_cache(maxsize=1)
def _load_data_models_module() -> ModuleType:
    dist = distribution("mainsequence")
    relative = next(
        file
        for file in dist.files or []
        if str(file).endswith("mainsequence/client/command_center/data_models.py")
    )
    module_path = Path(dist.locate_file(relative))
    spec = importlib_util.spec_from_file_location(
        "mainsequence_command_center_data_models_fallback",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Could not load Command Center data models from {module_path}."
        )
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_data_models = _load_data_models_module()

for _model_name in (
    "TableFieldResponse",
    "SourceMetadataResponse",
    "DataNodeTableSourceInputResponse",
):
    getattr(_data_models, _model_name).model_rebuild(_types_namespace=_data_models.__dict__)

DataNodeTableSourceInputResponse = _data_models.DataNodeTableSourceInputResponse
SourceMetadataResponse = _data_models.SourceMetadataResponse
TableFieldResponse = _data_models.TableFieldResponse
