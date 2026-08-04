from pydantic import BaseModel, ConfigDict

from medusa.model.hosts import BootstrapHost
from medusa.model.native import NativeSftpShare, NativeSftpUser
from medusa.model.services import (
    ComposeDataDir,
    ComposeFile,
    GeneratedEnvFile,
)
from medusa.model.storage import NfsServerExport


class NixosSshAlias(BaseModel):
    """One fleet ssh alias in the controller's system-wide ssh_config
    (``Host <name> / HostName <fqdn> / User <user>``). Reshaped from the
    managed-host derivation the aliases artifact consumes; the controller's
    own record is excluded (no self-alias). See T-099."""

    model_config = ConfigDict(frozen=True)

    name: str
    hostname: str
    user: str


class NixosController(BaseModel):
    """Control-plane config for the controller host (T-099). Declares what
    ``controller_bootstrap`` applied imperatively: the /etc/hosts bootstrap
    entries (``networking.hosts``) and the fleet ssh aliases
    (``programs.ssh.extraConfig``). Holds no key material; the controller is
    never a secrets recipient (T-080)."""

    model_config = ConfigDict(frozen=True)

    etc_hosts: tuple[BootstrapHost, ...] = ()
    ssh_aliases: tuple[NixosSshAlias, ...] = ()


class NixosNetwork(BaseModel):
    """Render-ready systemd-networkd config for a NixOS host. Derived from the
    same dns.yaml inputs as the Debian systemd-networkd unit (interface, prefix,
    gateway, nameservers) so both platforms agree on the host's canonical IP.
    ``address`` is the canonical ip joined with the prefix as CIDR. See T-074."""

    model_config = ConfigDict(frozen=True)

    interface: str
    address: str  # canonical ip as CIDR, e.g. "10.0.0.6/24"
    gateway: str
    nameservers: tuple[str, ...]


class NixosMount(BaseModel):
    """One ``fileSystems."<mountpoint>"`` entry. Mirrors the Debian managed
    fstab region exactly -- same source, type, and options -- so a host's NFS
    client mounts are identical whichever platform renders them (T-074)."""

    model_config = ConfigDict(frozen=True)

    mountpoint: str
    device: str  # "<server-fqdn>:/path", or a local path for same-host binds
    fs_type: str  # "nfs" | "nfs4" | "none" (same-host bind, T-096)
    options: tuple[str, ...]


class NixosTunnelClient(BaseModel):
    """Tunnel-routing client config for a NixOS host that runs `egress: tunnel`
    services (T-087/D6; the T-066 mechanism). Same split policy routing as the
    Debian tunnel_routing role: mark tunnel-subnet traffic, route LAN-bound
    traffic direct, everything else into a table whose ONLY route is the
    default via the gateway (fail-closed: gateway down = drop, never a leak).
    Values come from the resolved EgressGateway so both platforms route
    identically."""

    model_config = ConfigDict(frozen=True)

    network_name: str  # the external tunnel docker network (pinned subnet)
    tunnel_subnet: str
    gateway_address: str
    fwmark: int
    table: int
    lan_subnets: tuple[str, ...]


class NixosEgressGateway(BaseModel):
    """This host IS the shared WireGuard egress gateway (T-066 port). Emits
    wg-quick on the host-decrypted tmpfs config (delivered via the T-080
    seam), the generated NAT + fail-closed kill-switch nft ruleset, and the
    split-DNS resolver. A gateway host must be DEDICATED: the kill-switch
    forward chain has a drop policy, so co-located compose stacks' forwarded
    traffic would silently break (enforced in normalize)."""

    model_config = ConfigDict(frozen=True)

    interface: str  # e.g. "wg0"; artifact + secret paths derive from it


class NixosNfsServer(BaseModel):
    """NFS export server config for a NixOS host (T-096).

    Renders to: nfsd on the byte-identically staged exports file, the
    guarded create-only medusa-nfs-provision unit (T-071/T-085 contract;
    ADR ZFS verb fence), and the pool import when ``zfs_pool`` is set."""

    model_config = ConfigDict(frozen=True)

    exports: tuple[NfsServerExport, ...]
    # ZFS pool imported at boot (``boot.zfs.extraPools``), from the server's
    # declared zfs_root by the T-071 convention (pool name = mountpoint sans
    # leading slash). None when the server exports plain directories.
    zfs_pool: str | None
    # networking.hostId — the NixOS ZFS module refuses to build without it.
    # Derived deterministically from the host name so rebuilds keep the same
    # id (a changed hostid makes `zpool import` demand -f). None when no
    # pool (no ZFS module, no requirement).
    host_id: str | None


class NixosStagedConfig(BaseModel):
    """One per-host config artifact a Debian role would copy onto the host
    (traefik dynamic config, homepage services config, prometheus targets),
    staged into the flake tree instead (T-087 config-staging slice).

    ``source`` is the artifact's path under ``generated/``; the staging step
    copies those exact bytes -- it never re-renders, so the file is
    byte-identical to what the Debian role ships. ``dest`` is where the file
    materializes on the host, relative to the stack project dir (stack
    configs) or to the deploy root ``/home/medusa/medusa`` (deploy
    configs)."""

    model_config = ConfigDict(frozen=True)

    source: str
    dest: str


class NixosStack(BaseModel):
    """One compose stack this NixOS host runs (T-087, compose-on-NixOS).

    The compose file and env files are the SAME platform-neutral models the
    Debian compose renderer consumes, formatted with the same templates and
    staged into the flake tree (``generated/nixos/stacks/<host>/<name>/``);
    on the host they sync to the Debian-identical stacks root and a per-stack
    unit runs compose up. ``unit_suffix`` is the stack name made unit-safe
    (``media/immich`` -> ``media-immich``)."""

    model_config = ConfigDict(frozen=True)

    name: str
    unit_suffix: str
    compose_file: ComposeFile
    env_files: tuple[GeneratedEnvFile, ...]
    # Resources declared `external: true` in the stack: `docker compose up`
    # refuses to create them, so the stack unit pre-creates them idempotently
    # (mirror of the Debian compose role's external-resources step).
    external_networks: tuple[str, ...]
    external_volumes: tuple[str, ...]
    # Per-host configs staged INTO this stack's tree (traefik/homepage): the
    # containers bind them relatively (./traefik/dynamic, ./homepage/config),
    # so placing them in the staged stack dir gives sync, restart triggers,
    # and generation rollback with no extra machinery. Empty for most stacks.
    config_files: tuple[NixosStagedConfig, ...] = ()


class NixosStagedSecret(BaseModel):
    """One encrypted SOPS file staged into the flake tree (T-087 port of the
    T-080 seam). Store-safe: it is ciphertext; the host's ``medusa-secrets``
    unit decrypts it to tmpfs with its own ssh host key (ssh-to-age).
    ``staged`` is the path both under ``generated/nixos/secrets-enc/`` and
    ``/etc/medusa-secrets-enc/`` on the host. ``ciphertext`` is read verbatim
    at the inventory boundary (cli) and carried on the model so the renderer
    stays a pure formatter."""

    model_config = ConfigDict(frozen=True)

    staged: str
    ciphertext: str


class NixosSecretSetting(BaseModel):
    """One KEY=value line of a decrypted env-mode secret file: the setting name
    and the staged encrypted source its value is extracted from."""

    model_config = ConfigDict(frozen=True)

    setting: str
    staged: str


class NixosSecretEnvFile(BaseModel):
    """One env file the medusa-secrets unit assembles in tmpfs at
    ``/run/medusa-secrets/<destination>`` -- the exact path the generated
    compose files reference on every platform. Grouping of settings into files
    happens here (normalization), not in the template."""

    model_config = ConfigDict(frozen=True)

    destination: str
    owner_user: str  # "medusa" (service) | "root" (system)
    settings: tuple[NixosSecretSetting, ...]


class NixosSecretFile(BaseModel):
    """One file-mode secret decrypted whole to
    ``/run/medusa-secrets/<destination>``."""

    model_config = ConfigDict(frozen=True)

    destination: str
    owner_user: str
    staged: str


class NixosHost(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    hostname: str  # networking.hostName
    # IANA timezone -> time.timeZone. Not cosmetic: without it /etc/localtime
    # does not exist, and a compose service binding /etc/localtime makes
    # docker auto-create the missing source as a directory (mount failure).
    timezone: str | None = None
    # In-fleet registries served over plain HTTP behind the fleet proxy
    # (image registry component matches a fleet route host). Debian hosts
    # carry the same trust in daemon.json; here it renders to
    # virtualisation.docker.daemon.settings."insecure-registries".
    insecure_registries: tuple[str, ...] = ()
    network: NixosNetwork | None
    file_systems: tuple[NixosMount, ...]
    # Compose stacks this host runs (T-087). Empty when the host is
    # native-services-only (the charon shape). Non-empty enables docker, the
    # medusa runtime user, the stacks sync unit, and one unit per stack.
    stacks: tuple[NixosStack, ...]
    # Bind-mount data dirs under the stacks root for this host, created and
    # owned before compose up (same derivation the Debian role consumes from
    # compose-data-dirs.yaml; here they render to tmpfiles rules).
    data_dirs: tuple[ComposeDataDir, ...]
    # Shared WireGuard egress gateway (T-066 port): set when this host is the
    # resolved egress gateway. Mutually exclusive with stacks (dedicated
    # host). None everywhere else.
    egress_gateway: NixosEgressGateway | None = None
    # CoreDNS host (T-056 port): true when this host is the fleet's DNS
    # resolver. Runs CoreDNS on the same generated Corefile + lan.hosts the
    # Debian role deploys (byte-identical, staged in-generation).
    coredns: bool = False
    # Set when this host serves NFS exports (T-096); None everywhere else.
    nfs: NixosNfsServer | None = None
    # Tunnel-routing client (T-087/D6): set when this host runs at least one
    # `egress: tunnel` service. Emits the nft marking + policy-routing units,
    # loose reverse-path filtering, and the pinned-subnet tunnel network
    # pre-create; stack units hard-require the tunnel units so a container
    # can never start before the fail-closed routing is in place.
    tunnel: NixosTunnelClient | None = None
    # Per-host configs materialized under the deploy root /home/medusa/medusa,
    # mirroring the Debian medusa_deploy_root destination. Empty for most
    # hosts.
    deploy_configs: tuple[NixosStagedConfig, ...] = ()
    # Host-side-decrypted secrets this host's services reference (T-087 port of
    # the T-080 seam). staged_secrets drives ciphertext staging + etc entries;
    # the env/file groupings drive the medusa-secrets decrypt script. All empty
    # when the host references no secrets (no unit emitted).
    staged_secrets: tuple[NixosStagedSecret, ...]
    secret_env_files: tuple[NixosSecretEnvFile, ...]
    secret_files: tuple[NixosSecretFile, ...]
    # Host-native SFTP users reshaped into this host's module (T-076). Empty when
    # the host runs no native sftp service. The renderer emits the openssh
    # Match Group + per-user chroot/keys from these.
    sftp_users: tuple[NativeSftpUser, ...]
    # Shared spaces (T-084): pinned-gid groups whose members see a common
    # group-writable mount at shared/<name> inside their chroots. The mounts
    # themselves arrive via file_systems (synthesized into the storage model);
    # this drives the group definitions, memberships, and the sftp umask.
    sftp_shares: tuple[NativeSftpShare, ...] = ()
    # Base dir for the per-user chroots ("<root>/%u" in the Match block). None
    # when the host runs no sftp service. Kept on the model so the template stays
    # formatting-only.
    sftp_chroot_root: str | None
    # services.qemuGuest.enable -- true for "vm" guests (Proxmox/KVM) so the
    # hypervisor can read the guest IP and shut it down gracefully; false for
    # lxc/physical. Derived from the host's nixos_guest. See T-078.
    qemu_guest_agent: bool
    # Whether to emit a bootloader at all. True for vm/physical guests that own
    # a real disk; false for lxc (a container boots from the host kernel, no
    # bootloader). Derived from nixos_guest. See T-078.
    boot_loader: bool
    # Legacy-BIOS variant (T-094): the raw disk GRUB installs to (e.g.
    # /dev/vda) on firmware with no UEFI. None -> systemd-boot/EFI. Only
    # meaningful when boot_loader is true.
    boot_bios_device: str | None
    # system.stateVersion -- the release this host was installed with, pinned for
    # life. See T-078.
    state_version: str
    # Deploy user whose authorized_keys carry admin/deploy SSH access (the host's
    # deploy_user). None when the host has no managed SSH user; then admin_keys
    # is empty and no key block is emitted. See T-078.
    admin_user: str | None
    # Admin/deploy SSH public keys for ``admin_user``. Plain data (not secret).
    # Empty -> no authorizedKeys block (the host won't be reconcilable until the
    # operator adds one). See T-078.
    admin_keys: tuple[str, ...]
    # Import path (relative to hosts/<name>.nix) of the staged disko config, e.g.
    # "../disko/<name>.nix"; None when the host opts out of disko. When set, the
    # host module imports it and the flake adds disko.nixosModules.disko. See
    # T-078.
    disko_module: str | None
    # Verbatim operator-authored disko layout, carried on the model so the
    # renderer stays a pure formatter. None when the host opts out of disko.
    # Disk layout is operator territory (T-071/T-072): Medusa never derives a
    # partition scheme.
    disko_source: str | None
    # SSH endpoint for `nixos-rebuild switch --target-host`, as "<user>@<host>".
    # None when the host has no deploy_user (no managed SSH endpoint) -- such a
    # host can be rendered but not reconciled; the deploy plan warns and skips
    # it. Derived in normalize_nixos so deploy dispatch stays model-driven
    # (Platform Fork Boundary, seam 2). See T-075.
    deploy_target: str | None
    # Declared expected downtime (T-091). The host still renders (its flake
    # config stays a complete picture of intent) but the deploy plan skips it
    # with a notice instead of failing on an unreachable machine.
    dormant: bool = False
    # Control-plane seat (T-099): set when this host is the controller. Drives
    # the control-plane block in the host module and the deploy plan's local
    # switch (the seat cannot --target-host itself). None everywhere else.
    controller: NixosController | None = None


class NixosModel(BaseModel):
    """Hosts on the NixOS platform, fully derived for the Nix renderer. Empty
    when no host sets ``platform: nixos``. The renderer iterates and formats
    only -- all partitioning and derivation happens in ``normalize_nixos``
    (renderer contract). ``nixpkgs_ref`` pins the flake input. See T-073,
    T-074."""

    model_config = ConfigDict(frozen=True)

    nixpkgs_ref: str
    hosts: tuple[NixosHost, ...]
    # SSH public keys baked into the generated installer ISO's live root
    # (T-089). Non-empty emits generated/nixos/installer.nix and the flake's
    # `packages.<system>.installer` output (`nix build <flake>#installer`).
    # One generic image serves every host -- it carries only these keys.
    installer_keys: tuple[str, ...] = ()
