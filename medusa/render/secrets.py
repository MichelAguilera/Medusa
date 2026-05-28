from pathlib import Path

from medusa.model.services import ServicesModel
from medusa.render.templates import render_template


def render_secrets_manifest(
    model: ServicesModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    content = render_template(
        templates_dir,
        "secrets/secrets-manifest.yaml.j2",
        {"secrets": model.secret_sources},
    )
    return {generated_dir / "secrets-manifest.yaml": content}
