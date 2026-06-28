from pathlib import Path

from medusa.model.sops import SopsConfigModel
from medusa.render.templates import render_template


def render_sops_config(
    model: SopsConfigModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    """Render the generated ``.sops.yaml`` from the per-secret recipient rules.

    Lives under ``generated/`` (not the repo root) so it stays a generated,
    staleness-checked artifact; operators point sops at it with
    ``--config generated/.sops.yaml``. See T-080.
    """
    content = render_template(
        templates_dir,
        "sops/sops.yaml.j2",
        {"rules": model.rules},
    )
    return {generated_dir / ".sops.yaml": content}
