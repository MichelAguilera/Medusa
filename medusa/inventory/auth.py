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


AUTH_SECRET_KEYS = ("users", "session", "storage", "jwt", "oidc_hmac", "oidc_key")


class AuthSecretsInventory(BaseModel):
    """SOPS secret references the identity provider consumes, each a path
    under secrets/ without the .sops.yaml suffix. Fleet-level values are
    defaults; an auth service's own ``auth_secrets`` overrides per host, and
    every key must resolve one way or the other."""

    model_config = ConfigDict(extra="forbid")

    users: str | None = None
    session: str | None = None
    storage: str | None = None
    jwt: str | None = None
    oidc_hmac: str | None = None
    oidc_key: str | None = None

    @field_validator(*AUTH_SECRET_KEYS)
    @classmethod
    def normalize_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().removesuffix(".sops.yaml")
        if not normalized:
            raise ValueError("auth secret references cannot be empty")
        return normalized

    def merged_over(
        self, defaults: "AuthSecretsInventory | None"
    ) -> dict[str, str | None]:
        base = defaults.model_dump() if defaults is not None else {}
        return {key: getattr(self, key) or base.get(key) for key in AUTH_SECRET_KEYS}


class AuthInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = "Medusa"
    default_policy: AuthPolicy = "one_factor"
    session: AuthSessionInventory = Field(default_factory=AuthSessionInventory)
    secrets: AuthSecretsInventory | None = None

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
