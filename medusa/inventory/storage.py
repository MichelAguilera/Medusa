from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NfsExportInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    server: str
    path: str

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

    @model_validator(mode="after")
    def validate_references_and_uniqueness(self) -> Self:
        export_ids = [export.id for export in self.exports]
        if len(export_ids) != len(set(export_ids)):
            raise ValueError("export ids must be unique")

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
