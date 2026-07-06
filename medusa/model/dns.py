from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ManagedMode(StrEnum):
    """Three-state classification of a host's relationship to medusa.

    - ``NONE``: DNS-only ("documented"). No ansible_user, ignored by deploy.
    - ``FULL``: medusa-built template. Root SSH prep + hardening audit on.
    - ``LIMITED``: pre-existing/baremetal managed host. No prep, no
      root-SSH audit. Default when a host has ansible_user but no explicit
      mode, because ``FULL`` enables destructive prep flows and must be
      deliberate.
    """

    NONE = "none"
    FULL = "full"
    LIMITED = "limited"


class DnsZone(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    upstreams: tuple[str, ...]
    forwarder_mode: Literal["udp", "dot"] = "udp"
    forwarder_tls_servername: str | None = None


class HostNetwork(BaseModel):
    """Resolved static-networking config for a host that opted into
    Medusa-managed networking (``manage_network: true``). Built in
    normalization by merging the host's override over the global defaults;
    ``None`` on HostRecord for any host that did not opt in. See T-055."""

    model_config = ConfigDict(frozen=True)

    interface: str
    prefix: int
    gateway: str
    nameservers: tuple[str, ...]


class HostRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    ip: str
    # Temporary cutover address. When set, this host is seeded into the
    # controller's /etc/hosts bootstrap block at bootstrap_ip; once DNS
    # is live for the host the operator runs `medusa promote-host` to
    # clear it and the entry disappears from /etc/hosts.
    bootstrap_ip: str | None = None
    name: str
    zones: tuple[str, ...]
    aliases: tuple[str, ...]
    fqdns: tuple[str, ...]
    # ansible_user is None for DNS-only hosts; when set, the host is
    # ansible-managed and shows up in the rendered ansible inventory.
    ansible_user: str | None = None
    ansible_groups: tuple[str, ...] = ()
    managed_mode: ManagedMode = ManagedMode.NONE
    # IANA timezone; consumed by the NixOS render (time.timeZone). None means
    # the platform default (UTC, and no /etc/localtime on NixOS).
    timezone: str | None = None
    # Resolved static-networking config, set only for hosts that opted into
    # managed networking (manage_network: true). None otherwise. See T-055.
    network: HostNetwork | None = None
    # Explicit opt-in to a CoreDNS wildcard (subdomain catch-all) for this
    # host, independent of whether it runs a Medusa-managed proxy. Unioned
    # with proxy hosts when deriving rewrite_zones in normalize.
    wildcard: bool = False
    # Deploy platform. "debian-docker" hosts render to Compose + fstab +
    # systemd-networkd and deploy via Ansible; "nixos" hosts render to a Nix
    # module/flake (T-074) and deploy via nixos-rebuild (T-075). Orthogonal to
    # managed_mode. Renderers partition on this; they never branch on it
    # themselves (renderer contract). See T-073.
    platform: Literal["debian-docker", "nixos"] = "debian-docker"
    # Guest type ("vm" | "lxc" | "physical") for a nixos host. Drives the QEMU
    # guest agent and whether disko applies; normalize_nixos derives both from
    # it. Carried verbatim; default "vm". See T-078.
    nixos_guest: str = "vm"
    # Whether render stages an operator-authored disko config for this host's
    # nixos-anywhere bootstrap. See T-078.
    nixos_disko: bool = False
    # Admin/deploy SSH public keys for the nixos deploy user. Plain data (public
    # keys are not secret). See T-078.
    nixos_admin_keys: tuple[str, ...] = ()
    # NixOS system.stateVersion override (install-time release); None falls back
    # to the default in normalize_nixos. See T-078.
    nixos_state_version: str | None = None
    # The host's age recipient (public key) derived from its ssh host key via
    # ssh-to-age. Plain data (public). Carried verbatim; feeds the generated
    # .sops.yaml creation_rules so a host that references a secret is a recipient
    # of it and can decrypt host-side. None until harvested. See T-080.
    age_recipient: str | None = None

    @property
    def is_ansible_managed(self) -> bool:
        return self.ansible_user is not None

    @property
    def is_nixos(self) -> bool:
        return self.platform == "nixos"


class DnsModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    zones: tuple[DnsZone, ...]
    hosts: tuple[HostRecord, ...]
    # Public keys for the generated installer ISO's live root (T-089); empty
    # disables the installer image.
    installer_keys: tuple[str, ...] = ()

    def hosts_by_platform(self, platform: str) -> tuple[HostRecord, ...]:
        """Hosts on the given deploy platform, in declared order. The render
        fan-out reads this so platform partitioning stays in the model, not in
        renderers (T-073)."""
        return tuple(host for host in self.hosts if host.platform == platform)
