import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NfsExportInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    server: str
    path: str
    options: list[str] = Field(
        default_factory=lambda: ["rw", "sync", "no_subtree_check", "no_root_squash"]
    )
    # Declared ownership of the export directory itself (T-085). The
    # nfs_exports role applies this on every deploy, so deploys CONVERGE to
    # the declared state -- a manual chown would be clobbered. Defaults =
    # ansible-owned, 0755.
    owner: int = 1000
    group: int = 1000
    mode: str = "0755"

    @field_validator("id", "server")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("export id and server cannot be empty")
        return normalized

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("export path cannot be empty")
        if not normalized.startswith("/"):
            raise ValueError("export path must be absolute")
        return normalized.rstrip("/") or "/"

    @field_validator("options")
    @classmethod
    def normalize_options(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("export options cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("export options cannot contain duplicates")
        return normalized

    @field_validator("owner", "group")
    @classmethod
    def validate_ids(cls, value: int) -> int:
        if value < 0:
            raise ValueError("export owner/group must be a non-negative uid/gid")
        return value

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[0-7]{3,4}", normalized):
            raise ValueError(
                "export mode must be an octal string like '0755' or '2775'"
            )
        return normalized


class NfsServerInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    # Mountpoint of the ZFS pool root for this server (e.g. "/tank"). When set,
    # Medusa auto-creates missing export paths following the operator's
    # convention: the first path segment below the pool root is provisioned as a
    # ZFS dataset, every deeper segment as a plain directory inside it. The pool
    # root dataset NAME is assumed to equal the mountpoint without its leading
    # slash (the `zpool create tank` default => dataset "tank" at "/tank"); a
    # pool mounted somewhere other than "/<dataset>" is out of scope. When unset,
    # export paths are created as plain directories (no dataset). See T-071.
    zfs_root: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("server name cannot be empty")
        return normalized

    @field_validator("zfs_root")
    @classmethod
    def normalize_zfs_root(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("zfs_root cannot be empty when set")
        if not normalized.startswith("/"):
            raise ValueError("zfs_root must be absolute")
        normalized = normalized.rstrip("/") or "/"
        if normalized == "/":
            raise ValueError("zfs_root cannot be the filesystem root")
        return normalized


class NfsMountInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    host: list[str]
    export: str
    mountpoint: str
    type: Literal["nfs", "nfs4"] = "nfs"
    options: list[str] = Field(default_factory=lambda: ["defaults"])

    @field_validator("id", "export")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("mount id and export cannot be empty")
        return normalized

    @field_validator("host", mode="before")
    @classmethod
    def normalize_host(cls, value: str | list[str]) -> list[str]:
        items = [value] if isinstance(value, str) else list(value)
        normalized = [item.strip().lower() for item in items]
        if not normalized:
            raise ValueError("mount host cannot be empty")
        if any(not item for item in normalized):
            raise ValueError("mount host entries cannot be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("mount host entries cannot contain duplicates")
        return normalized

    @field_validator("mountpoint")
    @classmethod
    def normalize_mountpoint(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("mountpoint cannot be empty")
        if not normalized.startswith("/"):
            raise ValueError("mountpoint must be absolute")
        return normalized.rstrip("/") or "/"

    @field_validator("options")
    @classmethod
    def normalize_options(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("mount options cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("mount options cannot contain duplicates")
        return normalized


class StorageInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exports: list[NfsExportInventory] = Field(default_factory=list)
    mounts: list[NfsMountInventory] = Field(default_factory=list)
    servers: list[NfsServerInventory] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references_and_uniqueness(self) -> Self:
        export_ids = [export.id for export in self.exports]
        if len(export_ids) != len(set(export_ids)):
            raise ValueError("export ids must be unique")

        server_names = [server.name for server in self.servers]
        if len(server_names) != len(set(server_names)):
            raise ValueError("server names must be unique")

        mount_ids = [mount.id for mount in self.mounts]
        if len(mount_ids) != len(set(mount_ids)):
            raise ValueError("mount ids must be unique")

        known_exports = set(export_ids)
        unknown_export_refs = sorted(
            mount.export
            for mount in self.mounts
            if mount.export not in known_exports
        )
        if unknown_export_refs:
            formatted = ", ".join(unknown_export_refs)
            raise ValueError(f"mounts reference unknown exports: {formatted}")

        seen_mountpoints: set[tuple[str, str]] = set()
        for mount in self.mounts:
            for host in mount.host:
                key = (host, mount.mountpoint)
                if key in seen_mountpoints:
                    raise ValueError(
                        f"mountpoint {mount.mountpoint} is declared more than once on "
                        f"host {host}"
                    )
                seen_mountpoints.add(key)

        return self


def parse_storage_inventory(data: dict | None) -> StorageInventory:
    return StorageInventory.model_validate(data or {})
