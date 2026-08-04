from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    checks: dict[str, Literal["ok", "unavailable"]] = Field(default_factory=dict)
