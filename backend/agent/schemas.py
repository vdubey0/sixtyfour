"""Strict model decisions plus the small browser-facing request contract."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Condition(StrictModel):
    column: str = Field(min_length=1, max_length=200)
    operator: Literal['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'not_in', 'contains', 'is_missing', 'is_present']
    value_json: str = Field(max_length=2000, description='JSON-encoded comparison value; use null for presence checks.')


class Contract(StrictModel):
    description: str = Field(min_length=1, max_length=2000)
    mode: Literal['people', 'companies', 'unknown', 'metadata']
    min_rows: int = Field(ge=0, le=5000)
    max_rows: int | None = Field(description='Exact requested count: set min_rows and max_rows alike. Otherwise null.', ge=0, le=5000)
    required_columns: list[str] = Field(max_length=50)
    unique_by: list[str] = Field(max_length=20, description='Columns forming a unique key when deduplication is requested; otherwise empty.')
    conditions: list[Condition] = Field(max_length=30)
    unresolved_requirements: list[str] = Field(max_length=20, description='Requirements that cannot be checked with the available tools. Nonempty prevents success.')


class Decision(StrictModel):
    action: Literal['plan', 'tool', 'finish', 'partial', 'clarify']
    message: str = Field(min_length=1, max_length=2000, description='Brief user-facing action explanation or findings, never private chain of thought.')
    plan: list[str] | None = Field(max_length=12)
    contract: Contract | None
    initial_dataset_mode: Literal['people', 'companies', 'unknown'] | None = Field(description='On the initial plan only: entity type of uploaded rows, independently of desired output type. Use unknown if unclear.')
    tool: str | None
    arguments_json: str | None = Field(max_length=20000, description='JSON object containing the tool configuration from the catalog.')
    input_dataset: str | None = Field(description='current or a dataset ID from observed snapshots. Null for source/metadata tools.')


class RunRequest(StrictModel):
    query: str = Field(min_length=1, max_length=12000)
    use_jev: bool = False
    upload_path: str | None = None
    run_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
