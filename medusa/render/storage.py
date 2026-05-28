from pathlib import Path

from medusa.model.storage import StorageModel
from medusa.render.templates import render_template


def render_storage_manifest(
    model: StorageModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    content = render_template(
        templates_dir,
        "storage/storage-manifest.yaml.j2",
        {"storage": model},
    )
    return {generated_dir / "storage-manifest.yaml": content}
