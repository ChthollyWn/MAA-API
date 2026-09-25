"""Typed tool definitions, schema export, argument validation, and dispatch."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode


class ToolRisk(StrEnum):
    """Static risk tier declared by a tool; CONDITIONAL is resolved by policy."""

    SAFE = "SAFE"
    CONDITIONAL = "CONDITIONAL"
    DANGEROUS = "DANGEROUS"


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Request-scoped dependencies and identity shared by a tool handler."""

    caller: CallerType
    session_id: str | None
    request_id: str | None
    request: Any
    db_session: Any
    scopes: tuple[str, ...] | None = None


ToolHandler = Callable[[BaseModel, ToolContext], Any]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Public metadata and callable implementation for one agent tool."""

    name: str
    group: str
    risk: ToolRisk
    description: str
    params_model: type[BaseModel]
    handler: ToolHandler

    def __post_init__(self) -> None:
        if not isinstance(self.risk, ToolRisk):
            try:
                object.__setattr__(self, "risk", ToolRisk(str(self.risk).upper()))
            except ValueError as exc:
                raise ValueError(f"unsupported risk tier: {self.risk!r}") from exc


class ToolRegistry:
    """In-memory source of truth for tool metadata and invocation."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """Register a unique tool name or raise for invalid metadata."""
        if not definition.name or not definition.name.strip():
            raise ValueError("tool name must not be empty")
        if not definition.group or not definition.group.strip():
            raise ValueError("tool group must not be empty")
        if not isinstance(definition.params_model, type) or not issubclass(
            definition.params_model, BaseModel
        ):
            raise TypeError("params_model must be a Pydantic BaseModel class")
        if not callable(definition.handler):
            raise TypeError("tool handler must be callable")
        if definition.name in self._definitions:
            raise ValueError(f"tool {definition.name!r} is already registered")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        """Return one registered definition, using the public tool error for misses."""
        try:
            return self._definitions[name]
        except KeyError:
            raise AppError(ErrorCode.TOOL_NOT_FOUND, f"未知工具：{name}") from None

    def list(self, group: str | None = None) -> list[ToolDefinition]:
        """Return definitions in registration order, optionally filtered by group."""
        return [
            definition
            for definition in self._definitions.values()
            if group is None or definition.group == group
        ]

    def export_schema(self, group: str | None = None) -> list[dict[str, Any]]:
        """Export tool metadata with each Pydantic input schema."""
        return [
            {
                "name": definition.name,
                "group": definition.group,
                "risk": definition.risk.value,
                "description": definition.description,
                "inputSchema": definition.params_model.model_json_schema(
                    by_alias=True, mode="validation"
                ),
            }
            for definition in self.list(group)
        ]

    def validate(self, name: str, arguments: Mapping[str, Any]) -> BaseModel:
        """Validate JSON arguments and return the typed parameter model."""
        definition = self.get(name)
        try:
            return definition.params_model.model_validate(arguments)
        except ValidationError as exc:
            details = {
                "issues": exc.errors(include_input=False, include_context=False)
            }
            raise AppError(
                ErrorCode.TOOL_ARGS_INVALID,
                f"工具 {name} 的参数不符合 schema",
                details,
            ) from exc

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
    ) -> Any:
        """Validate parameters and invoke the handler with its model and context."""
        definition = self.get(name)
        params = self.validate(name, arguments)
        result = definition.handler(params, context)
        if inspect.isawaitable(result):
            return await result
        return result
