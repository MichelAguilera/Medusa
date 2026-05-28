from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HomepageHostInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip().lower().removesuffix(".")
        if not normalized:
            raise ValueError("homepage host name cannot be empty")
        return normalized

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("homepage host title cannot be empty")
        return normalized


class HomepageInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hosts: list[HomepageHostInventory] = Field(default_factory=list)
    settings: dict[str, Any] | None = None
    bookmarks: list[Any] | None = None
    widgets: list[Any] | None = None

    @model_validator(mode="after")
    def validate_unique_host_names(self) -> Self:
        names = [host.name for host in self.hosts]
        if len(names) != len(set(names)):
            raise ValueError("homepage host names must be unique")
        return self


def parse_homepage_inventory(data: dict | None) -> HomepageInventory:
    if data is None:
        return HomepageInventory()
    return HomepageInventory.model_validate(data)
