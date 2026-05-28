"""Pre-write inventory snapshots so any mutating ``medusa`` command can
be undone.

Each successful mutation snapshots the prior bytes of every file it
touched into ``~/.local/state/medusa/inventory-backups/<ts>-<slug>/``.
``medusa undo-last`` restores the most recent snapshot. The store is
append-only (snapshots are not consumed on undo) and bounded by
``MEDUSA_BACKUP_KEEP`` (default 50).

This is intentionally a flat snapshot store rather than a git-backed
audit trail. The longer-term plan is a sidecar inventory git, but a
snapshot dir is enough to unblock the "remove-target by mistake" case
today.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


BACKUP_ENV = "MEDUSA_BACKUP_DIR"
KEEP_ENV = "MEDUSA_BACKUP_KEEP"
DEFAULT_KEEP = 50
MANIFEST_NAME = "manifest.json"


def default_backup_root() -> Path:
    """``$MEDUSA_BACKUP_DIR`` if set, else the XDG state location."""
    override = os.environ.get(BACKUP_ENV)
    if override:
        return Path(override).expanduser()
    state_home = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(state_home).expanduser() / "medusa" / "inventory-backups"


@dataclass(frozen=True)
class Snapshot:
    directory: Path
    timestamp: str
    op: str
    files: tuple[str, ...]


def _slugify(label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")
    return slug[:80] or "op"


def take_snapshot(
    project_root: Path,
    touched_files: Iterable[Path],
    op_label: str,
    *,
    backup_root: Path | None = None,
) -> Path:
    """Copy the pre-write bytes of ``touched_files`` into a new snapshot
    dir. Returns the snapshot directory path. The caller is expected to
    delete this directory (via ``discard_snapshot``) if the write that
    motivated it failed validation.

    Files that do not yet exist on disk are recorded in the manifest
    with an empty ``original_exists`` flag so ``restore`` can unlink
    them on undo instead of restoring an empty file.
    """
    root = backup_root or default_backup_root()
    root.mkdir(parents=True, exist_ok=True)
    # Microsecond precision so rapid back-to-back mutations sort
    # correctly without a separate disambiguating counter.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    snapshot_dir = root / f"{ts}-{_slugify(op_label)}"
    counter = 1
    base = snapshot_dir
    while snapshot_dir.exists():
        counter += 1
        snapshot_dir = base.with_name(f"{base.name}-{counter}")
    snapshot_dir.mkdir(parents=True)

    files_meta: list[dict[str, object]] = []
    for path in touched_files:
        rel = path.relative_to(project_root)
        dest = snapshot_dir / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, dest)
            files_meta.append({"path": str(rel), "original_exists": True})
        else:
            files_meta.append({"path": str(rel), "original_exists": False})

    manifest = {
        "timestamp": ts,
        "op": op_label,
        "files": files_meta,
    }
    (snapshot_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return snapshot_dir


def discard_snapshot(snapshot_dir: Path) -> None:
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)


def gc_snapshots(backup_root: Path | None = None, keep: int | None = None) -> None:
    root = backup_root or default_backup_root()
    if not root.exists():
        return
    if keep is None:
        try:
            keep = int(os.environ.get(KEEP_ENV, DEFAULT_KEEP))
        except ValueError:
            keep = DEFAULT_KEEP
    snapshots = sorted(
        (d for d in root.iterdir() if d.is_dir()),
        key=lambda d: d.name,
    )
    surplus = len(snapshots) - keep
    if surplus <= 0:
        return
    for d in snapshots[:surplus]:
        shutil.rmtree(d, ignore_errors=True)


def list_snapshots(
    backup_root: Path | None = None, limit: int | None = None
) -> list[Snapshot]:
    root = backup_root or default_backup_root()
    if not root.exists():
        return []
    out: list[Snapshot] = []
    snapshots = sorted(
        (d for d in root.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    if limit is not None:
        snapshots = snapshots[:limit]
    for d in snapshots:
        manifest_path = d / MANIFEST_NAME
        if not manifest_path.exists():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        files = tuple(entry["path"] for entry in data.get("files", []))
        out.append(
            Snapshot(
                directory=d,
                timestamp=str(data.get("timestamp", "")),
                op=str(data.get("op", "")),
                files=files,
            )
        )
    return out


def latest_snapshot(backup_root: Path | None = None) -> Snapshot | None:
    items = list_snapshots(backup_root, limit=1)
    return items[0] if items else None


def restore(snapshot: Snapshot, project_root: Path) -> list[Path]:
    """Restore every file in ``snapshot`` to ``project_root``. Returns
    the list of restored paths (absolute). Files that did not exist
    pre-mutation are unlinked from the project tree rather than written
    as empty.
    """
    manifest_path = snapshot.directory / MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[Path] = []
    for entry in data.get("files", []):
        rel = Path(str(entry["path"]))
        target = project_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.get("original_exists", True):
            source = snapshot.directory / "files" / rel
            shutil.copy2(source, target)
        else:
            if target.exists():
                target.unlink()
        restored.append(target)
    return restored
