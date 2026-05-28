from dataclasses import dataclass
from enum import StrEnum

from medusa.inventory.services import ServicesInventory
from medusa.model.volumes import is_bind_source


class Severity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    message: str


def service_diagnostics(inventory: ServicesInventory) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    for name, preset in sorted(inventory.presets.items()):
        diagnostics.extend(_owner_diagnostics(f"preset {name}", preset))

    for service in inventory.services:
        diagnostics.extend(_owner_diagnostics(f"service {service.id}", service))

    return tuple(diagnostics)


def diagnostic_errors(diagnostics: tuple[Diagnostic, ...]) -> tuple[Diagnostic, ...]:
    return tuple(
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.severity == Severity.ERROR
    )


def diagnostic_warnings(diagnostics: tuple[Diagnostic, ...]) -> tuple[Diagnostic, ...]:
    return tuple(
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.severity == Severity.WARNING
    )


def _owner_diagnostics(owner: str, item) -> list[Diagnostic]:
    return [
        *_volume_diagnostics(owner, item),
        *_setting_diagnostics(owner, item),
        *_image_diagnostics(owner, item),
    ]


def _volume_diagnostics(owner: str, item) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if item.compose is None:
        return diagnostics

    for index, volume in enumerate(item.compose.volumes):
        source = _volume_source(volume)
        if source is None or not _has_interpolation(source):
            continue

        diagnostics.append(
            Diagnostic(
                Severity.ERROR,
                f"{owner} compose.volumes[{index}] uses Compose "
                f"interpolation in bind mount source: {volume}. Model host "
                "paths through service mounts so Medusa can verify them before "
                "Compose starts.",
            )
        )

    return diagnostics


def _setting_diagnostics(owner: str, item) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for name, binding in sorted(item.settings.items()):
        if isinstance(binding.value, str) and _has_interpolation(binding.value):
            diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    f"{owner} setting {name} uses Compose "
                    f"interpolation: {binding.value}. Prefer concrete Medusa "
                    "settings or secrets.",
                )
            )

    return diagnostics


def _image_diagnostics(owner: str, item) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if item.image is None:
        return diagnostics

    if _has_interpolation(item.image):
        diagnostics.append(
            Diagnostic(
                Severity.WARNING,
                f"{owner} image uses Compose interpolation: "
                f"{item.image}. Prefer explicit image tags for reproducible "
                "generated config.",
            )
        )

    if _image_tag(item.image) == "latest":
        diagnostics.append(
            Diagnostic(
                Severity.WARNING,
                f"{owner} image uses floating latest tag: "
                f"{item.image}. Prefer a pinned version tag or digest.",
            )
        )

    return diagnostics


def _has_interpolation(value: str) -> bool:
    return "${" in value


def _volume_source(volume: str) -> str | None:
    if is_bind_source(volume):
        return volume.split(":", 1)[0]
    return None


def _image_tag(image: str) -> str | None:
    if "@" in image:
        image = image.split("@", 1)[0]

    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon <= last_slash:
        return None

    return image[last_colon + 1 :]
