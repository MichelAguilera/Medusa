"""Nginx reverse-proxy renderer stub; see Phase 15 in PHASE.md."""

from pathlib import Path

from medusa.model.services import ServicesModel


def render_nginx(
    model: ServicesModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    hosts = sorted(h for h, engine in model.proxies.items() if engine == "nginx")
    if not hosts:
        return {}
    raise NotImplementedError(
        f"Nginx reverse-proxy rendering is not implemented (hosts: {', '.join(hosts)}). "
        "See medusa/render/nginx.py and Phase 15 in PHASE.md."
    )
