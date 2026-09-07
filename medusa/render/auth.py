from pathlib import Path

from medusa.model.services import ServicesModel
from medusa.render.templates import render_template


def render_auth(
    model: ServicesModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    files: dict[Path, str] = {}
    for host, auth in sorted(model.auth_by_host.items()):
        if auth.engine != "authelia":
            raise NotImplementedError(
                f"{auth.engine} auth rendering is not implemented (host: {host}). "
                f"See medusa/render/auth.py; only authelia has a renderer today."
            )
        files[generated_dir / "auth" / host / "configuration.yml"] = render_template(
            templates_dir, "auth/authelia.yml.j2", {"auth": auth}
        )
    return files
