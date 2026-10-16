"""Cities and domains are data (config/*.yaml), validated here. Never hard-code them in code."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from .qloo.models import INSIGHTS_TYPES


class City(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    display_name: str
    location: str = Field(min_length=1, max_length=100)  # sent as *.location.query
    country: str = Field(pattern=r"^[A-Z]{2}$")
    script_note: str = ""
    tier: int = Field(ge=1, le=3)


class Domain(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    filter_type: str
    take: int = Field(ge=1, le=50)
    phrasing: str
    location_param: Literal["filter", "signal"]
    params: dict[str, Any] = Field(default_factory=dict)
    tag_query: str = ""

    @field_validator("filter_type")
    @classmethod
    def _official(cls, v: str) -> str:
        if v not in INSIGHTS_TYPES:
            raise ValueError(f"{v!r} is not an insights filter.type; valid: {sorted(INSIGHTS_TYPES)}")
        return v

    def location_params(self, city: City) -> dict[str, str]:
        return {f"{self.location_param}.location.query": city.location}


def _load(path: Path, key: str, model):
    items = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get(key) or []
    out = [model.model_validate(x) for x in items]
    ids = [x.id for x in out]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate ids in {path}")
    return out


def load_cities(config_dir: Path | str = "config") -> list[City]:
    return _load(Path(config_dir) / "cities.yaml", "cities", City)


def load_domains(config_dir: Path | str = "config") -> list[Domain]:
    return _load(Path(config_dir) / "domains.yaml", "domains", Domain)
