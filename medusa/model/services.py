from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ServiceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    host: str
    stack: str | None


class TraefikRoute(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    host: str
    rule: str
    entrypoints: tuple[str, ...]
    tls: bool
    middlewares: tuple[str, ...]
    target_url: str


class ComposeService(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    host: str
    stack: str | None
    stack_networks: dict[str, Any]
    stack_volumes: dict[str, Any]
    image: str | None
    build: Any
    init: bool | None
    restart: str | None
    command: Any
    ports: tuple[str, ...]
    volumes: tuple[str, ...]
    env_files: tuple[str, ...]
    managed_environment: dict[str, str]
    networks: tuple[str, ...]
    labels: tuple[str, ...]
    depends_on: Any
    healthcheck: dict[str, Any] | None
    managed_secrets: tuple[str, ...]
    user: str | None
    shm_size: str | None
    hostname: str | None
    data_owner: str | None
    egress: Literal["direct", "tunnel"]
    # Container DNS servers. Empty for direct services. Tunneled services point
    # at the egress gateway's split resolver so external lookups exit via the
    # tunnel while `.lan` stays local. See T-066.
    dns: tuple[str, ...]
    # The network whose gateway must be the container's DEFAULT route. Set to
    # the tunnel network for tunneled services so egress can't silently fall
    # back to another attached network (e.g. `proxy`) on docker's undocumented
    # multi-network tie-break. None = leave docker's default. See T-066.
    gateway_network: str | None


class ComposeDataDir(BaseModel):
    """A service bind-mount data directory Medusa creates + chowns before
    `compose up`, so a non-root container can write its volume. ``path`` is
    relative to the managed stacks root. See T-065."""

    model_config = ConfigDict(frozen=True)

    host: str
    path: str
    owner: int
    group: int


class EgressGateway(BaseModel):
    """Resolved shared-WireGuard-egress config. Present only when at least one
    service is tunneled. Carries the external tunnel network name, the gateway
    host + address (split-DNS resolver + routing next-hop), the WireGuard
    interface + secret source, and the split-DNS inputs (managed zones forward
    to CoreDNS, everything else to the in-tunnel upstream). See T-066."""

    model_config = ConfigDict(frozen=True)

    network_name: str
    gateway: str
    gateway_address: str
    interface: str
    wireguard_secret: str
    dns_upstream: str
    coredns_address: str
    zones: tuple[str, ...]
    tunnel_subnet: str
    lan_subnets: tuple[str, ...]
    fwmark: int
    table: int


class ComposeFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    stack: str | None
    services: tuple[ComposeService, ...]
    networks: dict[str, Any]
    volumes: dict[str, Any]
    secrets: dict[str, Any]


class GeneratedEnvFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    stack: str | None
    name: str
    path: str
    values: dict[str, str]


class SecretSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    host: str
    stack: str | None
    destination: str
    mode: str
    setting: str
    secret_name: str
    # Which identity owns the decrypted file on the host. "service" -> the medusa
    # runtime user (compose secrets, read by the user that runs Compose);
    # "system" -> root (a system-daemon secret like the WireGuard gateway config,
    # on a host that may not even have the medusa user). The decrypt unit maps
    # this to a concrete uid. See T-080.
    owner: Literal["service", "system"] = "service"


class ServicesModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    services: tuple[ServiceRecord, ...]
    traefik: tuple[TraefikRoute, ...]
    traefik_routes_by_host: dict[str, tuple[TraefikRoute, ...]]
    # Debian/Docker compose render target only. NixOS hosts are partitioned out
    # here (not in render_compose) so the renderer never branches on platform and
    # no host renders to both compose and Nix. See the Platform Fork Boundary ADR.
    compose: tuple[ComposeFile, ...]
    # The same compose intent for NixOS hosts, consumed only by the normalize_nixos
    # crosscut (-> oci-containers). Never rendered to Debian compose files.
    nixos_compose: tuple[ComposeFile, ...]
    env_files: tuple[GeneratedEnvFile, ...]
    data_dirs: tuple[ComposeDataDir, ...]
    secret_sources: tuple[SecretSource, ...]
    proxies: dict[str, str]
    # host -> sorted tuple of names of services whose effective egress is
    # "tunnel". Empty/absent host = nothing to tunnel there. Drives the later
    # tunnel-network + host-routing stages (T-066).
    tunnel_services_by_host: dict[str, tuple[str, ...]]
    # Resolved egress gateway config; None when nothing is tunneled. T-066.
    egress: EgressGateway | None
