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
    # Canonical DNS IP of the client host (HostRecord.ip), NOT its
    # bootstrap_ip. Exports are emitted by IP so the NFS server authorizes
    # mounts without any reverse-DNS dependency (see T-059). Deriving from
    # the canonical ip keeps the export in lockstep with inventory: change
    # the host IP -> re-render -> export follows, and `medusa check` fails
    # until it does.
    ip: str
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
    # Canonical IPs used by the storage role's export-auth preflight: the
    # server's IP is the route target, and client_ip is the address the
    # server's export authorizes (this host's canonical ip). The role
    # fails fast if the client's live source IP toward the server is not
    # client_ip -- otherwise the mount is denied with an opaque server-side
    # error (T-059).
    server_ip: str
    client_ip: str


class StorageModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    exports: tuple[NfsExport, ...]
    exports_by_server: tuple[tuple[str, tuple[NfsServerExport, ...]], ...]
    mounts: tuple[NfsMount, ...]
    mounts_by_host: tuple[tuple[str, tuple[NfsMount, ...]], ...]
