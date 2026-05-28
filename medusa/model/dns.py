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

    @property
    def is_ansible_managed(self) -> bool:
        return self.ansible_user is not None


class DnsModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    zones: tuple[DnsZone, ...]
    hosts: tuple[HostRecord, ...]
