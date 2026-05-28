from pydantic import BaseModel, ConfigDict


class NfsExport(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    server: str
    path: str
    options: tuple[str, ...]


class NfsExportClient(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    fqdn: str
    options: tuple[str, ...]


class NfsServerExport(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    path: str
    clients: tuple[NfsExportClient, ...]


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
    exports_by_server: tuple[tuple[str, tuple[NfsServerExport, ...]], ...]
    mounts: tuple[NfsMount, ...]
    mounts_by_host: tuple[tuple[str, tuple[NfsMount, ...]], ...]
