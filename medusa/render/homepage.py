from pathlib import Path

from medusa.model.homepage import HomepageModel
from medusa.render.templates import render_template


def render_homepage(
    homepage_model: HomepageModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    if not homepage_model.hosts:
        return {}

    services_yaml = render_template(
        templates_dir,
        "homepage/services.yaml.j2",
        {"homepage": homepage_model},
    )
    files: dict[Path, str] = {}
    for host in homepage_model.hosts:
        host_dir = generated_dir / "homepage" / host
        files[host_dir / "services.yaml"] = services_yaml
        if homepage_model.settings is not None:
            files[host_dir / "settings.yaml"] = render_template(
                templates_dir,
                "homepage/settings.yaml.j2",
                {"settings": homepage_model.settings},
            )
        if homepage_model.bookmarks is not None:
            files[host_dir / "bookmarks.yaml"] = render_template(
                templates_dir,
                "homepage/bookmarks.yaml.j2",
                {"bookmarks": list(homepage_model.bookmarks)},
            )
        if homepage_model.widgets is not None:
            files[host_dir / "widgets.yaml"] = render_template(
                templates_dir,
                "homepage/widgets.yaml.j2",
                {"widgets": list(homepage_model.widgets)},
            )
    return files
