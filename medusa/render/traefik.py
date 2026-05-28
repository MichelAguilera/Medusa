from pathlib import Path

from medusa.model.services import ServicesModel
from medusa.render.templates import render_template


def render_traefik(
    model: ServicesModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    return {
        generated_dir / "traefik" / host / "dynamic.yaml": render_template(
            templates_dir,
            "traefik/dynamic.yaml.j2",
            {"traefik": {"routes": model.traefik_routes_by_host.get(host, ())}},
        )
        for host, engine in model.proxies.items()
        if engine == "traefik"
    }
