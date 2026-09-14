"""Tau tool adapter for the Alpaca Connectors CodeRepository agent."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from tau_agent.messages import TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

from src.agent_tools import PROJECT_AGENT_TOOLS, ProjectAgentTool


def _json_value(value: Any) -> JSONValue:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    elif isinstance(value, Enum):
        value = value.value
    elif isinstance(value, (dt.datetime, dt.date, dt.time)):
        value = value.isoformat()
    elif isinstance(value, (UUID, Decimal)):
        value = str(value)

    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _public_error(exc: Exception) -> dict[str, JSONValue]:
    if isinstance(exc, ValidationError):
        return {
            "code": "invalid_request",
            "message": "The tool arguments do not satisfy the project request contract.",
            "details": _json_value(exc.errors(include_url=False, include_input=False)),
            "retryable": False,
        }

    from api.app.errors import api_http_error

    http_error = api_http_error(exc)
    detail = _json_value(http_error.detail)
    if isinstance(detail, dict):
        return {
            "status_code": http_error.status_code,
            **detail,
        }
    return {
        "status_code": http_error.status_code,
        "code": "operation_failed",
        "message": str(detail),
        "retryable": False,
    }


def _tool_result(*, tool_name: str, result: Any) -> AgentToolResult:
    normalized = _json_value(result)
    details: dict[str, JSONValue] = {
        "ok": True,
        "tool": tool_name,
        "result": normalized,
    }
    return AgentToolResult(
        content=[TextContent(text=json.dumps(normalized, indent=2, sort_keys=True))],
        details=details,
    )


def _error_result(*, tool_name: str, exc: Exception) -> AgentToolResult:
    error = _public_error(exc)
    details: dict[str, JSONValue] = {
        "ok": False,
        "tool": tool_name,
        "error": error,
    }
    return AgentToolResult(
        content=[TextContent(text=json.dumps(error, indent=2, sort_keys=True))],
        details=details,
    )


def _tau_tool(definition: ProjectAgentTool) -> AgentTool:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, on_update
        if signal is not None and signal.is_cancelled():
            return AgentToolResult(
                content=[TextContent(text=f"{definition.label} was cancelled before execution.")],
                details={"ok": False, "tool": definition.name, "cancelled": True},
            )
        try:
            result = await asyncio.to_thread(definition.handler, dict(arguments))
        except Exception as exc:
            return _error_result(tool_name=definition.name, exc=exc)
        if signal is not None and signal.is_cancelled():
            return AgentToolResult(
                content=[
                    TextContent(
                        text=(
                            f"{definition.label} completed after cancellation was requested; "
                            "inspect the returned platform state before retrying."
                        )
                    )
                ],
                details={
                    "ok": True,
                    "tool": definition.name,
                    "cancelled_after_execution": True,
                    "result": _json_value(result),
                },
            )
        return _tool_result(tool_name=definition.name, result=result)

    return AgentTool(
        name=definition.name,
        label=definition.label,
        description=definition.description,
        parameters=definition.parameters,
        execute_fn=execute,
        execution_mode=definition.execution_mode,
    )


def setup(tau) -> None:
    """Register the repository's supported Alpaca operations with Tau."""

    for definition in PROJECT_AGENT_TOOLS:
        tau.register_tool(_tau_tool(definition))

