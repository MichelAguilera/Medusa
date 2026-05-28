from pathlib import Path

from medusa.model.coredns import CorednsModel
from medusa.render.templates import render_template


def render_coredns(
    model: CorednsModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    context = {"coredns": model}
    return {
        generated_dir / "coredns" / "Corefile": render_template(
            templates_dir, "coredns/Corefile.j2", context
        ),
        generated_dir / "coredns" / "lan.hosts": render_template(
            templates_dir, "coredns/hosts.j2", context
        ),
    }
