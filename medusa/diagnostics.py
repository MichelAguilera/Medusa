from dataclasses import dataclass
from enum import StrEnum

from medusa.inventory.services import ServicesInventory
from medusa.inventory.storage import StorageInventory
from medusa.model.dns import DnsModel
from medusa.model.services import ServicesModel
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


def storage_diagnostics(inventory: StorageInventory) -> tuple[Diagnostic, ...]:
    # Mountpoint convention (T-097): permanent client mounts live under /srv.
    diagnostics: list[Diagnostic] = []
    for mount in inventory.mounts:
        if mount.mountpoint != "/srv" and not mount.mountpoint.startswith("/srv/"):
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    message=(
                        f"mount {mount.id} mountpoint {mount.mountpoint} is "
                        "outside /srv. Permanent client mounts belong under "
                        "/srv/<share> (mountpoint convention, T-097)."
                    ),
                )
            )
    return tuple(diagnostics)


def sops_recipient_diagnostics(
    dns_model: DnsModel, services_model: ServicesModel
) -> tuple[Diagnostic, ...]:
    """Warn when a host references a secret but has no age recipient. Under
    host-side decryption (T-080) such a host is left out of its secret's
    generated creation_rule, so it cannot decrypt that secret locally until its
    ssh host key is harvested (`ssh-to-age`) and set as `age_recipient` in
    inventory. Warning-only: the render still succeeds (operators remain
    recipients), but the host's services would be missing their secrets."""
    has_recipient = {
        host.name for host in dns_model.hosts if host.age_recipient is not None
    }
    missing = sorted(
        {
            source.host
            for source in services_model.secret_sources
            if source.host not in has_recipient
        }
    )
    return tuple(
        Diagnostic(
            Severity.WARNING,
            f"host '{name}' references secrets but has no age_recipient; it is "
            f"omitted from those secrets' generated creation_rules and cannot "
            f"decrypt them host-side until its ssh host key is harvested "
            f"(ssh-to-age) and set as age_recipient in inventory (T-080).",
        )
        for name in missing
    )


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
