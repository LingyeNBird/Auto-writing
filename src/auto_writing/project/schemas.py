from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    chapter_count: int = Field(ge=1)
    theme_notes: str | None = Field(default=None)


class ProjectCreateResponse(BaseModel):
    project_id: str
    status: str


class ProjectReadResponse(BaseModel):
    id: str
    name: str
    chapter_count: int
    theme_notes: str | None
    status: str
    created_at: datetime
    updated_at: datetime
