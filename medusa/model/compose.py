from medusa.inventory.services import ServicesInventory
from medusa.model.services import ComposeDataDir, ComposeFile, ComposeService
from medusa.model.settings import (
    managed_env_files,
    managed_environment,
    managed_file_secret_names,
)
from medusa.model.storage import NfsMount, StorageModel
from medusa.model.volumes import is_named_compose_volume


def normalize_compose_services(
    inventory: ServicesInventory,
    services,
    mount_index: dict[tuple[str, str], NfsMount],
) -> list[ComposeService]:
    return [
        ComposeService(
            id=service.id,
            name=service.name,
            host=service.host,
            stack=service.stack,
            stack_networks=service.compose.stack_networks,
            stack_volumes=service.compose.stack_volumes,
            image=service.image,
            build=service.compose.build,
            init=service.compose.init,
            restart=service.compose.restart,
            command=service.compose.command,
            ports=tuple(service.compose.ports),
            volumes=_service_volumes(service, mount_index),
            env_files=managed_env_files(service),
            managed_environment=managed_environment(service),
            networks=tuple(service.compose.networks),
            labels=tuple(service.compose.labels),
            depends_on=service.compose.depends_on,
            healthcheck=service.compose.healthcheck,
            managed_secrets=managed_file_secret_names(service),
            user=service.compose.user,
            shm_size=service.compose.shm_size,
            data_owner=service.compose.data_owner,
        )
        for service in sorted(services, key=lambda item: item.id)
        if service.compose is not None
    ]


def normalize_compose_data_dirs(
    compose_services: list[ComposeService],
) -> tuple[ComposeDataDir, ...]:
    """Derive the bind-mount data directories Medusa must create + chown.

    For each service that declares ``data_owner``, every RELATIVE bind-mount
    (``./...``, under the stack project dir) becomes a directory Medusa owns to
    the declared UID:GID before `compose up`. Absolute/named/NFS mounts are not
    Medusa's to create and are skipped. ``path`` is relative to the managed
    stacks root (``<stack>/<source>``).
    """
    data_dirs: list[ComposeDataDir] = []
    for service in compose_services:
        if service.data_owner is None or service.stack is None:
            continue
        uid_str, _, gid_str = service.data_owner.partition(":")
        uid = int(uid_str)
        gid = int(gid_str) if gid_str else uid
        for volume in service.volumes:
            source = volume.split(":", 1)[0]
            if not source.startswith("./"):
                continue
            path = f"{service.stack}/{source[2:]}"
            data_dirs.append(
                ComposeDataDir(
                    host=service.host, path=path, owner=uid, group=gid
                )
            )
    return tuple(
        sorted(data_dirs, key=lambda item: (item.host, item.path))
    )


def normalize_compose_files(
    inventory: ServicesInventory,
    compose_services: list[ComposeService],
) -> tuple[ComposeFile, ...]:
    groups = sorted(
        {(service.host, service.stack) for service in compose_services},
        key=lambda item: (item[0], item[1] or ""),
    )
    return tuple(
        ComposeFile(
            host=host,
            stack=stack,
            services=group_services,
            networks=_compose_networks(group_services),
            volumes=_compose_named_volumes(group_services),
            secrets=_compose_secrets(group_services),
        )
        for host, stack in groups
        for group_services in [_compose_group_services(compose_services, host, stack)]
    )


def validate_service_mount_refs(
    services,
    storage_model: StorageModel | None,
) -> dict[tuple[str, str], NfsMount]:
    mount_index: dict[tuple[str, str], NfsMount] = {}
    if storage_model is not None:
        for mount in storage_model.mounts:
            mount_index[(mount.host, mount.id)] = mount

    referenced = [
        (service, mount_ref)
        for service in services
        for mount_ref in service.mounts
    ]

    missing = sorted(
        f"{service.id}->{mount_ref.mount}"
        for service, mount_ref in referenced
        if (service.host, mount_ref.mount) not in mount_index
    )
    if missing:
        formatted = ", ".join(missing)
        raise ValueError(
            f"services reference unknown mounts for their host: {formatted}"
        )

    return mount_index


def _service_volumes(
    service,
    mount_index: dict[tuple[str, str], NfsMount],
) -> tuple[str, ...]:
    volumes: list[str] = list(service.compose.volumes)
    for mount_ref in service.mounts:
        mount = mount_index[(service.host, mount_ref.mount)]
        suffix = ":ro" if mount_ref.read_only else ""
        volumes.append(f"{mount.mountpoint}:{mount_ref.target}{suffix}")
    return tuple(volumes)


def _compose_group_services(
    services: list[ComposeService],
    host: str,
    stack: str | None,
) -> tuple[ComposeService, ...]:
    return tuple(
        service
        for service in services
        if service.host == host and service.stack == stack
    )


def _compose_networks(services) -> dict:
    networks = {
        network: {"external": True} if network == "proxy" else None
        for network in sorted(
            {network for service in services for network in service.networks}
        )
    }
    for service in services:
        networks.update(service.stack_networks)
    return networks


def _compose_named_volumes(services) -> dict:
    volumes: set[str] = set()
    for service in services:
        for volume in service.volumes:
            source = volume.split(":", 1)[0]
            if is_named_compose_volume(source):
                volumes.add(source)

    rendered = {volume: None for volume in sorted(volumes)}
    for service in services:
        rendered.update(service.stack_volumes)
    return rendered


def _compose_secrets(services) -> dict:
    managed_secrets = {
        secret: {"file": f"./secrets/{secret}"}
        for secret in sorted(
            {secret for service in services for secret in service.managed_secrets}
        )
    }
    return managed_secrets
