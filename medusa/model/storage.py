from pydantic import BaseModel, ConfigDict


class NfsExport(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    server: str
    path: str


class NfsMount(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    host: str
    export_id: str
    server: str
    server_path: str
    mountpoint: str
    type: str
    options: tuple[str, ...]
    source: str


class StorageModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    exports: tuple[NfsExport, ...]
    mounts: tuple[NfsMount, ...]
    mounts_by_host: tuple[tuple[str, tuple[NfsMount, ...]], ...]
