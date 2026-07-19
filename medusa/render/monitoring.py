from pathlib import Path

from medusa.model.monitoring import MonitoringModel
from medusa.render.templates import render_template


def render_monitoring(
    monitoring_model: MonitoringModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    files: dict[Path, str] = {}
    for host in monitoring_model.hosts:
        files[
            generated_dir / "monitoring" / host / "prometheus-targets.yaml"
        ] = render_template(
            templates_dir,
            "monitoring/prometheus-targets.yaml.j2",
            {"monitoring": monitoring_model},
        )
        files[generated_dir / "monitoring" / host / "prometheus.yml"] = (
            render_template(
                templates_dir,
                "monitoring/prometheus.yml.j2",
                {"monitoring": monitoring_model},
            )
        )
    return files
