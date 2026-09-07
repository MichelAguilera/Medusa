from typing import Literal

from pydantic import BaseModel, ConfigDict

AuthEngine = Literal["authelia", "authentik", "keycloak"]


class AuthRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    domain: str
    policy: Literal["bypass", "one_factor", "two_factor"]


class OidcClient(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_id: str
    name: str
    secret_file: str
    redirect_uris: tuple[str, ...]
    scopes: tuple[str, ...]
    policy: Literal["one_factor", "two_factor"]


class AuthModel(BaseModel):
    """Engine-neutral identity-provider role: where it runs, the URL every
    proxy calls, the cookie domain, and the rules and clients derived from
    routes. Renderers map this onto a concrete engine's config."""

    model_config = ConfigDict(frozen=True)

    engine: AuthEngine
    host: str
    stack: str | None
    service: str
    url: str
    forward_auth_url: str
    cookie_domain: str
    display_name: str
    default_policy: Literal["one_factor", "two_factor"]
    session_inactivity: str
    session_expiration: str
    session_remember_me: str
    secret_files: dict[str, str]
    rules: tuple[AuthRule, ...]
    clients: tuple[OidcClient, ...]
