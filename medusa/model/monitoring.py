from pydantic import BaseModel, ConfigDict


class MonitoringTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    job: str
    target: str
    metrics_path: str
    labels: tuple[tuple[str, str], ...]


class MonitoringModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    hosts: tuple[str, ...]
    targets: tuple[MonitoringTarget, ...]
