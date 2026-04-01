"""Tool schema — describes what a tool is and what it accepts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolParameter(BaseModel):
    name: str
    type: str = "string"
    description: str
    required: bool = True


class ToolSchema(BaseModel):
    """Complete description of a tool that agents can use."""

    name: str
    description: str
    parameters: list[ToolParameter] = Field(default_factory=list)
    # Deferred loading (Claude Code pattern): tools with deferred=True
    # are NOT sent to the LLM unless keyword-matched to the user command.
    # Saves ~70% tool schema tokens on most turns.
    deferred: bool = False
    # Keywords that trigger inclusion of this deferred tool
    keywords: list[str] = Field(default_factory=list)

    def to_anthropic_tool(self) -> dict:
        """Convert to Anthropic API tool format."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters:
            properties[p.name] = {"type": p.type, "description": p.description}
            if p.required:
                required.append(p.name)

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }
