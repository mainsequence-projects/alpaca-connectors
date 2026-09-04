from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_CALL_NAMES = {
    "bulk_upsert_model",
    "create_model",
    "delete_model",
    "execute_markets_operation",
    "get_model_by_uid",
    "register_asset_fn",
    "register_alpaca_us_equity_assets",
    "ticker_and_optional_figi",
    "update_model",
    "upsert_model",
}
BACKEND_METHOD_NAMES = {
    "create",
    "delete",
    "filter",
    "get_all_assets",
    "get_asset",
    "get_by_uid",
    "get_by_unique_identifier",
    "run",
    "upsert",
}
BACKEND_ROW_TYPES = {
    "Account",
    "AccountHoldingsSet",
    "AlpacaAssetDetails",
    "AlpacaBarsConfiguration",
    "AlpacaETFSignalJobConfiguration",
    "Asset",
    "AssetCategory",
    "AssetCategoryMembership",
    "AssetRegistrationOperation",
    "AssetType",
    "OpenFigiDetails",
    "UniverseSource",
}


def _call_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _qualified_call_name(call: ast.Call) -> str:
    try:
        return ast.unparse(call.func)
    except Exception:
        return ""


def _per_iteration_nodes(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [*node.body, *node.orelse]
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return [
            node.elt,
            *(condition for generator in node.generators for condition in generator.ifs),
        ]
    if isinstance(node, ast.DictComp):
        return [
            node.key,
            node.value,
            *(condition for generator in node.generators for condition in generator.ifs),
        ]
    return []


def test_collection_loops_do_not_issue_backend_operations_per_item() -> None:
    violations: list[str] = []
    for source_root in (PROJECT_ROOT / "src", PROJECT_ROOT / "api"):
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for per_iteration_node in _per_iteration_nodes(node):
                    for child in ast.walk(per_iteration_node):
                        if not isinstance(child, ast.Call):
                            continue
                        name = _call_name(child)
                        qualified_name = _qualified_call_name(child)
                        row_method_call = (
                            name in {"create", "delete", "filter", "update", "upsert"}
                            and qualified_name.split(".", 1)[0] in BACKEND_ROW_TYPES
                        )
                        if (
                            name in BACKEND_CALL_NAMES
                            or name in BACKEND_METHOD_NAMES
                            or row_method_call
                        ):
                            violations.append(
                                f"{path.relative_to(PROJECT_ROOT)}:{child.lineno}: {name}"
                            )

    assert violations == [], (
        "Backend calls found inside collection loops; replace them with set-based queries, "
        f"bulk writes, or batched provider requests: {violations!r}"
    )
