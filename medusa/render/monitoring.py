from pathlib import Path

from medusa.model.monitoring import MonitoringModel
from medusa.render.templates import render_template


def render_monitoring(
    monitoring_model: MonitoringModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    return {
        generated_dir / "monitoring" / host / "prometheus-targets.yaml": (
            render_template(
                templates_dir,
                "monitoring/prometheus-targets.yaml.j2",
                {"monitoring": monitoring_model},
            )
        )
        for host in monitoring_model.hosts
    }
