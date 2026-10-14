"""The Alpaca Connectors MCP server, served by the API at ``/mcp`` (ADR 0015).

The Alpaca Agent declares this API as its `alpaca` MCP application and reaches the project tools
here. ``create_app()`` installs it with ``mainsequence.server.fastapi.install_mcp`` after the
request identity, so only authenticated POSTs reach it and every tool runs with the call's verified
identity: the caller, and on an Agent's call the person it works for.

The server is stateless and answers JSON. It lists `alpaca_connectors.agent_tools`'s catalog with
the same names and input schemas, validates arguments against them, and runs each handler in a
worker thread that keeps the call's identity. Results and errors are JSON text; errors never echo
arguments, so no submitted value reaches a response, a log, or the model.

The SDK's skills extension publishes only ``.agents/skills/mcp_tools``. It owns discovery,
manifests and file resources; managed SDK/platform skills are not exposed by this application.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import anyio.to_thread
import jsonschema
from mcp import types
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ValidationError
from starlette.types import ASGIApp

from alpaca_connectors.agent_tools import PROJECT_AGENT_TOOLS, ProjectAgentTool
from mainsequence.server.mcp_skills import skills_extension

MCP_SERVER_NAME = "alpaca"
MCP_PATH = "/mcp"
MCP_SKILLS_DIRECTORY = ".agents/skills/mcp_tools"


def json_value(value: Any) -> Any:
    """Convert service results into JSON-compatible values."""
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
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def public_error(exc: Exception) -> dict[str, Any]:
    """The user-safe error the REST routes would answer, without any submitted input."""
    if isinstance(exc, ValidationError):
        return {
            "code": "invalid_request",
            "message": "The tool arguments do not satisfy the project request contract.",
            "details": json_value(exc.errors(include_url=False, include_input=False)),
            "retryable": False,
        }

    from .errors import api_http_error

    http_error = api_http_error(exc)
    detail = json_value(http_error.detail)
    if isinstance(detail, dict):
        return {"status_code": http_error.status_code, **detail}
    return {
        "status_code": http_error.status_code,
        "code": "operation_failed",
        "message": str(detail),
        "retryable": False,
    }


def _schema_error(error: jsonschema.ValidationError) -> dict[str, Any]:
    # The validator's own message quotes the rejected value; report only where and which rule.
    return {
        "code": "invalid_request",
        "message": "The tool arguments do not satisfy the tool's input schema.",
        "details": [
            {
                "path": [str(part) for part in error.absolute_path],
                "rule": str(error.validator),
            }
        ],
        "retryable": False,
    }


def _text_result(payload: Any, *, is_error: bool) -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(payload, indent=2, sort_keys=True, default=str),
            )
        ],
        is_error=is_error,
    )


def mcp_tool(definition: ProjectAgentTool) -> types.Tool:
    return types.Tool(
        name=definition.name,
        title=definition.label,
        description=definition.description,
        input_schema=definition.parameters,
        annotations=types.ToolAnnotations(
            title=definition.label,
            read_only_hint=definition.execution_mode == "parallel",
        ),
    )


async def call_project_tool(
    definition: ProjectAgentTool,
    arguments: Mapping[str, Any] | None,
) -> types.CallToolResult:
    """Validate and run one project tool in a worker thread that keeps the call's identity."""
    payload = dict(arguments or {})
    try:
        jsonschema.validate(instance=payload, schema=definition.parameters)
    except jsonschema.ValidationError as error:
        return _text_result(_schema_error(error), is_error=True)
    try:
        result = await anyio.to_thread.run_sync(definition.handler, payload)
    except Exception as exc:
        return _text_result(public_error(exc), is_error=True)
    return _text_result(json_value(result), is_error=False)


class _ProjectMCPServer(MCPServer):
    """Adapt the project's explicit schemas without inferring a second tool contract."""

    def __init__(self, tools: tuple[ProjectAgentTool, ...]) -> None:
        self._definitions = {definition.name: definition for definition in tools}
        super().__init__(
            MCP_SERVER_NAME,
            extensions=[skills_extension(MCP_SKILLS_DIRECTORY)],
        )

    async def list_tools(self) -> list[types.Tool]:
        return [mcp_tool(definition) for definition in self._definitions.values()]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context | None = None,
    ) -> types.CallToolResult:
        # Validate in call_project_tool so rejected inputs never appear in SDK error text.
        definition = self._definitions.get(name)
        if definition is None:
            return _text_result(
                {
                    "code": "unknown_tool",
                    "message": "The requested tool is not available.",
                    "retryable": False,
                },
                is_error=True,
            )
        return await call_project_tool(definition, arguments)


def build_mcp_server(
    tools: tuple[ProjectAgentTool, ...] = PROJECT_AGENT_TOOLS,
) -> MCPServer:
    return _ProjectMCPServer(tools)


def build_mcp_application(
    tools: tuple[ProjectAgentTool, ...] = PROJECT_AGENT_TOOLS,
) -> tuple[ASGIApp, Callable[[], AbstractAsyncContextManager[None]]]:
    """Return the MCP ASGI app answering at ``/mcp`` and its lifespan, for ``install_mcp``.

    Each application gets its own session manager, whose lifespan runs once.
    """
    server = build_mcp_server(tools)
    asgi_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        # The request identity checks Origin and the platform routes the host.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return asgi_app, server.session_manager.run


__all__ = [
    "MCP_PATH",
    "MCP_SERVER_NAME",
    "MCP_SKILLS_DIRECTORY",
    "build_mcp_application",
    "build_mcp_server",
    "call_project_tool",
    "json_value",
    "mcp_tool",
    "public_error",
]
