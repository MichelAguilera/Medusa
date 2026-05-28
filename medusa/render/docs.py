from pathlib import Path

from medusa.model.dns import DnsModel
from medusa.model.services import ServicesModel
from medusa.model.storage import StorageModel
from medusa.render.templates import render_template


def render_docs(
    dns_model: DnsModel,
    services_model: ServicesModel,
    storage_model: StorageModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    context = {
        "dns": dns_model,
        "services": services_model,
        "storage": storage_model,
    }

    return {
        generated_dir / "docs" / "inventory.md": render_template(
            templates_dir,
            "docs/inventory.md.j2",
            context,
        )
    }
