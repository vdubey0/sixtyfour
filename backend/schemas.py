"""Shapes of browser requests and graph-validation errors.

These models validate the outer JSON structure. The catalog validates each
block's config; the graph builder validates how the blocks connect.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BlockDTO(BaseModel):
    """One canvas node: unique instance ID, tool type, and tool settings."""
    id: str
    type: str
    config: Dict[str, Any] = Field(default_factory=dict)


class ConnectionDTO(BaseModel):
    """A directed edge; field names match the frontend's JSON payload."""
    fromId: str
    toId: str


class FinalRequest(BaseModel):
    """The complete canvas submitted for execution, before ordering its nodes."""
    blocks: List[BlockDTO]
    connections: List[ConnectionDTO]


class ExecuteResponse(BaseModel):
    """Legacy response shape used for validation failures; live runs emit events."""
    status: str
    execution_order: List[str]
    final_output: Any = None
    error: Optional[str] = None
