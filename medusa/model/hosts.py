from pydantic import BaseModel, ConfigDict


class ManagedHost(BaseModel):
    """One managed host in the deploy-facing derivation.

    Derived in normalization from a HostRecord whose deploy_user is set.
    Consumers iterate these tuples without filtering or reshaping.
    """

    model_config = ConfigDict(frozen=True)

    name: str               # short alias (matches HostRecord.name)
    hostname: str           # connection name; first FQDN for the host
    ip: str
    deploy_user: str


class BootstrapHost(BaseModel):
    """One entry in the controller's /etc/hosts bootstrap block.

    Derived in normalization from any HostRecord whose bootstrap_ip is
    set. The address is the bootstrap_ip (the temporary cutover
    address), not the canonical DNS IP. Once the host is promoted, this
    entry disappears from the inventory and from /etc/hosts on next
    render.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    hostname: str
    ip: str


class ManagedHostsModel(BaseModel):
    """Managed + bootstrap host shape consumed by the controller submodel
    derivation (ssh aliases, /etc/hosts bootstrap block)."""

    model_config = ConfigDict(frozen=True)

    managed_hosts: tuple[ManagedHost, ...]
    bootstrap_hosts: tuple[BootstrapHost, ...] = ()
