"""Input validation schemas for the trust boundary (HTTP form bodies)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

MAX_TITLE_LEN = 200
MAX_DESC_LEN = 5000
MAX_TEAMS_CAP = 1000


class ProjectCreate(BaseModel):
    """Validated project-creation input. Raises ValidationError on bad input;
    the route turns that into a generic 400 with a 简体中文 message."""

    title: str
    description: str = ""
    max_teams: int | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _title_nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("标题不能为空")
        if len(v) > MAX_TITLE_LEN:
            raise ValueError("标题过长")
        return v

    @field_validator("description")
    @classmethod
    def _description_bounded(cls, v: str) -> str:
        v = v or ""
        if len(v) > MAX_DESC_LEN:
            raise ValueError("描述过长")
        return v

    @field_validator("max_teams")
    @classmethod
    def _max_teams_range(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if v < 1 or v > MAX_TEAMS_CAP:
            raise ValueError("队伍数量无效")
        return v

    @model_validator(mode="after")
    def _close_after_open(self) -> "ProjectCreate":
        if (
            self.opens_at is not None
            and self.closes_at is not None
            and self.closes_at <= self.opens_at
        ):
            raise ValueError("关闭时间必须晚于开放时间")
        return self
