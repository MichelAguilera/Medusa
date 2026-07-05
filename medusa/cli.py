import difflib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from medusa.diagnostics import (
    Diagnostic,
    Severity,
    coredns_target_diagnostics,
    diagnostic_errors,
    diagnostic_warnings,
    service_diagnostics,
    sops_recipient_diagnostics,
)
from medusa.generated import stale_files, write_generated
from medusa.inventory.dns import parse_dns_inventory
from medusa.inventory.dns_edit import (
    HostFields,
    add_host,
    clear_bootstrap_ip,
    find_host_index,
    list_hosts,
    list_managed_hosts,
    load_dns_doc,
    remove_host,
    save_dns_doc,
    serialize_dns_doc,
)
from medusa.inventory.homepage import parse_homepage_inventory
from medusa.inventory.loader import load_optional_yaml, load_yaml
from medusa.inventory.native import parse_native_inventory
from medusa.inventory.secrets import parse_secrets_inventory
from medusa.inventory.services import ServicesInventory, parse_services_inventory
from medusa.inventory.services_edit import (
    load_services_doc,
    remove_host_services,
    save_services_doc,
    serialize_services_doc,
)
from medusa.inventory.storage import parse_storage_inventory
from medusa.inventory.storage_edit import (
    load_storage_doc,
    remove_host_exports,
    remove_host_from_mounts,
    remove_mounts_by_export,
    save_storage_doc,
    serialize_storage_doc,
)
from medusa.model.coredns import CorednsModel
from medusa.model.dns import DnsModel
from medusa.model.groups import AnsibleGroupsModel
from medusa.model.homepage import HomepageModel
from medusa.model.hosts import AnsibleInventoryModel
from medusa.model.monitoring import MonitoringModel
from medusa.model.native import NativeModel
from medusa.model.network import NetworkModel
from medusa.model.nixos import NixosModel
from medusa.model.normalize import (
    normalize_ansible_groups,
    normalize_ansible_inventory,
    normalize_coredns,
    normalize_dns,
    normalize_homepage,
    normalize_monitoring,
    normalize_native,
    normalize_network,
    normalize_nixos,
    normalize_services,
    normalize_sops,
    normalize_storage,
)
from medusa.model.services import ServicesModel
from medusa.model.sops import SopsConfigModel
from medusa.model.storage import StorageModel
from medusa.paths import ProjectPaths
from medusa.render.ansible import render_ansible_groups
from medusa.render.caddy import render_caddy
from medusa.render.compose import render_compose
from medusa.render.egress import render_egress
from medusa.render.coredns import render_coredns
from medusa.render.docs import render_docs
from medusa.render.homepage import render_homepage
from medusa.render.hosts import render_hosts
from medusa.render.monitoring import render_monitoring
from medusa.render.network import render_network
from medusa.render.nginx import render_nginx
from medusa.render.nixos import render_nixos
from medusa.render.secrets import render_secrets_manifest
from medusa.render.sops import render_sops_config
from medusa.render.storage import render_storage_manifest
from medusa.render.traefik import render_traefik

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

_PIPELINE_PANEL = "Pipeline"
_INVENTORY_PANEL = "Inventory"
ROOT_OPTION = typer.Option(None, help="Project root. Defaults to cwd.")
WARNINGS_OPTION = typer.Option(
    True,
    "--warnings/--no-warnings",
    help="Show non-fatal validation warnings.",
)

DiagnosticHandler = Callable[[tuple[Diagnostic, ...]], None]


@dataclass(frozen=True)
class _Inventory:
    paths: ProjectPaths
    dns_model: DnsModel
    storage_model: StorageModel
    services_inventory: ServicesInventory
    services_model: ServicesModel
    homepage_model: HomepageModel
    monitoring_model: MonitoringModel
    groups_model: AnsibleGroupsModel
    coredns_model: CorednsModel
    ansible_inventory_model: AnsibleInventoryModel
    network_model: NetworkModel
    native_model: NativeModel
    nixos_model: NixosModel
    sops_model: SopsConfigModel


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else None


def _paths(root: Path | None) -> ProjectPaths:
    return ProjectPaths(
        root=(root or Path.cwd()).resolve(),
        inventory_dir_override=_env_path("MEDUSA_INVENTORY_DIR"),
        templates_dir_override=_env_path("MEDUSA_TEMPLATES_DIR"),
        generated_dir_override=_env_path("MEDUSA_GENERATED_DIR"),
        secrets_dir_override=_env_path("MEDUSA_SECRETS_DIR"),
    )


def _load_all(
    paths: ProjectPaths,
    *,
    on_diagnostics: DiagnosticHandler = lambda _: None,
) -> _Inventory:
    dns_model = normalize_dns(parse_dns_inventory(load_yaml(paths.dns_inventory)))
    storage_model = normalize_storage(
        parse_storage_inventory(load_optional_yaml(paths.storage_inventory)),
        dns_model,
    )

    services_inventory = parse_services_inventory(load_yaml(paths.services_inventory))
    _validate_secret_sources(services_inventory, paths)

    diagnostics = service_diagnostics(services_inventory)
    on_diagnostics(diagnostics)
    _fail_on_diagnostic_errors(diagnostics)

    services_model = normalize_services(services_inventory, dns_model, storage_model)
    homepage_model = normalize_homepage(
        services_inventory,
        parse_homepage_inventory(load_optional_yaml(paths.homepage_inventory)),
        dns_model,
    )
    monitoring_model = normalize_monitoring(services_inventory)
    groups_model = normalize_ansible_groups(
        dns_model, services_model, storage_model, homepage_model, monitoring_model
    )
    # Surface the silent DNS-target gap (coredns_hosts member with no
    # ansible_user -> config renders but never deploys). Warning-only.
    on_diagnostics(coredns_target_diagnostics(dns_model, groups_model))
    coredns_model = normalize_coredns(dns_model, services_model)
    ansible_inventory_model = normalize_ansible_inventory(dns_model)
    network_model = normalize_network(dns_model)
    native_model = normalize_native(
        parse_native_inventory(load_optional_yaml(paths.native_inventory)),
        dns_model,
        storage_model,
    )
    nixos_model = normalize_nixos(
        dns_model,
        storage_model,
        services_model,
        native_model,
        _load_disko_sources(dns_model, paths),
        _load_nixos_secret_ciphertexts(dns_model, services_model, paths),
    )
    sops_model = normalize_sops(
        dns_model,
        services_model,
        parse_secrets_inventory(load_optional_yaml(paths.secrets_inventory)),
    )
    # Surface secret-referencing hosts that have no age_recipient yet: under
    # host-side decryption they cannot decrypt their own secrets until their
    # key is harvested (T-080). Warning-only.
    on_diagnostics(sops_recipient_diagnostics(dns_model, services_model))

    return _Inventory(
        paths=paths,
        dns_model=dns_model,
        storage_model=storage_model,
        services_inventory=services_inventory,
        services_model=services_model,
        homepage_model=homepage_model,
        monitoring_model=monitoring_model,
        groups_model=groups_model,
        coredns_model=coredns_model,
        ansible_inventory_model=ansible_inventory_model,
        network_model=network_model,
        native_model=native_model,
        nixos_model=nixos_model,
        sops_model=sops_model,
    )


def _load_disko_sources(
    dns_model: DnsModel, paths: ProjectPaths
) -> dict[str, str]:
    """Read each disko-opted nixos host's operator-authored layout from the
    inventory tree (``inventory/nixos/disko/<host>.nix``). Disk layout is
    operator/host config, so it lives beside the rest of the inventory, not in
    the medusa templates repo. Missing files are left out; normalize_nixos turns
    that into a clear diagnostic. See T-078."""
    sources: dict[str, str] = {}
    disko_dir = paths.inventory_dir / "nixos" / "disko"
    for host in dns_model.hosts_by_platform("nixos"):
        if not host.nixos_disko:
            continue
        path = disko_dir / f"{host.name}.nix"
        if path.is_file():
            sources[host.name] = path.read_text(encoding="utf-8")
    return sources


def _load_nixos_secret_ciphertexts(
    dns_model: DnsModel, services_model: ServicesModel, paths: ProjectPaths
) -> dict[str, str]:
    """Read the encrypted SOPS sources referenced by NixOS hosts' services,
    verbatim, for staging into the flake tree (T-087). Ciphertext only -- the
    render never sees plaintext (Secrets ADR); decryption happens on the host
    (the medusa-secrets unit, the T-080 seam). Missing files are left out;
    normalize_nixos turns that into a clear diagnostic."""
    nixos_names = {host.name for host in dns_model.hosts_by_platform("nixos")}
    ciphertexts: dict[str, str] = {}
    for source in services_model.secret_sources:
        if source.host not in nixos_names or source.source in ciphertexts:
            continue
        # `source` is always "secrets/<ref>.sops.yaml" (settings.py); on disk
        # the file is secrets_dir/<ref>.sops.yaml -- the two differ when
        # MEDUSA_SECRETS_DIR overrides the default root/secrets location.
        path = paths.secrets_dir / source.source.removeprefix("secrets/")
        if path.is_file():
            ciphertexts[source.source] = path.read_text(encoding="utf-8")
    return ciphertexts


def _validate_secret_sources(
    inventory: ServicesInventory, paths: ProjectPaths
) -> None:
    missing = sorted(
        source for source in _secret_sources(inventory, paths) if not source.exists()
    )
    if missing:
        formatted = ", ".join(
            str(path.relative_to(paths.secrets_dir.parent)) for path in missing
        )
        raise ValueError(f"secret sources are missing: {formatted}")


def _secret_sources(inventory: ServicesInventory, paths: ProjectPaths) -> set[Path]:
    return {
        paths.secrets_dir / f"{setting.secret}.sops.yaml"
        for service in inventory.services
        for setting in service.settings.values()
        if setting.secret is not None
    }


def _render(loaded: _Inventory) -> dict[Path, str]:
    paths = loaded.paths
    services_model = loaded.services_model
    templates_dir = paths.templates_dir
    generated_dir = paths.generated_dir

    return {
        **render_coredns(loaded.coredns_model, templates_dir, generated_dir),
        **render_homepage(loaded.homepage_model, templates_dir, generated_dir),
        **render_traefik(services_model, templates_dir, generated_dir),
        **render_caddy(services_model, templates_dir, generated_dir),
        **render_nginx(services_model, templates_dir, generated_dir),
        **render_compose(services_model, templates_dir, generated_dir),
        **render_egress(services_model, templates_dir, generated_dir),
        **render_monitoring(loaded.monitoring_model, templates_dir, generated_dir),
        **render_secrets_manifest(services_model, templates_dir, generated_dir),
        **render_sops_config(loaded.sops_model, templates_dir, generated_dir),
        **render_storage_manifest(loaded.storage_model, templates_dir, generated_dir),
        **render_ansible_groups(loaded.groups_model, templates_dir, generated_dir),
        **render_hosts(loaded.ansible_inventory_model, templates_dir, generated_dir),
        **render_network(loaded.network_model, templates_dir, generated_dir),
        **render_nixos(loaded.nixos_model, templates_dir, generated_dir),
        **render_docs(
            loaded.dns_model,
            services_model,
            loaded.storage_model,
            templates_dir,
            generated_dir,
        ),
    }


def _diagnostic_handler(show_warnings: bool) -> DiagnosticHandler:
    if not show_warnings:
        return lambda _: None
    return _print_diagnostic_warnings


def _fail(message: str) -> None:
    _echo_status("FAILURE", "red", message, err=True)
    raise typer.Exit(1)


def _succeed(message: str) -> None:
    _echo_status("SUCCESS", "green", message)


def _colorize_diff(diff: str) -> str:
    """Apply git-style ANSI colors to a unified diff. Honours ``NO_COLOR``
    by passing through unchanged (click.style respects the env var)."""
    import os
    if os.environ.get("NO_COLOR"):
        return diff
    lines: list[str] = []
    for raw in diff.splitlines(keepends=True):
        line = raw.rstrip("\n")
        nl = "\n" if raw.endswith("\n") else ""
        if line.startswith("+++") or line.startswith("---"):
            lines.append(typer.style(line, bold=True) + nl)
        elif line.startswith("@@"):
            lines.append(typer.style(line, fg="cyan") + nl)
        elif line.startswith("+"):
            lines.append(typer.style(line, fg="green") + nl)
        elif line.startswith("-"):
            lines.append(typer.style(line, fg="red") + nl)
        else:
            lines.append(raw)
    return "".join(lines)


def _echo_status(
    label: str,
    color: str,
    message: str,
    *,
    err: bool = False,
) -> None:
    formatted = (
        typer.style(label, fg=color, bold=True)
        + typer.style("  ", fg=color)
        + message
    )
    typer.echo(formatted, err=err, color=True)


def _fail_on_diagnostic_errors(diagnostics: tuple[Diagnostic, ...]) -> None:
    errors = diagnostic_errors(diagnostics)
    if errors:
        raise ValueError(_format_diagnostics(errors))


def _print_diagnostic_warnings(diagnostics: tuple[Diagnostic, ...]) -> None:
    warnings = diagnostic_warnings(diagnostics)
    if warnings:
        typer.echo(_format_diagnostics(warnings), err=True, color=True)


def _format_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> str:
    return "\n".join(_format_diagnostic(diagnostic) for diagnostic in diagnostics)


def _format_diagnostic(diagnostic: Diagnostic) -> str:
    color = "red" if diagnostic.severity == Severity.ERROR else "yellow"
    label = diagnostic.severity.value.upper()
    return (
        typer.style(label, fg=color, bold=True)
        + typer.style("  ", fg=color)
        + diagnostic.message
    )


@app.command(rich_help_panel=_PIPELINE_PANEL)
def validate(
    root: Path | None = ROOT_OPTION,
    warnings: bool = WARNINGS_OPTION,
) -> None:
    """Validate inventory files."""
    paths = _paths(root)
    try:
        _load_all(paths, on_diagnostics=_diagnostic_handler(warnings))
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(str(error))

    _succeed("Inventory is valid.")


@app.command(rich_help_panel=_PIPELINE_PANEL)
def render(
    root: Path | None = ROOT_OPTION,
    warnings: bool = WARNINGS_OPTION,
) -> None:
    """Render generated artifacts."""
    paths = _paths(root)
    try:
        loaded = _load_all(paths, on_diagnostics=_diagnostic_handler(warnings))
        files = _render(loaded)
        write_generated(files, paths.generated_dir)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(str(error))

    _succeed(f"Rendered {len(files)} file(s).")


@app.command(rich_help_panel=_PIPELINE_PANEL)
def check(
    root: Path | None = ROOT_OPTION,
    warnings: bool = WARNINGS_OPTION,
) -> None:
    """Fail if generated artifacts are stale."""
    paths = _paths(root)
    try:
        loaded = _load_all(paths, on_diagnostics=_diagnostic_handler(warnings))
        files = _render(loaded)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(str(error))

    stale = stale_files(files, paths.generated_dir)
    if stale:
        formatted = "\n".join(f"- {path.relative_to(paths.root)}" for path in stale)
        _fail(f"Generated files are stale:\n{formatted}")

    _succeed("Generated files are up to date.")


@app.command("nixos-deploy-plan", rich_help_panel=_PIPELINE_PANEL)
def nixos_deploy_plan(
    root: Path | None = ROOT_OPTION,
) -> None:
    """Emit the per-host NixOS deploy plan consumed by `medusactl deploy`.

    One tab-separated ``<host>\\t<user@endpoint>`` line per NixOS host that has a
    managed SSH endpoint; the flake attribute is the host name (so the apply is
    ``nixos-rebuild switch --flake <generated>/nixos#<host> --target-host
    <user@endpoint>``). Hosts with no ansible_user are warned to stderr and
    skipped. Prints nothing (exit 0) when the fleet has no NixOS hosts, so a
    pure-Debian deploy is a no-op. This is deploy dispatch seam 2 of the Platform
    Fork Boundary; medusactl composes the invocation from these lines. See T-075.
    """
    paths = _paths(root)
    try:
        loaded = _load_all(paths, on_diagnostics=lambda _: None)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(str(error))

    for host in loaded.nixos_model.hosts:
        if host.deploy_target is None:
            typer.echo(
                f"nixos host '{host.name}' has no ansible_user; cannot reconcile "
                f"(skipping)",
                err=True,
            )
            continue
        typer.echo(f"{host.name}\t{host.deploy_target}")


@app.command("list-stacks", rich_help_panel=_PIPELINE_PANEL)
def list_stacks_cmd(
    root: Path | None = ROOT_OPTION,
) -> None:
    """List rendered Compose stacks (Debian docker_hosts), one per line.

    Tab-separated ``<stack>\\t<host>\\t<svc,svc,...>``. ``<stack>`` is the stack's
    path under generated/compose/ — the identity ``medusactl compose <verb>
    <stack>`` targets — falling back to the host name for a stackless service
    group. medusactl renders this for ``compose list`` and to surface valid
    targets; NixOS hosts are excluded (they have no compose stacks). See T-082.
    """
    paths = _paths(root)
    try:
        loaded = _load_all(paths, on_diagnostics=lambda _: None)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(str(error))

    rows = sorted(
        (
            (
                compose_file.stack or compose_file.host,
                compose_file.host,
                ",".join(sorted(service.name for service in compose_file.services)),
            )
            for compose_file in loaded.services_model.compose
        ),
        key=lambda row: (row[0], row[1]),
    )
    for stack, host, services in rows:
        typer.echo(f"{stack}\t{host}\t{services}")


# --- Host inventory ops -----------------------------------------------------
#
# Mutate inventory/dns.yaml with comment-preserving round-trip. Each command
# validates the mutated document against the full schema + cross-reference
# pipeline so a bad edit fails before the file is touched. The commands do
# not run `medusa render` themselves; callers (medusactl) chain that
# explicitly so each step is observable.


def _save_dns_with_rollback(doc: Any, paths: ProjectPaths) -> None:
    # Save then validate. Without rollback, a cross-reference failure
    # (e.g. storage exports point at a host we just removed) would leave
    # dns.yaml mutated on disk while the operation reported failure.
    # In-memory bytes are enough; longer-term undo lives in git
    # (T-038 retired the XDG snapshot store).
    original = paths.dns_inventory.read_bytes()
    save_dns_doc(doc, paths.dns_inventory)
    try:
        _load_all(paths, on_diagnostics=lambda _: None)
    except (ValidationError, ValueError, NotImplementedError):
        paths.dns_inventory.write_bytes(original)
        raise


def _save_inventory_atomic(
    paths: ProjectPaths,
    edits: dict[Path, str],
) -> None:
    """Write multiple inventory files as a single atomic unit.

    Captures each target's current bytes in memory, writes the proposed
    contents, runs the full validation pipeline, and restores every
    original if validation fails. Files that did not exist before are
    unlinked on rollback rather than restored to empty.
    """
    originals: dict[Path, bytes | None] = {}
    for path in edits:
        originals[path] = path.read_bytes() if path.exists() else None
    try:
        for path, contents in edits.items():
            path.write_text(contents, encoding="utf-8")
        _load_all(paths, on_diagnostics=lambda _: None)
    except (ValidationError, ValueError, NotImplementedError):
        for path, original in originals.items():
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(original)
        raise


def _emit_dry_run_multi(
    paths: ProjectPaths, edits: dict[Path, str], summary: str
) -> None:
    """Print a unified diff per touched file, then validate the proposed
    state against the full pipeline by writing-then-restoring. Mirrors
    ``_emit_dry_run`` but spans multiple files."""
    for path, proposed in edits.items():
        rel = path.relative_to(paths.root)
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
        )
        if diff:
            typer.echo(_colorize_diff(diff), nl=False, color=True)
        else:
            typer.echo(f"(no textual change to {rel})")

    try:
        _load_all(paths, on_diagnostics=lambda _: None)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(
            f"inventory is already invalid before this edit "
            f"(this is not caused by the dry-run target):\n  {error}\n"
            f"fix the underlying inventory first, then re-run --dry-run"
        )

    snapshots: dict[Path, bytes | None] = {}
    for path in edits:
        snapshots[path] = path.read_bytes() if path.exists() else None
    try:
        for path, contents in edits.items():
            path.write_text(contents, encoding="utf-8")
        try:
            _load_all(paths, on_diagnostics=lambda _: None)
        except (ValidationError, ValueError, NotImplementedError) as error:
            _fail(f"proposed edit fails validation:\n  {error}")
    finally:
        for path, original in snapshots.items():
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(original)

    _succeed(f"DRY-RUN: {summary}; no inventory files modified.")


def _validate_proposed_doc(doc: Any, paths: ProjectPaths) -> None:
    # Write the proposed doc to disk, run the full pipeline against it,
    # then unconditionally restore the original. Used by --dry-run paths
    # so the operator gets the same validation guarantee as a real save.
    original = paths.dns_inventory.read_bytes()
    save_dns_doc(doc, paths.dns_inventory)
    try:
        _load_all(paths, on_diagnostics=lambda _: None)
    finally:
        paths.dns_inventory.write_bytes(original)


def _diff_proposed_doc(doc: Any, paths: ProjectPaths) -> str:
    original = paths.dns_inventory.read_text(encoding="utf-8")
    proposed = serialize_dns_doc(doc)
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{paths.dns_inventory.name}",
            tofile=f"b/{paths.dns_inventory.name}",
        )
    )


def _emit_dry_run(doc: Any, paths: ProjectPaths, summary: str) -> None:
    """Print the diff first so the operator sees the intended edit even
    if validation fails, then check the on-disk baseline before the
    proposed doc so unrelated inventory damage is reported distinctly
    from problems caused by the proposed edit itself."""
    rel = paths.dns_inventory.relative_to(paths.root)
    diff = _diff_proposed_doc(doc, paths)
    if diff:
        typer.echo(_colorize_diff(diff), nl=False, color=True)
    else:
        typer.echo(f"(no textual change to {rel})")

    try:
        _load_all(paths, on_diagnostics=lambda _: None)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(
            f"inventory is already invalid before this edit "
            f"(this is not caused by the dry-run target):\n  {error}\n"
            f"fix the underlying inventory first, then re-run --dry-run"
        )

    try:
        _validate_proposed_doc(doc, paths)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(f"proposed edit fails validation:\n  {error}")

    _succeed(f"DRY-RUN: {summary}; {rel} not modified.")


def _host_dependents(paths: ProjectPaths, name: str) -> list[str]:
    refs: list[str] = []
    storage = parse_storage_inventory(load_optional_yaml(paths.storage_inventory))
    for export in storage.exports:
        if export.server == name:
            refs.append(f"storage export {export.id!r} (server)")
    for mount in storage.mounts:
        if name in mount.host:
            refs.append(f"storage mount {mount.id!r} (host)")
    services = parse_services_inventory(load_yaml(paths.services_inventory))
    for service in services.services:
        if service.host == name:
            refs.append(f"service {service.id!r} (host)")
    return refs


def _host_in_dns(doc: Any, name: str) -> bool:
    return find_host_index(doc, name) is not None


@app.command("add-host", rich_help_panel=_INVENTORY_PANEL)
def add_host_cmd(
    name: str = typer.Argument(..., help="Short host name (e.g. 'host01')."),
    ip: str = typer.Option(..., "--ip", help="IPv4 or IPv6 address."),
    bootstrap_ip: str | None = typer.Option(
        None,
        "--bootstrap-ip",
        help=(
            "Temporary cutover address. When set, the host is seeded into "
            "the controller's /etc/hosts at this address until real DNS is "
            "live for it; clear with `medusa promote-host`. Must differ "
            "from --ip."
        ),
    ),
    zone: list[str] = typer.Option(
        ..., "--zone", help="DNS zone name. Repeat for multiple zones."
    ),
    alias: list[str] = typer.Option(
        [], "--alias", help="Optional DNS alias. Repeat for multiple."
    ),
    ansible_user: str | None = typer.Option(
        None,
        "--ansible-user",
        help="Mark host as ansible-managed and use this SSH user.",
    ),
    ansible_group: list[str] = typer.Option(
        [],
        "--ansible-group",
        help="Ansible inventory group. Repeat for multiple. Ignored unless --ansible-user is set.",
    ),
    managed_mode: str | None = typer.Option(
        None,
        "--managed-mode",
        help=(
            "Host management class: 'full' (medusa-built template; root "
            "SSH prep + hardening audit) or 'limited' (pre-existing/"
            "baremetal; no prep). Requires --ansible-user. Defaults to "
            "'limited' on managed hosts because 'full' is destructive "
            "and must be deliberate."
        ),
    ),
    manage_network: bool = typer.Option(
        False,
        "--manage-network",
        help=(
            "Opt this host into Medusa-managed static networking. Medusa "
            "installs systemd-networkd and performs a guarded DHCP->static "
            "cutover to --ip during deploy. Override fields fall back to the "
            "global network: defaults. NEVER set on Proxmox/bridge hosts."
        ),
    ),
    net_interface: str | None = typer.Option(
        None, "--net-interface", help="Per-host network override: NIC name."
    ),
    net_prefix: int | None = typer.Option(
        None, "--net-prefix", help="Per-host network override: CIDR prefix length."
    ),
    net_gateway: str | None = typer.Option(
        None, "--net-gateway", help="Per-host network override: default gateway."
    ),
    net_nameserver: list[str] = typer.Option(
        [],
        "--net-nameserver",
        help="Per-host network override: nameserver. Repeat for multiple.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing host of the same name instead of failing.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate the proposed edit and print a unified diff; do not write.",
    ),
    root: Path | None = ROOT_OPTION,
) -> None:
    """Append a host to inventory/dns.yaml (or replace with --force)."""
    paths = _paths(root)
    if managed_mode is not None:
        if managed_mode not in ("full", "limited"):
            _fail("--managed-mode must be 'full' or 'limited'")
        if ansible_user is None:
            _fail("--managed-mode requires --ansible-user")
    if not manage_network and (
        net_interface is not None
        or net_prefix is not None
        or net_gateway is not None
        or net_nameserver
    ):
        _fail("--net-* overrides require --manage-network")
    try:
        doc = load_dns_doc(paths.dns_inventory)
        fields = HostFields(
            name=name,
            ip=ip,
            zones=tuple(zone),
            aliases=tuple(alias),
            ansible_user=ansible_user,
            ansible_groups=tuple(ansible_group),
            ansible_managed_mode=managed_mode,  # type: ignore[arg-type]
            bootstrap_ip=bootstrap_ip,
            manage_network=manage_network,
            net_interface=net_interface,
            net_prefix=net_prefix,
            net_gateway=net_gateway,
            net_nameservers=tuple(net_nameserver),
        )
        mutated = add_host(doc, fields, replace=force)
        if not mutated:
            _succeed(f"Host {name!r} already present with identical fields.")
            return

        if dry_run:
            _emit_dry_run(doc, paths, f"would write host {name!r}")
            return

        _save_dns_with_rollback(doc, paths)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(str(error))

    _succeed(f"Host {name!r} written to {paths.dns_inventory.name}.")


@app.command("remove-host", rich_help_panel=_INVENTORY_PANEL)
def remove_host_cmd(
    name: str = typer.Argument(..., help="Host name to remove."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate the proposed edit and print a unified diff; do not write.",
    ),
    remove_mounts: bool = typer.Option(
        False,
        "--remove-mounts",
        help=(
            "Drop the host from every storage mount host list. Mounts whose "
            "host list empties are removed entirely."
        ),
    ),
    remove_exports: bool = typer.Option(
        False,
        "--remove-exports",
        help="Remove every storage export whose server is this host.",
    ),
    remove_services: bool = typer.Option(
        False,
        "--remove-services",
        help="Remove every service entry whose host is this host.",
    ),
    cascade: bool = typer.Option(
        False,
        "--cascade",
        help=(
            "Imply --remove-mounts, --remove-exports, and --remove-services. "
            "Full sweep of references in one shot."
        ),
    ),
    root: Path | None = ROOT_OPTION,
) -> None:
    """Strip a host entry from inventory/dns.yaml.

    By default, refuses to remove a host that is still referenced from
    storage or services. Pass the per-category --remove-* flags (or
    --cascade for all three) to also clean up the references in the
    same atomic operation.
    """
    if cascade:
        remove_mounts = True
        remove_exports = True
        remove_services = True

    paths = _paths(root)
    try:
        dns_doc = load_dns_doc(paths.dns_inventory)
        present = _host_in_dns(dns_doc, name)
        dependents = _host_dependents(paths, name)

        if not present:
            if dependents:
                formatted = "\n  ".join(dependents)
                _fail(
                    f"host {name!r} is not in {paths.dns_inventory.name}, but "
                    f"the following inventory entries still reference it:\n"
                    f"  {formatted}\n"
                    f"fix or remove these refs (the host is already gone "
                    f"from dns.yaml)"
                )
            _succeed(f"Host {name!r} was not present; nothing to do.")
            return

        if dependents and not (remove_mounts or remove_exports or remove_services):
            formatted = "\n  ".join(dependents)
            _fail(
                f"host {name!r} is referenced by:\n  {formatted}\n"
                f"remove or reassign these refs before removing the host, "
                f"or re-run with --cascade (or the more specific "
                f"--remove-mounts / --remove-exports / --remove-services)"
            )

        storage_doc = load_storage_doc(paths.storage_inventory)
        services_doc = load_services_doc(paths.services_inventory)

        actions: list[str] = []
        orphaned_by_exports: list[str] = []

        if remove_exports:
            removed_export_ids, orphaned_by_exports = remove_host_exports(
                storage_doc, name
            )
            if orphaned_by_exports and not remove_mounts:
                formatted = ", ".join(orphaned_by_exports)
                _fail(
                    f"--remove-exports would orphan storage mounts: {formatted}\n"
                    f"also pass --remove-mounts (or --cascade) to clean up "
                    f"those mounts in the same operation"
                )
            for export_id in removed_export_ids:
                actions.append(f"remove storage export {export_id!r}")

        if remove_mounts:
            touched, removed = remove_host_from_mounts(storage_doc, name)
            for mount_id in touched:
                actions.append(f"drop {name!r} from storage mount {mount_id!r}")
            for mount_id in removed:
                actions.append(f"remove empty storage mount {mount_id!r}")
            # Drop any mount that pointed at a removed export so the
            # storage doc does not validate with dangling export refs.
            still_orphaned = [m for m in orphaned_by_exports if m not in removed]
            also_removed = _remove_mounts_by_id(storage_doc, still_orphaned)
            for mount_id in also_removed:
                removed.append(mount_id)
                actions.append(f"remove orphaned storage mount {mount_id!r}")
            if removed:
                service_refs = _services_referencing_mounts(services_doc, removed)
                if service_refs and not remove_services:
                    formatted = ", ".join(sorted(service_refs))
                    _fail(
                        f"--remove-mounts would leave services pointing at "
                        f"removed mounts: {formatted}\n"
                        f"also pass --remove-services (or --cascade)"
                    )

        if remove_services:
            removed_service_ids = remove_host_services(services_doc, name)
            for service_id in removed_service_ids:
                actions.append(f"remove service {service_id!r}")

        remove_host(dns_doc, name)
        actions.append(f"remove host {name!r}")

        edits: dict[Path, str] = {
            paths.dns_inventory: serialize_dns_doc(dns_doc),
            paths.storage_inventory: serialize_storage_doc(storage_doc),
            paths.services_inventory: serialize_services_doc(services_doc),
        }

        # Drop edits that are no-ops to keep the diff tidy and to avoid
        # rewriting files we did not touch.
        for path in list(edits):
            if path.exists() and path.read_text(encoding="utf-8") == edits[path]:
                del edits[path]

        summary = "; ".join(actions) if actions else f"remove host {name!r}"

        if dry_run:
            _emit_dry_run_multi(paths, edits, f"would {summary}")
            return

        flags = []
        if remove_mounts:
            flags.append("--remove-mounts")
        if remove_exports:
            flags.append("--remove-exports")
        if remove_services:
            flags.append("--remove-services")
        if cascade:
            flags.append("--cascade")
        _save_inventory_atomic(paths, edits)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(str(error))

    applied = summary.replace("drop ", "dropped ").replace("remove ", "removed ")
    _succeed(f"Host {name!r}: {applied}.")


def _remove_mounts_by_id(storage_doc: Any, mount_ids: list[str]) -> list[str]:
    if not mount_ids:
        return []
    target = set(mount_ids)
    mounts = storage_doc.get("mounts")
    if mounts is None:
        return []
    survivors = type(mounts)()
    removed: list[str] = []
    for mount in mounts:
        if isinstance(mount, dict) and mount.get("id") in target:
            removed.append(mount.get("id", ""))
            continue
        survivors.append(mount)
    storage_doc["mounts"] = survivors
    return removed


def _services_referencing_mounts(services_doc: Any, mount_ids: list[str]) -> set[str]:
    refs: set[str] = set()
    mount_set = set(mount_ids)
    for service in services_doc.get("services") or []:
        if not isinstance(service, dict):
            continue
        for entry in service.get("mounts") or []:
            if isinstance(entry, dict) and entry.get("mount") in mount_set:
                refs.add(service.get("id", ""))
    return refs


@app.command("promote-host", rich_help_panel=_INVENTORY_PANEL)
def promote_host_cmd(
    name: str = typer.Argument(..., help="Host name to promote."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate the proposed edit and print a unified diff; do not write.",
    ),
    root: Path | None = ROOT_OPTION,
) -> None:
    """Clear bootstrap_ip on a host so it drops out of the controller's
    /etc/hosts bootstrap block. Idempotent: no-op when the host already
    has no bootstrap_ip set. Run this once real DNS is authoritative for
    the host."""
    paths = _paths(root)
    try:
        doc = load_dns_doc(paths.dns_inventory)
        try:
            mutated = clear_bootstrap_ip(doc, name)
        except KeyError:
            _fail(f"host {name!r} is not in {paths.dns_inventory.name}")

        if not mutated:
            _succeed(
                f"Host {name!r} has no bootstrap_ip; nothing to do "
                f"(already promoted)."
            )
            return

        if dry_run:
            _emit_dry_run(doc, paths, f"would clear bootstrap_ip on host {name!r}")
            return

        _save_dns_with_rollback(doc, paths)
    except (ValidationError, ValueError, NotImplementedError) as error:
        _fail(str(error))

    _succeed(f"Host {name!r} promoted; bootstrap_ip cleared.")


@app.command("list-hosts", rich_help_panel=_INVENTORY_PANEL)
def list_hosts_cmd(
    managed_only: bool = typer.Option(
        False,
        "--managed-only",
        help="Show only hosts that have ansible_user set (both F and L).",
    ),
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="Filter by managed mode: 'full' or 'limited'. Implies --managed-only.",
    ),
    root: Path | None = ROOT_OPTION,
) -> None:
    """Print the host inventory in a compact table.

    Mode prefix: ``F`` = full managed, ``L`` = limited managed, ``-`` =
    DNS-only (documented). The medusactl wrapper greps this column, so
    the prefix layout is a public contract.
    """
    paths = _paths(root)
    if mode is not None and mode not in ("full", "limited"):
        _fail("--mode must be 'full' or 'limited'")
    try:
        doc = load_dns_doc(paths.dns_inventory)
    except (ValueError, FileNotFoundError) as error:
        _fail(str(error))

    if mode is not None or managed_only:
        hosts = list_managed_hosts(doc)
    else:
        hosts = list_hosts(doc)

    if mode is not None:
        hosts = tuple(h for h in hosts if _host_mode_letter(h) == mode[0].upper())

    if not hosts:
        _succeed("(no hosts)")
        return

    hosts = _sort_hosts_by_ip(hosts)

    for host in hosts:
        letter = _host_mode_letter(host)
        zones = ",".join(host.get("zones") or [])
        user = host.get("ansible_user") or ""
        groups = ",".join(host.get("ansible_groups") or [])
        typer.echo(
            f"{letter} {host.get('name'):<20} {str(host.get('ip')):<18} "
            f"zones={zones:<12} user={user:<10} groups={groups}"
        )


def _sort_hosts_by_ip(hosts: Any) -> tuple[dict[str, Any], ...]:
    """Sort hosts by numeric IP, then name. Malformed IPs sort last
    (warned to stderr) so a mid-edit inventory still prints.
    """
    import ipaddress

    def key(host: dict[str, Any]) -> tuple[int, Any, str]:
        ip_str = str(host.get("ip") or "")
        try:
            addr = ipaddress.ip_address(ip_str)
            # IPv4 sorts before IPv6 (bucket 0 vs 1). Within a bucket,
            # ipaddress objects compare numerically.
            bucket = 0 if isinstance(addr, ipaddress.IPv4Address) else 1
            return (bucket, addr, str(host.get("name") or ""))
        except ValueError:
            typer.echo(
                f"WARNING  host {host.get('name')!r} has malformed ip "
                f"{ip_str!r}; sorting last",
                err=True,
            )
            # Bucket 2 keeps malformed entries after both v4 and v6.
            # ipaddress.IPv6Address(0) is just a sentinel so the tuple
            # element types match the other branch.
            return (2, ipaddress.IPv6Address(0), str(host.get("name") or ""))

    return tuple(sorted(hosts, key=key))


def _host_mode_letter(host: dict[str, Any]) -> str:
    """Map a raw dns.yaml host entry to its list-hosts prefix letter.

    Mirrors the safer-default rule from normalize: an ansible-managed host
    with no explicit ``ansible_managed_mode`` is treated as ``limited``.
    """
    if not host.get("ansible_user"):
        return "-"
    mode = host.get("ansible_managed_mode") or "limited"
    return "F" if mode == "full" else "L"
