from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class EstimateContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_notes: str = Field(default="", max_length=50_000)
    template_type: str = Field(default="", max_length=40)
    scope: dict[str, Any] = Field(default_factory=dict)
    reference_job_ids: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] = Field(default_factory=list, max_length=10)
    include_source_metadata: bool = False


class EstimateContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    scope: dict[str, Any]
    context: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    retrieval_summary: dict[str, Any]
    source_metadata: dict[str, Any] | None = None
