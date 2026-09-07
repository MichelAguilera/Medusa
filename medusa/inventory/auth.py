from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AuthPolicy = Literal["one_factor", "two_factor"]


class AuthSessionInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inactivity: str = "1h"
    expiration: str = "12h"
    remember_me: str = "30d"

    @field_validator("inactivity", "expiration", "remember_me")
    @classmethod
    def normalize_duration(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("session durations cannot be empty")
        return normalized


class AuthSecretsInventory(BaseModel):
    """SOPS secret references the identity provider consumes, each a path
    under secrets/ without the .sops.yaml suffix."""

    model_config = ConfigDict(extra="forbid")

    users: str
    session: str
    storage: str
    jwt: str
    oidc_hmac: str
    oidc_key: str

    @field_validator("users", "session", "storage", "jwt", "oidc_hmac", "oidc_key")
    @classmethod
    def normalize_secret(cls, value: str) -> str:
        normalized = value.strip().removesuffix(".sops.yaml")
        if not normalized:
            raise ValueError("auth secret references cannot be empty")
        return normalized


class AuthInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = "Medusa"
    default_policy: AuthPolicy = "one_factor"
    session: AuthSessionInventory = Field(default_factory=AuthSessionInventory)
    secrets: AuthSecretsInventory

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("auth display_name cannot be empty")
        return normalized


def parse_auth_inventory(data: dict | None) -> AuthInventory | None:
    if not data:
        return None
    return AuthInventory.model_validate(data)
