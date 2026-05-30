from pathlib import PurePosixPath
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    conint,
    field_validator,
    model_validator,
)


class RouteInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str | None = None
    name: str | None = None
    port: conint(ge=1, le=65535) | None = None
    entrypoints: list[str] | None = None
    tls: bool | None = None
    middlewares: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def expand_shorthand(cls, data):
        if isinstance(data, str):
            return {"host": data}
        return data

    @field_validator("host", "name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip().lower().removesuffix(".")
        if not normalized:
            raise ValueError("route name fields cannot be empty")
        return normalized

    @field_validator("entrypoints", "middlewares")
    @classmethod
    def normalize_string_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("route lists cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("route lists cannot contain duplicates")
        return normalized


class ComposeInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stack_volumes: dict[str, Any] = Field(default_factory=dict)
    stack_networks: dict[str, Any] = Field(default_factory=dict)
    image: str | None = None
    build: Any = None
    init: bool | None = None
    restart: str | None = None
    command: Any = None
    ports: list[str] = Field(default_factory=list)
    volumes: list[str] = Field(default_factory=list)
    environment: Any = None
    env_file: str | list[str] | None = None
    networks: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    depends_on: Any = None
    healthcheck: dict[str, Any] | None = None
    secrets: list[str] = Field(default_factory=list)
    user: str | None = None
    shm_size: str | None = None
    hostname: str | None = None
    # Owner (UID[:GID]) for this service's relative bind-mount data
    # directories. Medusa creates + chowns them before `compose up` so a
    # non-root container can write its config/data volume. None => Medusa does
    # not touch the dirs (Docker auto-creates them root). See T-065.
    data_owner: str | None = None

    @field_validator("data_owner")
    @classmethod
    def validate_data_owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        parts = value.split(":")
        if len(parts) not in (1, 2) or not all(p.isdigit() for p in parts):
            raise ValueError(
                "compose.data_owner must be 'UID' or 'UID:GID' (numeric), "
                f"got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def reject_legacy_env_and_secrets(self) -> Self:
        if self.image is not None:
            raise ValueError("compose.image is forbidden; use service image")
        if self.environment is not None:
            raise ValueError("compose.environment is forbidden; use service settings")
        if self.env_file is not None:
            raise ValueError("compose.env_file is forbidden; use service settings")
        if self.secrets:
            raise ValueError("compose.secrets is forbidden; use service settings")
        return self

    @field_validator(
        "image",
        "restart",
        "user",
        "shm_size",
        "hostname",
    )
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        if not normalized:
            raise ValueError("compose string fields cannot be empty")
        return normalized

    @field_validator("ports", "volumes", "networks", "labels", "secrets")
    @classmethod
    def normalize_string_lists(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("compose lists cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("compose lists cannot contain duplicates")
        return normalized

    @field_validator("env_file")
    @classmethod
    def normalize_env_file(
        cls,
        value: str | list[str] | None,
    ) -> str | list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("compose env_file cannot be empty")
            return normalized
        return cls.normalize_string_lists(value)


class ServiceSettingInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | int | float | bool | None = None
    secret: str | None = None
    delivery: Literal["env", "file", "file_env"] | None = None
    file_env: str | None = None

    @model_validator(mode="before")
    @classmethod
    def expand_shorthand(cls, data):
        if isinstance(data, dict):
            return data
        return {"value": data}

    @field_validator("secret")
    @classmethod
    def normalize_secret_source(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip().removesuffix(".sops.yaml")
        if not normalized:
            raise ValueError("secret source cannot be empty")

        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("secret source must be a relative path under secrets")
        return normalized

    @model_validator(mode="after")
    def validate_single_source(self) -> Self:
        if self.value is not None and self.secret is not None:
            raise ValueError("setting cannot define both value and secret")
        if self.value is None and self.secret is None:
            raise ValueError("setting must define value or secret")
        if self.secret is None and self.delivery is not None:
            raise ValueError("delivery is only valid for secret settings")
        if self.secret is None and self.file_env is not None:
            raise ValueError("file_env is only valid for secret settings")
        if self.delivery == "file_env" and self.file_env is None:
            raise ValueError("file_env delivery requires file_env")
        if self.delivery != "file_env" and self.file_env is not None:
            raise ValueError("file_env is only valid with file_env delivery")
        return self


class MonitoringTargetInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: conint(ge=1, le=65535)
    path: str = "/metrics"
    job: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("/"):
            raise ValueError("monitoring path must start with /")
        return normalized

    @field_validator("job")
    @classmethod
    def normalize_job(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("monitoring job cannot be empty")
        return normalized

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, val in value.items():
            key_n = key.strip()
            val_n = val.strip()
            if not key_n or not val_n:
                raise ValueError("monitoring labels cannot have empty keys or values")
            normalized[key_n] = val_n
        return normalized


class HomepageEntryInventory(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    icon: str | None = None
    href: str | None = None
    description: str | None = None
    order: int | None = None

    @field_validator("name", "icon", "href", "description")
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("homepage string fields cannot be empty")
        return normalized


class ServiceMountInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mount: str
    target: str
    read_only: bool = False

    @field_validator("mount")
    @classmethod
    def normalize_mount(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("mount reference cannot be empty")
        return normalized

    @field_validator("target")
    @classmethod
    def normalize_target(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("mount target cannot be empty")
        if not normalized.startswith("/"):
            raise ValueError("mount target must be absolute")
        return normalized.rstrip("/") or "/"


class ServicePresetInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    image: str | None = None
    route: RouteInventory | None = None
    compose: ComposeInventory | None = None
    settings: dict[str, ServiceSettingInventory] = Field(default_factory=dict)
    homepage: HomepageEntryInventory | None = None
    monitoring: MonitoringTargetInventory | None = None

    @field_validator("name", "image")
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        if not normalized:
            raise ValueError("preset string fields cannot be empty")
        return normalized


class ServiceInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    host: str
    stack: str | None = None
    proxy: Literal["traefik", "caddy", "nginx"] | None = None
    compose: ComposeInventory | None = None
    use: list[str] = Field(default_factory=list)
    image: str | None = None
    route: RouteInventory | None = None
    settings: dict[str, ServiceSettingInventory] = Field(default_factory=dict)
    mounts: list[ServiceMountInventory] = Field(default_factory=list)
    homepage: HomepageEntryInventory | None = None
    monitoring: MonitoringTargetInventory | None = None

    @model_validator(mode="after")
    def imply_compose_from_stack(self) -> Self:
        if self.stack is not None and self.compose is None:
            self.compose = ComposeInventory()
        return self

    @model_validator(mode="after")
    def validate_unique_mount_targets(self) -> Self:
        targets = [mount.target for mount in self.mounts]
        if len(targets) != len(set(targets)):
            raise ValueError(
                f"service {self.id} declares duplicate mount targets"
            )
        return self

    @model_validator(mode="after")
    def require_runtime_source_for_compose(self) -> Self:
        if (
            self.compose is not None
            and self.image is None
            and not self.use
            and self.compose.build is None
        ):
            raise ValueError(
                f"service {self.id} has compose without image, build, or use"
            )
        return self

    @field_validator("id", "name", "host")
    @classmethod
    def normalize_required_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("service id, name, and host cannot be empty")
        return normalized

    @field_validator("stack")
    @classmethod
    def normalize_stack_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        if not normalized:
            raise ValueError("service stack cannot be empty")
        return normalized

    @field_validator("use")
    @classmethod
    def normalize_use(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower() for item in value]
        if any(not item for item in normalized):
            raise ValueError("service use cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("service use cannot contain duplicates")
        return normalized

    @field_validator("image")
    @classmethod
    def normalize_image(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        if not normalized:
            raise ValueError("service image cannot be empty")
        return normalized


class ServicesInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presets: dict[str, ServicePresetInventory] = Field(default_factory=dict)
    services: list[ServiceInventory] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_service_ids(self) -> Self:
        ids = [service.id for service in self.services]
        if len(ids) != len(set(ids)):
            raise ValueError("service ids must be unique")
        return self

    @model_validator(mode="after")
    def validate_secret_settings(self) -> Self:
        for service in self.services:
            self._validate_secret_settings(service.id, service.settings)

        for preset_name, preset in self.presets.items():
            self._validate_secret_settings(f"preset {preset_name}", preset.settings)

        return self

    def _validate_secret_settings(
        self,
        owner: str,
        settings: dict[str, ServiceSettingInventory],
    ) -> None:
        for setting_name, binding in settings.items():
            if binding.secret is not None and binding.delivery is None:
                raise ValueError(f"{owner} setting {setting_name} requires delivery")

    @model_validator(mode="after")
    def validate_presets(self) -> Self:
        unknown_service_presets = sorted(
            preset_name
            for service in self.services
            for preset_name in service.use
            if preset_name not in self.presets
        )
        if unknown_service_presets:
            formatted = ", ".join(unknown_service_presets)
            raise ValueError(f"services reference unknown presets: {formatted}")

        return self


def parse_services_inventory(data: dict) -> ServicesInventory:
    return ServicesInventory.model_validate(data)
