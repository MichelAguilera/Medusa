"""Normalized host-native services (T-076): a platform-neutral, fully-derived
form of the "daemon with users" concept. Authorized keys are SSH *public* keys
carried verbatim as plain inventory data (T-078) -- public keys are not
secret."""

from pydantic import BaseModel, ConfigDict


class NativeSftpUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    # Root-owned ChrootDirectory, derived as "/srv/sftp/<name>". OpenSSH requires
    # the chroot dir to be root-owned and not group/world-writable; the writable
    # area is the storage bind beneath it (``home``).
    chroot: str
    # The user's writable home = the resolved storage mountpoint, which sits
    # under ``chroot`` (enforced in normalize). The NFS bind is the writable part.
    home: str
    uid: int | None
    # Authorized SSH public keys, verbatim from inventory. The renderer emits
    # them as `openssh.authorizedKeys.keys`; no sops-nix delivery is involved.
    authorized_keys: tuple[str, ...]


class NativeSftpShare(BaseModel):
    """A shared space (T-084): a named group-writable area visible inside every
    member's chroot at ``shared/<name>``. The actual per-member NFS mounts ride
    the storage model (synthesized from the share declaration); this carries
    what the renderer additionally needs -- the group (name + pinned gid) and
    who belongs to it."""

    model_config = ConfigDict(frozen=True)

    name: str
    # Pinned numeric gid: NFS sec=sys checks numeric ids, so this must agree
    # with the server-side chgrp of the export dir (mirrors NativeSftpUser.uid).
    gid: int
    members: tuple[str, ...]


class NativeSftpService(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    users: tuple[NativeSftpUser, ...]
    shares: tuple[NativeSftpShare, ...] = ()


class NativeModel(BaseModel):
    """Host-native daemons with users (T-076). Empty when no native service is
    declared. A native service on a non-NixOS host is rejected in
    ``normalize_native`` -- no Debian native renderer exists."""

    model_config = ConfigDict(frozen=True)

    sftp: tuple[NativeSftpService, ...]
