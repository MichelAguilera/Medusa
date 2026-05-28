from typing import Any

from pydantic import BaseModel, ConfigDict


class HomepageCard(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    fields: tuple[tuple[str, Any], ...]


class HomepageGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    title: str
    cards: tuple[HomepageCard, ...]


class HomepageModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    hosts: tuple[str, ...]
    groups: tuple[HomepageGroup, ...]
    settings: dict[str, Any] | None
    bookmarks: tuple[Any, ...] | None
    widgets: tuple[Any, ...] | None
