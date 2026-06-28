import re

from medusa.inventory.dns import (
    DnsInventory,
    HostInventory,
    NetworkConfig,
    resolve_host_network,
)
from medusa.inventory.homepage import HomepageInventory
from medusa.inventory.native import NativeServicesInventory
from medusa.inventory.secrets import SecretsInventory
from medusa.inventory.services import ServicesInventory, resolve_egress
from medusa.inventory.storage import StorageInventory
from medusa.model.compose import (
    normalize_compose_data_dirs,
    normalize_compose_files,
    normalize_compose_services,
    validate_service_mount_refs,
)
from medusa.model.coredns import CorednsModel
from medusa.model.dns import DnsModel, DnsZone, HostNetwork, HostRecord, ManagedMode
from medusa.model.groups import AnsibleGroupsModel
from medusa.model.homepage import HomepageCard, HomepageGroup, HomepageModel
from medusa.model.hosts import AnsibleHost, AnsibleInventoryModel, BootstrapHost
from medusa.model.monitoring import MonitoringModel, MonitoringTarget
from medusa.model.native import NativeModel, NativeSftpService, NativeSftpUser
from medusa.model.network import NetworkHost, NetworkModel
from medusa.model.nixos import (
    NixosContainer,
    NixosHost,
    NixosModel,
    NixosMount,
    NixosNetwork,
    NixosSecret,
)
from medusa.model.services import (
    ComposeService,
    EgressGateway,
    ServiceRecord,
    ServicesModel,
    TraefikRoute,
)
from medusa.model.settings import generated_env_files, secret_sources
from medusa.model.sops import SopsConfigModel, SopsRule
from medusa.model.storage import (
    NfsExport,
    NfsExportClient,
    NfsMount,
    NfsServerExport,
    StorageModel,
)


def _derive_managed_mode(
    ansible_user: str | None, ansible_managed_mode: str | None
) -> ManagedMode:
    if ansible_user is None:
        # Inventory-layer validator already rejects mode-without-user, so
        # this branch only fires for true DNS-only hosts.
        return ManagedMode.NONE
    if ansible_managed_mode is None:
        # Safer default: "full" enables destructive prep flows and must be
        # deliberate. Hosts marked managed without an explicit mode are
        # treated as long-lived limited hosts.
        return ManagedMode.LIMITED
    return ManagedMode(ansible_managed_mode)


def _resolve_network(
    host: HostInventory, defaults: NetworkConfig | None
) -> HostNetwork | None:
    # resolve_host_network owns the override+default merge and all validation
    # (already exercised by the inventory validator). Map its result onto the
    # normalized model type; None for hosts that did not opt in.
    resolved = resolve_host_network(host, defaults)
    if resolved is None:
        return None
    return HostNetwork(
        interface=resolved.interface,
        prefix=resolved.prefix,
        gateway=resolved.gateway,
        nameservers=resolved.nameservers,
    )


def normalize_dns(inventory: DnsInventory) -> DnsModel:
    zones = tuple(
        DnsZone(
            name=zone.name,
            upstreams=tuple(zone.upstreams),
            forwarder_mode=zone.forwarder_mode,
            forwarder_tls_servername=zone.forwarder_tls_servername,
        )
        for zone in inventory.zones
    )

    hosts = tuple(
        HostRecord(
            ip=str(host.ip),
            bootstrap_ip=(
                str(host.bootstrap_ip) if host.bootstrap_ip is not None else None
            ),
            name=host.name,
            zones=tuple(host.zones),
            aliases=tuple(host.aliases),
            fqdns=tuple(
                f"{label}.{zone}"
                for zone in host.zones
                for label in [host.name, *host.aliases]
            ),
            ansible_user=host.ansible_user,
            ansible_groups=tuple(host.ansible_groups),
            managed_mode=_derive_managed_mode(
                host.ansible_user, host.ansible_managed_mode
            ),
            network=_resolve_network(host, inventory.network),
            wildcard=host.wildcard,
            platform=host.platform,
            nixos_guest=host.nixos_guest,
            nixos_disko=host.nixos_disko,
            nixos_admin_keys=tuple(host.nixos_admin_keys),
            nixos_state_version=host.nixos_state_version,
            age_recipient=host.age_recipient,
        )
        for host in inventory.hosts
    )

    return DnsModel(zones=zones, hosts=hosts)


def normalize_ansible_inventory(dns_model: DnsModel) -> AnsibleInventoryModel:
    """Build the ansible inventory model from DNS hosts whose ansible_user
    is set. The hostname is the host's first FQDN, so day-zero SSH works
    once the /etc/hosts bootstrap block is applied (and once DNS is live
    it keeps working unchanged).

    Also derives ``bootstrap_hosts``: every host (managed or DNS-only)
    that declares a ``bootstrap_ip``. These feed the controller's
    /etc/hosts bootstrap block; the address used is the bootstrap_ip,
    not the canonical DNS IP. A DNS-only host with no bootstrap_ip
    contributes nothing here.
    """
    managed: list[AnsibleHost] = []
    bootstrap: list[BootstrapHost] = []
    for host in dns_model.hosts:
        if host.bootstrap_ip is not None:
            if not host.fqdns:
                raise ValueError(
                    f"host {host.name} declares bootstrap_ip but has no "
                    f"FQDN; add at least one zone"
                )
            bootstrap.append(
                BootstrapHost(
                    name=host.name,
                    hostname=host.fqdns[0],
                    ip=host.bootstrap_ip,
                )
            )
        if not host.is_ansible_managed:
            continue
        if not host.fqdns:
            raise ValueError(
                f"host {host.name} is ansible-managed but has no FQDN; "
                f"add at least one zone"
            )
        assert host.ansible_user is not None  # narrow for type checkers
        managed.append(
            AnsibleHost(
                name=host.name,
                hostname=host.fqdns[0],
                ip=host.ip,
                ansible_user=host.ansible_user,
                groups=host.ansible_groups,
                managed_mode=host.managed_mode,
            )
        )
    return AnsibleInventoryModel(
        managed_hosts=tuple(managed),
        bootstrap_hosts=tuple(bootstrap),
    )


def normalize_sops(
    dns_model: DnsModel,
    services_model: ServicesModel,
    secrets_inventory: SecretsInventory,
) -> SopsConfigModel:
    """Build the generated ``.sops.yaml`` model: one creation_rule per distinct
    secret source, whose recipients are the operator keys plus the age recipient
    of every host that references that secret. A crosscut over services (the
    secret->host map) and dns (host age recipients) -- documented like
    render_docs, kept out of any single renderer. A host that references a
    secret but has no age recipient yet is simply omitted from that rule's
    recipients (it gets added once its key is harvested); the gap is surfaced by
    ``sops_recipient_diagnostics``. See T-080.
    """
    recipient_by_host = {
        host.name: host.age_recipient
        for host in dns_model.hosts
        if host.age_recipient is not None
    }
    operators = tuple(secrets_inventory.operators)

    hosts_by_source: dict[str, list[str]] = {}
    for source in services_model.secret_sources:
        hosts_by_source.setdefault(source.source, []).append(source.host)

    rules: list[SopsRule] = []
    for source, hosts in hosts_by_source.items():
        host_recipients = sorted(
            {
                recipient_by_host[host]
                for host in hosts
                if host in recipient_by_host
            }
        )
        ordered: list[str] = []
        for recipient in (*operators, *host_recipients):
            if recipient not in ordered:
                ordered.append(recipient)
        rules.append(
            SopsRule(
                # Anchor at a path-component boundary, not the string start: sops
                # matches path_regex against the file's ABSOLUTE path, so a
                # leading ^ would never match (the path starts with the repo
                # root). (^|/) matches both a relative `secrets/...` and an
                # absolute `/.../secrets/...`. See T-080.
                path_regex=f"(^|/){re.escape(source)}$",
                recipients=tuple(ordered),
            )
        )

    return SopsConfigModel(rules=tuple(rules))


def normalize_network(dns_model: DnsModel) -> NetworkModel:
    """Build the static-networking model from hosts that opted in
    (``manage_network: true``, surfaced as ``HostRecord.network``). The
    canonical ip and prefix are joined into a CIDR ``address`` here so the
    renderer/template only formats. Hosts that did not opt in contribute
    nothing; the model is empty when none did. See T-055."""
    return NetworkModel(
        hosts=tuple(
            NetworkHost(
                name=host.name,
                ip=host.ip,
                address=f"{host.ip}/{host.network.prefix}",
                interface=host.network.interface,
                gateway=host.network.gateway,
                nameservers=host.network.nameservers,
            )
            for host in dns_model.hosts
            if host.network is not None
        )
    )


# Pinned nixpkgs the generated flake builds against. A single point of change;
# the exact rev is frozen by flake.lock when the NixOS deploy path lands (T-075).
NIXPKGS_REF = "github:NixOS/nixpkgs/nixos-25.05"
# Default system.stateVersion for a freshly installed nixos host. Deliberately a
# separate constant from NIXPKGS_REF: stateVersion is pinned at install and must
# NOT move when the flake's nixpkgs pin is bumped (it guards stateful defaults).
# A host can override per-host via nixos_state_version. See T-078.
DEFAULT_NIXOS_STATE_VERSION = "25.05"
SFTP_CHROOT_ROOT = "/srv/sftp"


def normalize_nixos(
    dns_model: DnsModel,
    storage_model: StorageModel,
    services_model: ServicesModel,
    native_model: NativeModel | None = None,
    disko_sources: dict[str, str] | None = None,
) -> NixosModel:
    """Partition the fleet by platform and build the per-host NixOS modules the
    Nix renderer formats. Crosscuts dns + storage + services + native services
    the way the Debian path is split across compose/fstab/networkd, but gathered
    here so the renderer stays formatting-only (renderer contract). Empty when no
    host is on the NixOS platform. See T-073, T-074, T-076."""
    mounts_by_host = dict(storage_model.mounts_by_host)
    containers_by_host: dict[str, list[ComposeService]] = {}
    for compose in services_model.nixos_compose:
        for service in compose.services:
            containers_by_host.setdefault(service.host, []).append(service)

    sftp_users_by_host: dict[str, tuple[NativeSftpUser, ...]] = {}
    if native_model is not None:
        sftp_users_by_host = {
            service.host: service.users for service in native_model.sftp
        }

    disko_sources = disko_sources or {}
    for host in dns_model.hosts_by_platform("nixos"):
        if host.nixos_disko and host.name not in disko_sources:
            raise ValueError(
                f"host '{host.name}' sets nixos_disko but no disko layout was "
                f"found at inventory/nixos/disko/{host.name}.nix -- author it "
                f"(disk layout is operator territory; see the example in the "
                f"medusa repo's templates/nixos/disko/example.nix)"
            )

    hosts = tuple(
        NixosHost(
            name=host.name,
            hostname=host.name,
            network=_nixos_network(host),
            file_systems=tuple(
                NixosMount(
                    mountpoint=mount.mountpoint,
                    device=mount.source,
                    fs_type=mount.type,
                    options=mount.options,
                )
                for mount in mounts_by_host.get(host.name, ())
            ),
            container_backend="docker",
            containers=tuple(
                _nixos_container(service)
                for service in sorted(
                    containers_by_host.get(host.name, ()),
                    key=lambda item: item.name,
                )
            ),
            sftp_users=sftp_users_by_host.get(host.name, ()),
            sftp_chroot_root=(
                SFTP_CHROOT_ROOT if sftp_users_by_host.get(host.name) else None
            ),
            qemu_guest_agent=host.nixos_guest == "vm",
            boot_loader=host.nixos_guest != "lxc",
            state_version=host.nixos_state_version or DEFAULT_NIXOS_STATE_VERSION,
            admin_user=host.ansible_user,
            admin_keys=host.nixos_admin_keys,
            disko_module=(
                f"../disko/{host.name}.nix" if host.nixos_disko else None
            ),
            disko_source=(
                disko_sources.get(host.name) if host.nixos_disko else None
            ),
            secrets=_nixos_secrets(sftp_users_by_host.get(host.name, ())),
            deploy_target=_nixos_deploy_target(host),
        )
        for host in dns_model.hosts_by_platform("nixos")
    )
    return NixosModel(nixpkgs_ref=NIXPKGS_REF, hosts=hosts)


def normalize_native(
    inventory: NativeServicesInventory,
    dns_model: DnsModel,
    storage_model: StorageModel,
) -> NativeModel:
    """Validate + derive host-native services (currently SFTP). A native service
    must target a ``platform: nixos`` host -- no Debian native-service renderer
    exists yet, so a debian-docker target is rejected with a clear diagnostic
    (the explicit not-implemented boundary). Each user's storage ref resolves
    against storage.yaml and must sit under the derived, root-owned chroot so the
    writable area is inside the ChrootDirectory (OpenSSH correctness, derived not
    asked). Authorized keys become secret references delivered by sops-nix
    (T-077). See T-076."""
    hosts_by_name = {host.name: host for host in dns_model.hosts}
    mounts_by_host = dict(storage_model.mounts_by_host)

    services: list[NativeSftpService] = []
    for service in inventory.native_services:
        host = hosts_by_name.get(service.host)
        if host is None:
            raise ValueError(
                f"native service references unknown host: {service.host}"
            )
        if not host.is_nixos:
            raise ValueError(
                f"native service '{service.type}' on host '{service.host}' "
                f"requires platform: nixos (no Debian native-service renderer "
                f"exists). Move the host to NixOS or drop the native service."
            )

        host_mounts = {mount.id: mount for mount in mounts_by_host.get(service.host, ())}
        users: list[NativeSftpUser] = []
        for user in service.users:
            chroot = f"{SFTP_CHROOT_ROOT}/{user.name}"
            mount = host_mounts.get(user.storage)
            if mount is None:
                raise ValueError(
                    f"sftp user '{user.name}' on '{service.host}' references "
                    f"storage '{user.storage}', which is not mounted on that host"
                )
            if not (
                mount.mountpoint == chroot
                or mount.mountpoint.startswith(f"{chroot}/")
            ):
                raise ValueError(
                    f"sftp user '{user.name}' storage '{user.storage}' is mounted "
                    f"at {mount.mountpoint}, outside the user's chroot {chroot}. "
                    f"Mount it under {chroot}/ so the writable area sits inside the "
                    f"root-owned chroot."
                )
            users.append(
                NativeSftpUser(
                    name=user.name,
                    chroot=chroot,
                    home=mount.mountpoint,
                    uid=user.uid,
                    key_names=tuple(user.keys),
                    key_sources=tuple(
                        f"secrets/{ref}.sops.yaml" for ref in user.keys
                    ),
                    key_files=tuple(f"/run/secrets/{ref}" for ref in user.keys),
                )
            )
        services.append(NativeSftpService(host=service.host, users=tuple(users)))
    return NativeModel(sftp=tuple(services))


def _nixos_secrets(
    sftp_users: tuple[NativeSftpUser, ...],
) -> tuple[NixosSecret, ...]:
    """sops-nix secret declarations a host's services reference (T-077). Today
    that is the SFTP authorized-key secrets: each materializes at
    ``/run/secrets/<ref>``, owned by root (sshd reads ``authorizedKeys.keyFiles``
    as root before the chroot). Deduplicated by name and sorted for stable
    output. The reference model is unchanged -- only the on-host deliverer (sops-
    nix vs the Ansible secrets role) differs by platform."""
    by_name: dict[str, NixosSecret] = {}
    for user in sftp_users:
        for name in user.key_names:
            by_name.setdefault(
                name,
                NixosSecret(
                    name=name,
                    sops_file=f"../secrets/{name}.sops.yaml",
                    owner="root",
                    mode="0400",
                ),
            )
    return tuple(by_name[name] for name in sorted(by_name))


def _nixos_deploy_target(host: HostRecord) -> str | None:
    """SSH endpoint for `nixos-rebuild --target-host`, "<user>@<host>". None when
    the host has no managed SSH user (renderable but not reconcilable). Uses the
    canonical fqdn so the controller reaches it the same way Ansible reaches
    Debian hosts; falls back to the ip when no fqdn is declared. See T-075."""
    if host.ansible_user is None:
        return None
    endpoint = host.fqdns[0] if host.fqdns else host.ip
    return f"{host.ansible_user}@{endpoint}"


def _nixos_network(host: HostRecord) -> NixosNetwork | None:
    # Only hosts that opted into managed networking carry a resolved config;
    # others leave systemd.network untouched (NixOS default applies).
    if host.network is None:
        return None
    return NixosNetwork(
        interface=host.network.interface,
        address=f"{host.ip}/{host.network.prefix}",
        gateway=host.network.gateway,
        nameservers=host.network.nameservers,
    )


def _nixos_container(service: ComposeService) -> NixosContainer:
    if service.image is None:
        raise ValueError(
            f"service {service.id} targets a NixOS host but has no image; "
            f"oci-containers needs an image (build-from-source is not supported "
            f"on the NixOS path)"
        )
    return NixosContainer(
        name=service.name,
        image=service.image,
        ports=service.ports,
        volumes=service.volumes,
        environment=tuple(sorted(service.managed_environment.items())),
    )


def _export_path_plan(
    path: str, zfs_root: str | None
) -> tuple[str | None, tuple[str, ...]]:
    """Derive the (dataset, directories) provisioning plan for an export path.

    Convention (T-071): under a ZFS pool root, the first path segment is a
    dataset and every deeper segment is a plain directory inside it. Returns the
    dataset NAME to ensure (or None) and the absolute paths to create+chown (the
    dataset mountpoint plus deeper dirs, or just the export path when the server
    has no pool root).
    """
    if zfs_root is None:
        return None, (path,)
    if path == zfs_root:
        # The export targets the pool root itself; it already exists.
        return None, ()
    prefix = zfs_root + "/"
    if not path.startswith(prefix):
        raise ValueError(
            f"export path {path} is outside the declared zfs_root {zfs_root}"
        )
    segments = path[len(prefix):].split("/")
    dataset = f"{zfs_root.lstrip('/')}/{segments[0]}"
    directories: list[str] = []
    accumulator = zfs_root
    for segment in segments:
        accumulator = f"{accumulator}/{segment}"
        directories.append(accumulator)
    return dataset, tuple(directories)


def normalize_storage(
    inventory: StorageInventory,
    dns_model: DnsModel,
) -> StorageModel:
    hosts_by_name = {host.name: host for host in dns_model.hosts}
    known_hosts = set(hosts_by_name)

    unknown_servers = sorted(
        {
            export.server
            for export in inventory.exports
            if export.server not in known_hosts
        }
    )
    unknown_config_servers = sorted(
        {
            server.name
            for server in inventory.servers
            if server.name not in known_hosts
        }
    )
    if unknown_config_servers:
        formatted = ", ".join(unknown_config_servers)
        raise ValueError(f"server config references unknown hosts: {formatted}")

    if unknown_servers:
        formatted = ", ".join(unknown_servers)
        raise ValueError(f"exports reference unknown hosts: {formatted}")
    unaddressable_servers = sorted(
        {
            export.server
            for export in inventory.exports
            if not hosts_by_name[export.server].fqdns
        }
    )
    if unaddressable_servers:
        formatted = ", ".join(unaddressable_servers)
        raise ValueError(f"exports reference hosts without FQDNs: {formatted}")

    unknown_clients = sorted(
        {
            host
            for mount in inventory.mounts
            for host in mount.host
            if host not in known_hosts
        }
    )
    if unknown_clients:
        formatted = ", ".join(unknown_clients)
        raise ValueError(f"mounts reference unknown hosts: {formatted}")

    exports = {
        export.id: NfsExport(
            id=export.id,
            server=export.server,
            path=export.path,
            options=tuple(export.options),
        )
        for export in inventory.exports
    }

    mounts = tuple(
        sorted(
            (
                NfsMount(
                    id=mount.id,
                    host=host,
                    export_id=mount.export,
                    server=exports[mount.export].server,
                    server_path=exports[mount.export].path,
                    mountpoint=mount.mountpoint,
                    type=mount.type,
                    options=tuple(mount.options),
                    source=(
                        f"{hosts_by_name[exports[mount.export].server].fqdns[0]}:"
                        f"{exports[mount.export].path}"
                    ),
                    server_ip=hosts_by_name[exports[mount.export].server].ip,
                    # The export authorizes this client by its canonical IP;
                    # the role asserts the live source IP matches (T-059).
                    client_ip=hosts_by_name[host].ip,
                )
                for mount in inventory.mounts
                for host in mount.host
            ),
            key=lambda item: (item.host, item.id),
        )
    )

    mounts_by_host: dict[str, list[NfsMount]] = {}
    for mount in mounts:
        mounts_by_host.setdefault(mount.host, []).append(mount)
    mounts_by_host_tuple = tuple(
        (
            host,
            tuple(sorted(mounts_by_host[host], key=lambda item: item.mountpoint)),
        )
        for host in sorted(mounts_by_host)
    )
    zfs_root_by_server = {
        server.name: server.zfs_root for server in inventory.servers
    }
    exports_by_server: dict[str, list[NfsServerExport]] = {}
    for export in exports.values():
        export_mounts = sorted(
            (mount for mount in inventory.mounts if mount.export == export.id),
            key=lambda item: item.id,
        )
        clients = tuple(
            sorted(
                (
                    NfsExportClient(
                        host=host,
                        fqdn=hosts_by_name[host].fqdns[0],
                        # Canonical IP, never bootstrap_ip -- the export must
                        # not carry a host's temporary cutover address (T-059).
                        ip=hosts_by_name[host].ip,
                        options=export.options,
                    )
                    for mount in export_mounts
                    for host in mount.host
                ),
                key=lambda item: item.host,
            )
        )
        dataset, directories = _export_path_plan(
            export.path, zfs_root_by_server.get(export.server)
        )
        exports_by_server.setdefault(export.server, []).append(
            NfsServerExport(
                id=export.id,
                path=export.path,
                clients=clients,
                dataset=dataset,
                directories=directories,
            )
        )
    exports_by_server_tuple = tuple(
        (
            server,
            tuple(sorted(exports_by_server[server], key=lambda item: item.path)),
        )
        for server in sorted(exports_by_server)
    )

    zfs_roots_tuple = tuple(
        sorted(
            (server.name, server.zfs_root)
            for server in inventory.servers
            if server.zfs_root is not None
        )
    )

    return StorageModel(
        exports=tuple(sorted(exports.values(), key=lambda item: item.id)),
        exports_by_server=exports_by_server_tuple,
        mounts=mounts,
        mounts_by_host=mounts_by_host_tuple,
        zfs_roots=zfs_roots_tuple,
    )


def normalize_services(
    inventory: ServicesInventory,
    dns_model: DnsModel,
    storage_model: StorageModel | None = None,
) -> ServicesModel:
    services = _effective_services(inventory)
    known_hosts = {host.name for host in dns_model.hosts}

    unknown_hosts = sorted(
        {
            service.host
            for service in services
            if service.host not in known_hosts
        }
    )
    if unknown_hosts:
        formatted = ", ".join(unknown_hosts)
        raise ValueError(f"services reference unknown hosts: {formatted}")

    # A stack renders as one unit per host platform; a stack whose services
    # straddle a Debian and a NixOS host has no coherent output target. Reject
    # it with a clear diagnostic (T-073). Relax later if a real need appears.
    platform_by_host = {host.name: host.platform for host in dns_model.hosts}
    stack_platforms: dict[str, set[str]] = {}
    for service in services:
        if service.stack is None:
            continue
        stack_platforms.setdefault(service.stack, set()).add(
            platform_by_host[service.host]
        )
    cross_platform = sorted(
        stack for stack, platforms in stack_platforms.items() if len(platforms) > 1
    )
    if cross_platform:
        formatted = ", ".join(cross_platform)
        raise ValueError(
            f"stacks span multiple host platforms (debian-docker + nixos): "
            f"{formatted}. Split the stack so each stays on one platform."
        )

    mount_index = validate_service_mount_refs(services, storage_model)

    egress = _resolve_egress_gateway(inventory, services, dns_model)

    service_records = tuple(
        ServiceRecord(
            id=service.id,
            name=service.name,
            host=service.host,
            stack=service.stack,
        )
        for service in sorted(services, key=lambda item: item.id)
    )

    traefik_routes = tuple(
        route
        for service in sorted(services, key=lambda item: item.id)
        for route in [_normalize_traefik_route(service)]
        if route is not None
    )

    compose_services = normalize_compose_services(
        inventory, services, mount_index, egress
    )
    compose_files = normalize_compose_files(inventory, compose_services, egress)
    compose_data_dirs = normalize_compose_data_dirs(compose_services)

    # Partition compose by host platform at the source (not in render_compose):
    # Debian hosts render to compose files, NixOS hosts to oci-containers via
    # normalize_nixos. Each host lands in exactly one bucket. See the Platform
    # Fork Boundary ADR and T-074's deferred double-render note.
    nixos_names = {host.name for host in dns_model.hosts if host.is_nixos}
    debian_compose = tuple(c for c in compose_files if c.host not in nixos_names)
    nixos_compose = tuple(c for c in compose_files if c.host in nixos_names)
    proxies = _validate_proxy_engines(services, traefik_routes)
    _validate_proxy_network_membership(services)

    traefik_routes_by_host: dict[str, tuple[TraefikRoute, ...]] = {
        host: tuple(route for route in traefik_routes if route.host == host)
        for host in proxies
    }

    tunnel_hosts = sorted(
        {service.host for service in compose_services if service.egress == "tunnel"}
    )
    tunnel_services_by_host = {
        host: tuple(
            sorted(
                service.name
                for service in compose_services
                if service.host == host and service.egress == "tunnel"
            )
        )
        for host in tunnel_hosts
    }

    return ServicesModel(
        services=service_records,
        traefik=traefik_routes,
        traefik_routes_by_host=traefik_routes_by_host,
        compose=debian_compose,
        nixos_compose=nixos_compose,
        env_files=generated_env_files(compose_services),
        data_dirs=compose_data_dirs,
        secret_sources=secret_sources(services, egress),
        proxies=proxies,
        tunnel_services_by_host=tunnel_services_by_host,
        egress=egress,
    )


def _resolve_egress_gateway(
    inventory: ServicesInventory,
    services,
    dns_model: DnsModel,
) -> EgressGateway | None:
    """Resolve the egress gateway config when any service is tunneled. The
    gateway must be a known DNS host; its canonical IP becomes the split-DNS
    resolver address tunneled containers point at. Returns None when nothing is
    tunneled (no network/dns wiring needed)."""
    any_tunneled = any(
        resolve_egress(service, inventory.stacks) == "tunnel" for service in services
    )
    if not any_tunneled:
        return None

    # Presence of egress_gateway is guaranteed by ServicesInventory validation
    # whenever a service is tunneled.
    config = inventory.egress_gateway
    host = next(
        (host for host in dns_model.hosts if host.name == config.gateway), None
    )
    if host is None:
        raise ValueError(
            f"egress_gateway.gateway '{config.gateway}' is not a known host"
        )

    # The gateway's split resolver forwards managed zones to CoreDNS; resolve
    # the CoreDNS host's canonical address for that forward.
    coredns_host = next(
        (host for host in dns_model.hosts if host.name == "coredns"), None
    )
    if coredns_host is None:
        raise ValueError(
            "egress gateway split DNS needs a 'coredns' host in the DNS "
            "inventory to forward managed zones to"
        )

    return EgressGateway(
        network_name=config.network_name,
        gateway=config.gateway,
        gateway_address=host.ip,
        interface=config.interface,
        wireguard_secret=config.wireguard_secret,
        dns_upstream=config.dns_upstream,
        coredns_address=coredns_host.ip,
        zones=tuple(zone.name for zone in dns_model.zones),
        tunnel_subnet=config.tunnel_subnet,
        lan_subnets=tuple(config.lan_subnets),
        fwmark=config.fwmark,
        table=config.table,
    )


def _validate_proxy_network_membership(services) -> None:
    proxy_networks: dict[str, set[str]] = {
        service.host: set(service.compose.networks if service.compose else [])
        for service in services
        if service.proxy is not None
    }

    unreachable: list[str] = []
    for service in services:
        if service.route is None or service.route.host is None:
            continue
        if service.proxy is not None:
            continue
        host_networks = proxy_networks.get(service.host)
        if host_networks is None:
            continue
        service_networks = set(service.compose.networks if service.compose else [])
        if not service_networks & host_networks:
            unreachable.append(service.id)

    if unreachable:
        formatted = ", ".join(sorted(unreachable))
        raise ValueError(
            f"routed services share no network with their proxy: {formatted}"
        )


def _validate_proxy_engines(services, traefik_routes) -> dict[str, str]:
    proxies: dict[str, str] = {}
    for service in services:
        if service.proxy is None:
            continue
        if service.host in proxies:
            raise ValueError(
                f"host {service.host} declares more than one proxy service"
            )
        proxies[service.host] = service.proxy

    routed_hosts = {route.host for route in traefik_routes}
    missing = sorted(routed_hosts - proxies.keys())
    if missing:
        formatted = ", ".join(missing)
        raise ValueError(f"hosts have routes but no proxy service: {formatted}")

    return proxies


def _effective_services(inventory: ServicesInventory):
    return [_effective_service(inventory, service) for service in inventory.services]


def _effective_service(inventory: ServicesInventory, service):
    merged: dict = {}
    for preset_name in reversed(service.use):
        merged = _merge_dicts(merged, _resolved_preset(inventory, preset_name))

    local = service.model_dump(exclude_unset=True, exclude_none=True)
    local.pop("use", None)
    merged = _merge_dicts(merged, local)
    merged.setdefault("name", merged["id"])
    return service.__class__.model_validate(merged)


def _resolved_preset(inventory: ServicesInventory, name: str) -> dict:
    preset = inventory.presets[name]
    return preset.model_dump(exclude_unset=True, exclude_none=True)


def _merge_dicts(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _merge_dicts(merged[key], value)
            continue
        merged[key] = value
    return merged


def _normalize_traefik_route(service) -> TraefikRoute | None:
    if service.route is None or service.route.host is None:
        return None

    if service.route.port is None:
        raise ValueError(
            f"route service {service.id} must define port"
        )

    return TraefikRoute(
        name=service.route.name or service.name,
        host=service.host,
        rule=f"Host(`{service.route.host}`)",
        entrypoints=tuple(service.route.entrypoints or ["web"]),
        tls=False if service.route.tls is None else service.route.tls,
        middlewares=tuple(service.route.middlewares or []),
        target_url=f"http://{service.name}:{service.route.port}",
    )


def normalize_homepage(
    services_inventory: ServicesInventory,
    homepage_inventory: HomepageInventory,
    dns_model: DnsModel,
) -> HomepageModel:
    known_hosts = {host.name for host in dns_model.hosts}

    unknown = sorted(
        host.name
        for host in homepage_inventory.hosts
        if host.name not in known_hosts
    )
    if unknown:
        formatted = ", ".join(unknown)
        raise ValueError(f"homepage references unknown hosts: {formatted}")

    declared_order = [host.name for host in homepage_inventory.hosts]
    titles = {
        host.name: host.title or host.name
        for host in homepage_inventory.hosts
    }

    effective_services = _effective_services(services_inventory)

    cards_by_host: dict[str, list[tuple[int, str, HomepageCard]]] = {}
    for service in effective_services:
        if service.homepage is None:
            continue

        card = _build_homepage_card(service)
        order = service.homepage.order if service.homepage.order is not None else 0
        cards_by_host.setdefault(service.host, []).append((order, card.name, card))

    declared_hosts_with_cards = [
        host for host in declared_order if host in cards_by_host
    ]
    extra_hosts = sorted(set(cards_by_host) - set(declared_order))
    ordered_hosts = declared_hosts_with_cards + extra_hosts

    groups = tuple(
        HomepageGroup(
            host=host,
            title=titles.get(host, host),
            cards=tuple(
                card for _, _, card in sorted(
                    cards_by_host[host], key=lambda item: (item[0], item[1])
                )
            ),
        )
        for host in ordered_hosts
    )

    return HomepageModel(
        hosts=_platform_hosts_from_inventory(effective_services, {"homepage"}),
        groups=groups,
        settings=homepage_inventory.settings,
        bookmarks=(
            tuple(homepage_inventory.bookmarks)
            if homepage_inventory.bookmarks is not None
            else None
        ),
        widgets=(
            tuple(homepage_inventory.widgets)
            if homepage_inventory.widgets is not None
            else None
        ),
    )


def _build_homepage_card(service) -> HomepageCard:
    entry = service.homepage
    dumped = entry.model_dump(exclude_none=True)
    dumped.pop("order", None)
    name = dumped.pop("name", None) or service.name

    href = dumped.pop("href", None)
    if href is None:
        if service.route is not None and service.route.host is not None:
            # Follow the route's TLS setting; an http-only route (tls falsey,
            # the default) must not produce an https:// tile that dead-links.
            scheme = "https" if getattr(service.route, "tls", None) else "http"
            href = f"{scheme}://{service.route.host}"
        else:
            raise ValueError(
                f"service {service.id} homepage entry requires href or route.host"
            )

    description = dumped.pop("description", None)
    icon = dumped.pop("icon", None)

    fields: list[tuple[str, object]] = [("href", href)]
    if description is not None:
        fields.append(("description", description))
    if icon is not None:
        fields.append(("icon", icon))
    for key in sorted(dumped):
        fields.append((key, dumped[key]))

    return HomepageCard(name=name, fields=tuple(fields))


def normalize_monitoring(services_inventory: ServicesInventory) -> MonitoringModel:
    effective_services = _effective_services(services_inventory)
    targets: list[MonitoringTarget] = []
    for service in effective_services:
        if service.monitoring is None:
            continue

        spec = service.monitoring
        job = spec.job or service.name
        auto_labels = {
            "service": service.name,
            "host": service.host,
        }
        if service.stack is not None:
            auto_labels["stack"] = service.stack
        merged_labels = {**auto_labels, **spec.labels}

        targets.append(
            MonitoringTarget(
                job=job,
                target=f"{service.name}:{spec.port}",
                metrics_path=spec.path,
                labels=tuple(
                    (key, merged_labels[key]) for key in sorted(merged_labels)
                ),
            )
        )

    targets.sort(key=lambda item: item.job)
    return MonitoringModel(
        hosts=_platform_hosts_from_inventory(
            effective_services, {"grafana", "prometheus"}
        ),
        targets=tuple(targets),
    )


def normalize_ansible_groups(
    dns_model: DnsModel,
    services_model: ServicesModel,
    storage_model: StorageModel,
    homepage_model: HomepageModel,
    monitoring_model: MonitoringModel,
) -> AnsibleGroupsModel:
    # NixOS hosts are driven by the Nix path (T-074/T-075), never by Ansible
    # roles. Strip them from every role group so an operator can't point a role
    # at a NixOS box; they appear only in the dedicated nixos_hosts group.
    nixos_names = {host.name for host in dns_model.hosts if host.is_nixos}

    def _ansible(names: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(name for name in names if name not in nixos_names)

    docker_hosts = _ansible(
        tuple(sorted({compose.host for compose in services_model.compose}))
    )
    storage_hosts = _ansible(
        tuple(sorted({mount.host for mount in storage_model.mounts}))
    )
    nfs_export_hosts = _ansible(
        tuple(server for server, _ in storage_model.exports_by_server)
    )
    coredns_hosts = _platform_hosts(services_model, {"coredns"})
    if not coredns_hosts:
        coredns_hosts = tuple(
            host.name for host in dns_model.hosts if host.name == "coredns"
        )
    coredns_hosts = _ansible(coredns_hosts)
    managed_network_hosts = _ansible(
        tuple(sorted(host.name for host in dns_model.hosts if host.network is not None))
    )
    egress_gateway_hosts = _ansible(
        (services_model.egress.gateway,) if services_model.egress else ()
    )
    # Docker hosts that actually run a tunneled service get the tunnel network
    # + policy routing applied. These are exactly the keys of the per-host
    # tunnel map.
    tunnel_routing_hosts = _ansible(tuple(sorted(services_model.tunnel_services_by_host)))
    nixos_hosts = tuple(sorted(nixos_names))

    traefik_hosts = _ansible(_platform_hosts(services_model, {"traefik"}))
    homepage_hosts = _ansible(homepage_model.hosts)
    monitoring_hosts = _ansible(monitoring_model.hosts)

    fields: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("coredns_hosts", tuple(sorted(coredns_hosts))),
        ("docker_hosts", docker_hosts),
        ("egress_gateway_hosts", egress_gateway_hosts),
        ("homepage_hosts", homepage_hosts),
        ("managed_network_hosts", managed_network_hosts),
        ("monitoring_hosts", monitoring_hosts),
        ("nfs_export_hosts", nfs_export_hosts),
        ("nixos_hosts", nixos_hosts),
        ("storage_hosts", storage_hosts),
        ("traefik_hosts", traefik_hosts),
        ("tunnel_routing_hosts", tunnel_routing_hosts),
    )

    return AnsibleGroupsModel(
        docker_hosts=docker_hosts,
        storage_hosts=storage_hosts,
        nfs_export_hosts=nfs_export_hosts,
        coredns_hosts=tuple(sorted(coredns_hosts)),
        traefik_hosts=traefik_hosts,
        homepage_hosts=homepage_hosts,
        monitoring_hosts=monitoring_hosts,
        managed_network_hosts=managed_network_hosts,
        egress_gateway_hosts=egress_gateway_hosts,
        tunnel_routing_hosts=tunnel_routing_hosts,
        nixos_hosts=nixos_hosts,
        groups=fields,
    )


def normalize_coredns(
    dns_model: DnsModel,
    services_model: ServicesModel,
) -> CorednsModel:
    upstreams = tuple(
        dict.fromkeys(
            upstream for zone in dns_model.zones for upstream in zone.upstreams
        )
    )

    # Forward transport is a property of the single rendered `.` block,
    # not of each zone. Schema lives on zones because that's where the
    # upstream list lives. Collapse the set across zones that actually
    # contribute upstreams; reject divergent configurations rather than
    # silently picking one.
    forwarder_configs = {
        (zone.forwarder_mode, zone.forwarder_tls_servername)
        for zone in dns_model.zones
        if zone.upstreams
    }
    if len(forwarder_configs) > 1:
        descriptions = sorted(
            f"({mode}, servername={servername!r})"
            for mode, servername in forwarder_configs
        )
        raise ValueError(
            "zones with upstreams disagree on forwarder configuration: "
            + ", ".join(descriptions)
            + ". Pick one (forwarder_mode + forwarder_tls_servername) "
            "across every zone that declares upstreams."
        )
    if forwarder_configs:
        forwarder_mode, forwarder_tls_servername = forwarder_configs.pop()
    else:
        forwarder_mode, forwarder_tls_servername = "udp", None

    host_zones = {host.name: host.zones for host in dns_model.hosts}
    # A host gets a wildcard rewrite block if it runs a Medusa-managed proxy
    # (Host-header routing needs it) OR it explicitly opted in via wildcard:
    # true (e.g. a host running its own unmanaged reverse proxy).
    proxy_hosts = set(services_model.proxies.keys())
    wildcard_hosts = {host.name for host in dns_model.hosts if host.wildcard}
    rewrite_hosts = (proxy_hosts | wildcard_hosts) & host_zones.keys()
    rewrite_zones = tuple(
        f"{host_name}.{zone}"
        for host_name in sorted(rewrite_hosts)
        for zone in host_zones[host_name]
    )
    return CorednsModel(
        dns=dns_model,
        upstreams=upstreams,
        rewrite_zones=rewrite_zones,
        forwarder_mode=forwarder_mode,
        forwarder_tls_servername=forwarder_tls_servername,
    )


def _platform_hosts(
    services_model: ServicesModel,
    service_names: set[str],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                service.host
                for compose in services_model.compose
                for service in compose.services
                if service.name in service_names
            }
        )
    )


def _platform_hosts_from_inventory(
    effective_services,
    service_names: set[str],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                service.host
                for service in effective_services
                if service.compose is not None and service.name in service_names
            }
        )
    )
