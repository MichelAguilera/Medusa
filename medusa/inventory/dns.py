from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ZoneInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    upstreams: list[str] = Field(default_factory=list)
    # CoreDNS forward transport for this zone's upstreams. "udp" is the
    # historical default; "dot" switches to DNS-over-TLS (tcp/853) and
    # requires forwarder_tls_servername for cert validation. Operators
    # flip to dot when an upstream path is hijacked by an ISP or router
    # that rewrites/drops plain UDP/53 queries.
    forwarder_mode: Literal["udp", "dot"] = "udp"
    forwarder_tls_servername: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_zone_name(cls, value: str) -> str:
        normalized = value.strip().lower().removesuffix(".")
        if not normalized:
            raise ValueError("zone name cannot be empty")
        return normalized

    @field_validator("upstreams")
    @classmethod
    def upstreams_cannot_be_empty_strings(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("upstreams cannot contain empty values")
        return normalized

    @field_validator("forwarder_tls_servername")
    @classmethod
    def normalize_tls_servername(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("forwarder_tls_servername cannot be empty when set")
        if " " in normalized:
            raise ValueError(
                "forwarder_tls_servername must be a single hostname, not a list"
            )
        return normalized

    @model_validator(mode="after")
    def validate_forwarder_combo(self) -> Self:
        if self.forwarder_mode == "dot" and self.forwarder_tls_servername is None:
            raise ValueError(
                f"zone {self.name}: forwarder_mode 'dot' requires "
                f"forwarder_tls_servername"
            )
        if self.forwarder_mode == "udp" and self.forwarder_tls_servername is not None:
            raise ValueError(
                f"zone {self.name}: forwarder_tls_servername is only valid "
                f"with forwarder_mode 'dot'"
            )
        return self


class NetworkConfig(BaseModel):
    """Static-networking inputs that ``ip`` alone does not carry. Used both
    as the optional global default block (``DnsInventory.network``) and as a
    per-host override (``HostInventory.network``). Every field is optional;
    a host opted into managed networking resolves each one from its override
    first, then the global default. Completeness is enforced at the
    ``DnsInventory`` level by ``resolve_host_network``, which is the single
    source of merge truth (it can see both layers).
    """

    model_config = ConfigDict(extra="forbid")

    interface: str | None = None
    prefix: int | None = Field(default=None, ge=1, le=128)
    gateway: IPv4Address | IPv6Address | None = None
    nameservers: list[IPv4Address | IPv6Address] = Field(default_factory=list)

    @field_validator("interface")
    @classmethod
    def normalize_interface(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("interface cannot be empty when set")
        return normalized


class ResolvedHostNetwork(BaseModel):
    """A host's managed-network config after merging override + global
    defaults. Only produced for hosts that opt in (``manage_network: true``)
    and only when every required field resolves."""

    model_config = ConfigDict(frozen=True)

    interface: str
    prefix: int
    gateway: str
    nameservers: tuple[str, ...]


def resolve_host_network(
    host: "HostInventory", defaults: "NetworkConfig | None"
) -> ResolvedHostNetwork | None:
    """Merge a host's per-host network override over the global defaults.

    Returns ``None`` for a host that has not opted into managed networking.
    Raises ``ValueError`` when a host opts in but the merged config is
    incomplete, or when a host supplies overrides without opting in, or when
    the gateway is not on the host's own subnet. This is called both by
    ``DnsInventory``'s validator (to surface errors at validation time) and
    by normalization (to build the model), so the merge logic lives once.
    """
    override = host.network
    if not host.manage_network:
        if override is not None:
            raise ValueError(
                f"host {host.name}: network overrides are set but "
                f"manage_network is false; set manage_network: true or drop "
                f"the network block"
            )
        return None

    interface = (override.interface if override else None) or (
        defaults.interface if defaults else None
    )
    prefix = (override.prefix if override else None) or (
        defaults.prefix if defaults else None
    )
    gateway = (override.gateway if override else None) or (
        defaults.gateway if defaults else None
    )
    nameservers = (
        (override.nameservers if override and override.nameservers else None)
        or (defaults.nameservers if defaults and defaults.nameservers else None)
        or []
    )

    missing = [
        field
        for field, value in (
            ("interface", interface),
            ("prefix", prefix),
            ("gateway", gateway),
            ("nameservers", nameservers),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"host {host.name}: manage_network is true but {', '.join(missing)} "
            f"could not be resolved; set it per-host or in global network defaults"
        )

    # mypy/readers: missing-check above guarantees these are set.
    assert interface is not None and prefix is not None and gateway is not None

    network_cls = IPv4Network if isinstance(host.ip, IPv4Address) else IPv6Network
    subnet = network_cls(f"{host.ip}/{prefix}", strict=False)
    if gateway not in subnet:
        raise ValueError(
            f"host {host.name}: gateway {gateway} is not on the host's subnet "
            f"{subnet} (ip {host.ip}/{prefix})"
        )

    return ResolvedHostNetwork(
        interface=interface,
        prefix=prefix,
        gateway=str(gateway),
        nameservers=tuple(str(server) for server in nameservers),
    )


class HostInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ip: IPv4Address | IPv6Address
    # Temporary cutover address used in the controller's /etc/hosts
    # bootstrap block when real DNS does not yet resolve this host
    # (e.g. host still on a DHCP lease, CoreDNS not yet authoritative
    # for the zone). When unset, the host is assumed to be reachable
    # via DNS and is NOT seeded into /etc/hosts. Clear with
    # `medusa promote-host` once DNS is live for this host.
    bootstrap_ip: IPv4Address | IPv6Address | None = None
    zones: list[str] = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    # Optional ansible inventory metadata. When ansible_user is set, the host
    # is considered "managed" and is emitted into ansible inventory + the
    # bootstrap /etc/hosts / ~/.ssh/config helper files. DNS-only hosts (no
    # ansible_user) are skipped by the ansible inventory renderer.
    ansible_user: str | None = None
    ansible_groups: list[str] = Field(default_factory=list)
    # When ansible_user is set, distinguishes how much access medusa has to
    # the host. "full" = medusa-built template (root SSH prep + hardening
    # audit allowed). "limited" = pre-existing/baremetal host (no prep, no
    # root-SSH audit). When omitted on a managed host, normalize defaults to
    # "limited" because "full" can run destructive prep flows and must be
    # deliberate. DNS-only hosts (no ansible_user) must leave this unset.
    ansible_managed_mode: Literal["full", "limited"] | None = None
    # Opt-in to Medusa-managed static networking. When true, Medusa renders
    # and (in a later stage) applies a static config pinning this host's NIC
    # to its canonical `ip`. Default false: hosts are NEVER touched unless
    # opted in, which keeps Proxmox bridge networking and any baremetal host
    # off-limits by default. See T-055.
    manage_network: bool = False
    # Per-host static-networking override. Fields left unset fall back to the
    # global `network:` defaults. Only meaningful when manage_network is true.
    network: NetworkConfig | None = None
    # Opt-in to a CoreDNS wildcard (subdomain catch-all) for this host.
    # When true, `*.<name>.<zone>` resolves to the host's own IP via a
    # `rewrite stop` block, so the host can do its own Host-header routing.
    # Independent of Medusa-managed services: a host running its own reverse
    # proxy (unmanaged) can opt in without any service records. Proxy hosts
    # get a wildcard automatically regardless of this flag.
    wildcard: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip().lower().removesuffix(".")
        if not normalized:
            raise ValueError("host name cannot be empty")
        if "." in normalized:
            raise ValueError("host name must be relative, not a FQDN")
        return normalized

    @field_validator("zones")
    @classmethod
    def normalize_zones(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower().removesuffix(".") for item in value]
        if any(not item for item in normalized):
            raise ValueError("host zones cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("host zones must be unique")
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower().removesuffix(".") for item in value]
        if any(not item for item in normalized):
            raise ValueError("aliases cannot contain empty values")
        if any("." in item for item in normalized):
            raise ValueError("aliases must be relative, not FQDNs")
        if len(set(normalized)) != len(normalized):
            raise ValueError("aliases must be unique per host")
        return normalized

    @field_validator("ansible_user")
    @classmethod
    def normalize_ansible_user(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("ansible_user cannot be empty when set")
        return normalized

    @field_validator("ansible_groups")
    @classmethod
    def normalize_ansible_groups(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("ansible_groups cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("ansible_groups must be unique per host")
        return normalized

    @model_validator(mode="after")
    def validate_managed_mode_requires_user(self) -> Self:
        if self.ansible_user is None and self.ansible_managed_mode is not None:
            raise ValueError(
                f"host {self.name}: ansible_managed_mode requires ansible_user"
            )
        return self

    @model_validator(mode="after")
    def validate_bootstrap_ip_differs_from_ip(self) -> Self:
        if self.bootstrap_ip is not None and self.bootstrap_ip == self.ip:
            raise ValueError(
                f"host {self.name}: bootstrap_ip equals ip; drop bootstrap_ip "
                f"instead of duplicating the address"
            )
        return self


class DnsInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zones: list[ZoneInventory]
    hosts: list[HostInventory] = Field(default_factory=list)
    # Global static-networking defaults applied to every host that opts into
    # managed networking (manage_network: true). Per-host `network:` blocks
    # override individual fields. Typically sets interface, prefix, gateway,
    # and nameservers (the CoreDNS host IP). See T-055.
    network: NetworkConfig | None = None

    @model_validator(mode="after")
    def validate_managed_network(self) -> Self:
        # Drives resolve_host_network for every host so opt-in completeness,
        # stray-override, and gateway-subnet errors surface at validation
        # time rather than at render/apply.
        for host in self.hosts:
            resolve_host_network(host, self.network)
        return self

    @model_validator(mode="after")
    def validate_references_and_uniqueness(self) -> Self:
        zone_names = [zone.name for zone in self.zones]
        if len(zone_names) != len(set(zone_names)):
            raise ValueError("zone names must be unique")

        known_zones = set(zone_names)
        seen_names: set[str] = set()

        for host in self.hosts:
            unknown = sorted(set(host.zones) - known_zones)
            if unknown:
                formatted = ", ".join(unknown)
                raise ValueError(
                    f"host {host.name} references unknown zone(s): {formatted}"
                )

            if host.name in host.aliases:
                raise ValueError(f"host {host.name} repeats its own name as an alias")

            for label in [host.name, *host.aliases]:
                for zone in host.zones:
                    fqdn = f"{label}.{zone}"
                    if fqdn in seen_names:
                        raise ValueError(f"duplicate DNS name: {fqdn}")
                    seen_names.add(fqdn)

        return self


def parse_dns_inventory(data: dict) -> DnsInventory:
    return DnsInventory.model_validate(data)
