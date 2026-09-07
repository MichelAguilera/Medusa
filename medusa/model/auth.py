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
    token_auth: Literal["client_secret_basic", "client_secret_post"]


class AuthModel(BaseModel):
    """Engine-neutral identity-provider role for one host: its portal URL,
    the in-host address the proxy calls, the cookie domain (the host's own
    subdomain, so sessions never cross hosts), and the rules and clients
    derived from that host's routes. Renderers map it onto an engine."""

    model_config = ConfigDict(frozen=True)

    engine: AuthEngine
    host: str
    stack: str | None
    service: str
    port: int
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
