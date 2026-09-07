from pathlib import Path

from medusa.model.services import ServicesModel
from medusa.render.templates import render_template


def render_auth(
    model: ServicesModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    auth = model.auth
    if auth is None:
        return {}
    if auth.engine != "authelia":
        raise NotImplementedError(
            f"{auth.engine} auth rendering is not implemented (host: {auth.host}). "
            f"See medusa/render/auth.py; only authelia has a renderer today."
        )
    return {
        generated_dir / "auth" / auth.host / "configuration.yml": render_template(
            templates_dir, "auth/authelia.yml.j2", {"auth": auth}
        )
    }
