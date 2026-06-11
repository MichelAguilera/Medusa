from medusa.inventory.dns import (
    DnsInventory,
    HostInventory,
    NetworkConfig,
    resolve_host_network,
)
from medusa.inventory.homepage import HomepageInventory
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
from medusa.model.network import NetworkHost, NetworkModel
from medusa.model.services import (
    EgressGateway,
    ServiceRecord,
    ServicesModel,
    TraefikRoute,
)
from medusa.model.settings import generated_env_files, secret_sources
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
        exports_by_server.setdefault(export.server, []).append(
            NfsServerExport(id=export.id, path=export.path, clients=clients)
        )
    exports_by_server_tuple = tuple(
        (
            server,
            tuple(sorted(exports_by_server[server], key=lambda item: item.path)),
        )
        for server in sorted(exports_by_server)
    )

    return StorageModel(
        exports=tuple(sorted(exports.values(), key=lambda item: item.id)),
        exports_by_server=exports_by_server_tuple,
        mounts=mounts,
        mounts_by_host=mounts_by_host_tuple,
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
        compose=compose_files,
        env_files=generated_env_files(compose_services),
        data_dirs=compose_data_dirs,
        secret_sources=secret_sources(services),
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
    docker_hosts = tuple(
        sorted({compose.host for compose in services_model.compose})
    )
    storage_hosts = tuple(sorted({mount.host for mount in storage_model.mounts}))
    nfs_export_hosts = tuple(server for server, _ in storage_model.exports_by_server)
    coredns_hosts = _platform_hosts(services_model, {"coredns"})
    if not coredns_hosts:
        coredns_hosts = tuple(
            host.name for host in dns_model.hosts if host.name == "coredns"
        )
    managed_network_hosts = tuple(
        sorted(host.name for host in dns_model.hosts if host.network is not None)
    )
    egress_gateway_hosts = (
        (services_model.egress.gateway,) if services_model.egress else ()
    )
    # Docker hosts that actually run a tunneled service get the tunnel network
    # + policy routing applied. These are exactly the keys of the per-host
    # tunnel map.
    tunnel_routing_hosts = tuple(sorted(services_model.tunnel_services_by_host))

    fields: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("coredns_hosts", tuple(sorted(coredns_hosts))),
        ("docker_hosts", docker_hosts),
        ("egress_gateway_hosts", egress_gateway_hosts),
        ("homepage_hosts", homepage_model.hosts),
        ("managed_network_hosts", managed_network_hosts),
        ("monitoring_hosts", monitoring_model.hosts),
        ("nfs_export_hosts", nfs_export_hosts),
        ("storage_hosts", storage_hosts),
        ("traefik_hosts", _platform_hosts(services_model, {"traefik"})),
        ("tunnel_routing_hosts", tunnel_routing_hosts),
    )

    return AnsibleGroupsModel(
        docker_hosts=docker_hosts,
        storage_hosts=storage_hosts,
        nfs_export_hosts=nfs_export_hosts,
        coredns_hosts=tuple(sorted(coredns_hosts)),
        traefik_hosts=_platform_hosts(services_model, {"traefik"}),
        homepage_hosts=homepage_model.hosts,
        monitoring_hosts=monitoring_model.hosts,
        managed_network_hosts=managed_network_hosts,
        egress_gateway_hosts=egress_gateway_hosts,
        tunnel_routing_hosts=tunnel_routing_hosts,
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
