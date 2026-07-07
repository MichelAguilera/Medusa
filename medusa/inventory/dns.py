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
    # Deploy platform for this host. "debian-docker" (default) renders to the
    # historical Compose + fstab + systemd-networkd path driven by Ansible.
    # "nixos" renders to a generated Nix module/flake driven by nixos-rebuild
    # (T-074/T-075) and is excluded from every Ansible role group. Orthogonal
    # to ansible_managed_mode: a host's platform and its access mode are
    # independent. Default keeps every existing host on the Debian path. See
    # T-073.
    platform: Literal["debian-docker", "nixos"] = "debian-docker"
    # Host lifecycle state (T-091). "dormant" declares expected downtime: the
    # host keeps its DNS records and all its artifacts still render, but deploy
    # dispatch skips it (absent from ansible inventory/groups, skipped by the
    # nixos deploy plan, dropped from monitoring scrape targets) and the deploy
    # stays green. Downtime is DECLARED here, never detected at deploy time --
    # an unreachable *active* host stays a loud failure. Validation rejects a
    # dormant host that active hosts still depend on (NFS exports, egress
    # gateway, coredns). Only meaningful on a deployable host (ansible_user set
    # or platform: nixos); a DNS-only host is already inert.
    state: Literal["active", "dormant"] = "active"
    # Guest type for a nixos host. "vm" (Proxmox/KVM) gets the QEMU guest agent
    # (services.qemuGuest.enable) so the hypervisor can read the guest's IP, run
    # graceful shutdown, etc.; "lxc" (Proxmox container) gets no agent and cannot
    # use disko -- a container has no block device; "physical" gets no agent but
    # can use disko. Only meaningful for platform: nixos (ignored on debian).
    # Default "vm" -- the common Proxmox case and the SFTP pilot (T-078).
    nixos_guest: Literal["vm", "lxc", "physical"] = "vm"
    # Opt into Medusa-staged disko partitioning for the nixos-anywhere bootstrap
    # (T-078). When true, the operator authors `templates/nixos/disko/<name>.nix`
    # (a real disko.devices Nix file -- disk layout is operator territory,
    # T-071/T-072); render stages it verbatim into the flake tree and the host
    # module + flake import disko and that config. Default false: a nixos host
    # whose disk is already laid out (or managed elsewhere) needs no disko.
    # Requires platform: nixos and a guest with a real disk (not lxc).
    nixos_disko: bool = False
    # Admin/deploy SSH public keys authorized for this host's deploy user
    # (``ansible_user`` -- root or a wheel user). Plain inventory data: public
    # keys are not secret. Without at least one, `nixos-rebuild --target-host`
    # cannot authenticate, so a deployable nixos host needs one. nixos-only.
    nixos_admin_keys: list[str] = Field(default_factory=list)
    # NixOS `system.stateVersion` for this host -- the release it was first
    # installed with. Pinned at install and NEVER bumped on upgrade (it guards
    # stateful defaults), so it is host data, not derived from the flake's
    # nixpkgs pin. Defaults in normalize to the current release. nixos-only.
    nixos_state_version: str | None = None
    # IANA timezone for the host (e.g. "America/New_York"). Consumed by the
    # NixOS render (`time.timeZone`); Debian hosts carry their timezone from
    # OS install. Matters beyond cosmetics: without it /etc/localtime does
    # not exist on NixOS, and a compose service binding /etc/localtime makes
    # docker auto-create the missing source as a DIRECTORY, which then fails
    # to mount over the container's file.
    timezone: str | None = None
    # The host's age recipient (public key), derived from its ssh host key via
    # `ssh-to-age`. Public data -- not key material -- so it lives in inventory.
    # When a host references a secret, this recipient is added to that secret's
    # generated creation_rule so the host can decrypt it locally with its own
    # ssh host key (host-side decryption, T-080). Both platforms: NixOS via
    # sops-nix, Debian via a local sops decrypt step. Unset until the host's key
    # is harvested during the T-080 cutover; a secret-referencing host without
    # one renders a creation_rule it cannot decrypt (surfaced as a warning).
    age_recipient: str | None = None

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

    @field_validator("age_recipient")
    @classmethod
    def normalize_age_recipient(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("age_recipient cannot be empty when set")
        if not normalized.startswith("age1"):
            raise ValueError("age_recipient must be an age1 public key")
        return normalized

    @model_validator(mode="after")
    def validate_managed_mode_requires_user(self) -> Self:
        if self.ansible_user is None and self.ansible_managed_mode is not None:
            raise ValueError(
                f"host {self.name}: ansible_managed_mode requires ansible_user"
            )
        return self

    @model_validator(mode="after")
    def validate_dormant_requires_deployable(self) -> Self:
        if (
            self.state == "dormant"
            and self.ansible_user is None
            and self.platform != "nixos"
        ):
            raise ValueError(
                f"host {self.name}: state 'dormant' has no effect on a "
                f"DNS-only host (no ansible_user, not platform: nixos); "
                f"drop the state field"
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

    @model_validator(mode="after")
    def validate_nixos_guest_options(self) -> Self:
        # nixos_guest / nixos_disko are nixos-only knobs; reject them on debian
        # hosts so a misplaced field is a loud error, not a silent no-op.
        if self.platform != "nixos":
            if self.nixos_disko:
                raise ValueError(
                    f"host {self.name}: nixos_disko requires platform: nixos"
                )
            if self.nixos_guest != "vm":
                raise ValueError(
                    f"host {self.name}: nixos_guest is only meaningful for "
                    f"platform: nixos"
                )
            if self.nixos_admin_keys:
                raise ValueError(
                    f"host {self.name}: nixos_admin_keys requires platform: nixos"
                )
            if self.nixos_state_version is not None:
                raise ValueError(
                    f"host {self.name}: nixos_state_version requires platform: "
                    f"nixos"
                )
        if self.nixos_disko and self.nixos_guest == "lxc":
            raise ValueError(
                f"host {self.name}: nixos_disko cannot apply to an lxc guest -- "
                f"a container has no block device to partition"
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
    # SSH public keys baked into the generated Medusa installer ISO's live
    # root (T-089): boot the ISO and the controller reaches the target
    # immediately -- no console bootstrap. Fleet-level and explicit (NOT
    # derived from any host's nixos_admin_keys) so key rotation stays
    # deliberate. Empty = no installer image is emitted. Public keys only.
    nixos_installer_keys: list[str] = Field(default_factory=list)

    @field_validator("nixos_installer_keys")
    @classmethod
    def _validate_installer_keys(cls, value: list[str]) -> list[str]:
        normalized = [key.strip() for key in value]
        if any(not key for key in normalized):
            raise ValueError("nixos_installer_keys cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("nixos_installer_keys cannot contain duplicates")
        for key in normalized:
            # Same plaintext-transport guard as sftp authorized keys: catch a
            # pasted private key or multi-line blob before it lands in an
            # installer image.
            if "\n" in key or not key.startswith(("ssh-", "ecdsa-", "sk-")):
                raise ValueError(
                    f"nixos installer key {key[:32]!r}... does not look like "
                    f"an SSH public key (expected 'ssh-...', 'ecdsa-...' or "
                    f"'sk-...'). Only public keys belong in inventory."
                )
        return normalized

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
