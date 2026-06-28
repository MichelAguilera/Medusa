"""Normalized host-native services (T-076). A platform-neutral, fully-derived
form of the "daemon with users" concept; NixOS hosts reshape it into their
per-host module (T-074), and a Debian renderer can consume the same model later.
Authorized keys are carried as secret *references* (sops sources delivered by
T-077), never key material -- Medusa is not a secret manager."""

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
    # Bare authorized-key secret reference names (the sops.secrets.<name> keys).
    key_names: tuple[str, ...]
    # sops source files T-077 must decrypt + deliver (one per key ref).
    key_sources: tuple[str, ...]
    # Where the rendered NixOS config reads the delivered public keys
    # (authorizedKeys.keyFiles). sops-nix places each source here (T-077).
    key_files: tuple[str, ...]


class NativeSftpService(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    users: tuple[NativeSftpUser, ...]


class NativeModel(BaseModel):
    """Host-native daemons with users. Empty when no native service is declared.
    A native service on a non-NixOS host is rejected in ``normalize_native`` --
    no Debian native renderer exists yet (the explicit not-implemented boundary,
    surfaced as a clear diagnostic rather than a silent skip). See T-076."""

    model_config = ConfigDict(frozen=True)

    sftp: tuple[NativeSftpService, ...]
