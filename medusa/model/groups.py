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
    groups: tuple[tuple[str, tuple[str, ...]], ...]
