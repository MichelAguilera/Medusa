from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    inventory_dir_override: Path | None = None
    templates_dir_override: Path | None = None
    generated_dir_override: Path | None = None
    secrets_dir_override: Path | None = None

    @property
    def inventory_dir(self) -> Path:
        return self.inventory_dir_override or self.root / "inventory"

    @property
    def templates_dir(self) -> Path:
        return self.templates_dir_override or self.root / "templates"

    @property
    def generated_dir(self) -> Path:
        return self.generated_dir_override or self.root / "generated"

    @property
    def secrets_dir(self) -> Path:
        return self.secrets_dir_override or self.root / "secrets"

    @property
    def dns_inventory(self) -> Path:
        return self.inventory_dir / "dns.yaml"

    @property
    def services_inventory(self) -> Path:
        return self.inventory_dir / "services.yaml"

    @property
    def storage_inventory(self) -> Path:
        return self.inventory_dir / "storage.yaml"

    @property
    def homepage_inventory(self) -> Path:
        return self.inventory_dir / "homepage.yaml"
