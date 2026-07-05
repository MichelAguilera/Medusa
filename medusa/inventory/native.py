"""Inventory schema for host-native services -- daemons with users that are
neither HTTP routes nor stack containers (T-076). Today the only type is `sftp`
(OpenSSH `Match Group sftp` chroot + per-user authorized public keys + a
storage bind).
A dedicated file rather than `services.yaml`: native daemons have a different
shape than container services and forcing them into the compose-shaped `services`
record would muddy both. Type-specific (not free-form passthrough) to avoid the
NetBox-clone scope creep the project forbids."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SftpSharedInventory(BaseModel):
    """A shared space (T-084): one NFS export mounted inside the chroot of every
    member at ``/srv/sftp/<member>/shared/<name>``, made mutually writable via a
    pinned-gid group + server-side setgid + the sftp umask. ``storage`` names an
    *export* id (not a mount id): a single authored mount carries exactly one
    mountpoint, but a share needs the same export at N member mountpoints, so
    the per-member mounts are synthesized (``synthesize_sftp_shared_mounts``)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    # Export id from storage.yaml. The export dir on the server must be
    # group-owned by ``gid`` with setgid (mode 2775) -- same operator step as
    # chowning a private export to its user's uid.
    storage: str
    # Pinned numeric gid, mirroring SftpUserInventory.uid: NFS sec=sys checks
    # numeric ids, so the group's gid must agree with the server-side chgrp.
    gid: int
    # Member user names (from this service's ``users``). Empty means every user.
    members: list[str] = Field(default_factory=list)
    # Client mount type/options for the synthesized per-member mounts, same
    # shape and defaults as a storage.yaml mount entry.
    type: Literal["nfs", "nfs4"] = "nfs"
    options: list[str] = Field(default_factory=lambda: ["defaults"])

    @field_validator("name", "storage")
    @classmethod
    def _nonempty_lower(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("shared space name and storage ref cannot be empty")
        return normalized

    @field_validator("name")
    @classmethod
    def _reserved_name(cls, value: str) -> str:
        if value == "sftp":
            raise ValueError(
                "shared space cannot be named 'sftp' (collides with the group "
                "that routes users into the chroot Match block)"
            )
        return value

    @field_validator("gid")
    @classmethod
    def _positive_gid(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("shared space gid must be a positive integer")
        return value

    @field_validator("members")
    @classmethod
    def _normalize_members(cls, value: list[str]) -> list[str]:
        normalized = [member.strip().lower() for member in value]
        if any(not member for member in normalized):
            raise ValueError("shared space members cannot be empty strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("shared space members cannot contain duplicates")
        return normalized

    @field_validator("options")
    @classmethod
    def _normalize_options(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("shared space options cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("shared space options cannot contain duplicates")
        return normalized


class SftpUserInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    # Authorized SSH *public* keys (>=1), verbatim. Public keys are not secret
    # (same reasoning as `nixos_admin_keys`), so they ride as plain inventory
    # data instead of sops references -- resolving the T-078 open question and
    # removing the install-time chicken-and-egg (the host would have needed its
    # own host key harvested before it could decrypt its first secret).
    authorized_keys: list[str]
    # Mount id from storage.yaml. The mount's mountpoint must sit under the
    # user's derived chroot; normalize enforces that so the writable area is
    # inside the root-owned ChrootDirectory.
    storage: str
    # Optional fixed uid (e.g. to match an NFS export's file ownership). Omit to
    # let NixOS allocate. There is no per-user gid: every sftp user shares the
    # `sftp` group so the single `Match Group sftp` chroot block applies to them.
    uid: int | None = None

    @field_validator("name", "storage")
    @classmethod
    def _nonempty_lower(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("sftp user name and storage ref cannot be empty")
        return normalized

    @field_validator("authorized_keys")
    @classmethod
    def _normalize_keys(cls, value: list[str]) -> list[str]:
        normalized = [key.strip() for key in value]
        if any(not key for key in normalized):
            raise ValueError("sftp authorized keys cannot be empty")
        if not normalized:
            raise ValueError("sftp user must declare at least one authorized key")
        if len(set(normalized)) != len(normalized):
            raise ValueError("sftp authorized keys cannot contain duplicates")
        for key in normalized:
            # Guard the plaintext transport: catch a pasted private key, a
            # multi-line blob, or a stale secret ref (the pre-plaintext `keys:`
            # shape) before it lands in a rendered module.
            if "\n" in key or not key.startswith(("ssh-", "ecdsa-", "sk-")):
                raise ValueError(
                    f"sftp authorized key {key[:32]!r}... does not look like an "
                    f"SSH public key (expected 'ssh-...', 'ecdsa-...' or "
                    f"'sk-...'). Only public keys belong in inventory."
                )
        return normalized


class SftpServiceInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sftp"]
    host: str
    users: list[SftpUserInventory]
    shared: list[SftpSharedInventory] = Field(default_factory=list)

    @field_validator("host")
    @classmethod
    def _normalize_host(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("native service host cannot be empty")
        return normalized

    @field_validator("users")
    @classmethod
    def _validate_users(
        cls, value: list[SftpUserInventory]
    ) -> list[SftpUserInventory]:
        if not value:
            raise ValueError("sftp service must declare at least one user")
        names = [user.name for user in value]
        if len(names) != len(set(names)):
            raise ValueError("sftp user names must be unique within a service")
        return value

    @model_validator(mode="after")
    def _validate_shared(self) -> Self:
        share_names = [share.name for share in self.shared]
        if len(share_names) != len(set(share_names)):
            raise ValueError("shared space names must be unique within a service")
        gids = [share.gid for share in self.shared]
        if len(gids) != len(set(gids)):
            raise ValueError("shared space gids must be unique within a service")
        user_names = {user.name for user in self.users}
        for share in self.shared:
            if share.name in user_names:
                raise ValueError(
                    f"shared space '{share.name}' collides with an sftp user "
                    f"name; pick a distinct name (it becomes a Unix group)"
                )
            unknown = sorted(set(share.members) - user_names)
            if unknown:
                formatted = ", ".join(unknown)
                raise ValueError(
                    f"shared space '{share.name}' lists members that are not "
                    f"sftp users on this service: {formatted}"
                )
        return self


class NativeServicesInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    native_services: list[SftpServiceInventory] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_one_per_host(self) -> Self:
        # A host's sftp config is a single openssh daemon; two sftp services on
        # one host would render conflicting Match blocks.
        hosts = [service.host for service in self.native_services]
        if len(hosts) != len(set(hosts)):
            raise ValueError("a host may declare at most one sftp native service")
        return self


def parse_native_inventory(data: dict | None) -> NativeServicesInventory:
    return NativeServicesInventory.model_validate(data or {})
