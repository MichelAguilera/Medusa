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
    # Resolved static-networking config, set only for hosts that opted into
    # managed networking (manage_network: true). None otherwise. See T-055.
    network: HostNetwork | None = None
    # Explicit opt-in to a CoreDNS wildcard (subdomain catch-all) for this
    # host, independent of whether it runs a Medusa-managed proxy. Unioned
    # with proxy hosts when deriving rewrite_zones in normalize.
    wildcard: bool = False

    @property
    def is_ansible_managed(self) -> bool:
        return self.ansible_user is not None


class DnsModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    zones: tuple[DnsZone, ...]
    hosts: tuple[HostRecord, ...]
