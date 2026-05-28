from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def inventory_dir(self) -> Path:
        return self.root / "inventory"

    @property
    def templates_dir(self) -> Path:
        return self.root / "templates"

    @property
    def generated_dir(self) -> Path:
        return self.root / "generated"

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
