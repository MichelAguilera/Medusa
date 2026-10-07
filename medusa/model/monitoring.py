from pydantic import BaseModel, ConfigDict

NODE_EXPORTER_PORT = 9100


class MonitoringTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    job: str
    target: str
    metrics_path: str
    labels: tuple[tuple[str, str], ...]


class MonitoringJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    job: str
    metrics_path: str
    targets: tuple[MonitoringTarget, ...]


class MonitoringModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    hosts: tuple[str, ...]
    targets: tuple[MonitoringTarget, ...]
    jobs: tuple[MonitoringJob, ...] = ()
    # Hosts whose grafana and prometheus share a stack network: the only
    # case a rendered datasource URL (http://prometheus:9090) resolves.
    datasource_hosts: tuple[str, ...] = ()
