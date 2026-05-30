from pydantic import BaseModel, ConfigDict


class NetworkHost(BaseModel):
    """Render-ready static-networking config for one opted-in host.

    Derived in normalization from a HostRecord whose ``network`` is set
    (``manage_network: true``). The renderer iterates these and writes one
    systemd-networkd unit per host without filtering or reshaping.
    ``address`` is the canonical ip joined with the prefix as CIDR,
    precomputed here so the template stays formatting-only. See T-055, T-060.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    ip: str  # bare canonical address, e.g. "10.0.0.250" (reconnect target)
    address: str  # canonical ip as CIDR, e.g. "10.0.0.250/24"
    interface: str
    gateway: str
    nameservers: tuple[str, ...]


class NetworkModel(BaseModel):
    """Hosts that opted into Medusa-managed static networking. Empty when no
    host sets ``manage_network: true``."""

    model_config = ConfigDict(frozen=True)

    hosts: tuple[NetworkHost, ...]
