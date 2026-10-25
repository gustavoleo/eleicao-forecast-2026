"""Validate required structure; preserve extra fields for evolving TSE schemas."""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

class UnifiedResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    ele: str
    t: str
    cdabr: str
    dg: str
    hg: str
    carg: list[dict[str, Any]] = Field(min_length=1)
    s: dict[str, Any]
    e: dict[str, Any]
    v: dict[str, Any]

class Candidate(BaseModel):
    model_config = ConfigDict(extra='allow')
    candidate_id: str
    name: str
    votes: int = Field(ge=0)
    elected: bool = False
    destination: str = ''
