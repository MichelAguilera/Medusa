from pydantic import BaseModel, ConfigDict

from medusa.model.dns import ManagedMode


class AnsibleHost(BaseModel):
    """One ansible-managed host in the rendered inventory.

    Derived in normalization from a HostRecord whose ansible_user is set.
    The renderer iterates these tuples without filtering or reshaping.
    """

    model_config = ConfigDict(frozen=True)

    name: str               # short alias (matches HostRecord.name)
    hostname: str           # connection name; first FQDN for the host
    ip: str
    ansible_user: str
    groups: tuple[str, ...] = ()
    managed_mode: ManagedMode = ManagedMode.LIMITED


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


class AnsibleInventoryModel(BaseModel):
    """Inventory shape consumed by the ansible-inventory + bootstrap-blocks
    renderer."""

    model_config = ConfigDict(frozen=True)

    managed_hosts: tuple[AnsibleHost, ...]
    bootstrap_hosts: tuple[BootstrapHost, ...] = ()
