from pydantic import BaseModel, ConfigDict


class AnsibleGroupsModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    docker_hosts: tuple[str, ...]
    storage_hosts: tuple[str, ...]
    nfs_export_hosts: tuple[str, ...]
    coredns_hosts: tuple[str, ...]
    traefik_hosts: tuple[str, ...]
    homepage_hosts: tuple[str, ...]
    monitoring_hosts: tuple[str, ...]
    managed_network_hosts: tuple[str, ...]
    egress_gateway_hosts: tuple[str, ...]
    tunnel_routing_hosts: tuple[str, ...]
    # Hosts on the NixOS platform. They carry an SSH endpoint (for
    # nixos-rebuild --target-host, T-075) but are deliberately absent from
    # every role group above, which drive Ansible roles. See T-073.
    nixos_hosts: tuple[str, ...]
    groups: tuple[tuple[str, tuple[str, ...]], ...]
