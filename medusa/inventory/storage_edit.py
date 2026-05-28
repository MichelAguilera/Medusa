"""Comment-preserving edits to ``inventory/storage.yaml``.

Mirrors the shape of ``inventory/dns_edit.py``: the mutation functions
are pure with respect to the file system and take the loaded ruamel
document as input. Callers own the load / save bracketing so the same
helpers can drive both real writes and dry-run previews.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=2, offset=0)
    y.width = 4096
    return y


def load_storage_doc(path: Path) -> CommentedMap:
    if not path.exists():
        # Treat absence the same as an empty file; the document still has
        # the canonical exports / mounts keys so downstream mutations can
        # be no-ops without a special case.
        doc: CommentedMap = CommentedMap()
        doc["exports"] = CommentedSeq()
        doc["mounts"] = CommentedSeq()
        return doc
    data = _yaml().load(path.read_text(encoding="utf-8"))
    if data is None:
        data = CommentedMap()
    if not isinstance(data, CommentedMap):
        raise ValueError(f"storage inventory must contain a YAML mapping: {path}")
    data.setdefault("exports", CommentedSeq())
    data.setdefault("mounts", CommentedSeq())
    return data


def save_storage_doc(doc: CommentedMap, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        _yaml().dump(doc, fh)
    tmp.replace(path)


def serialize_storage_doc(doc: CommentedMap) -> str:
    buf = StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


def remove_host_from_mounts(doc: CommentedMap, name: str) -> tuple[list[str], list[str]]:
    """Drop ``name`` from every ``mounts[].host`` list. If a mount's host
    list becomes empty, remove the mount entry entirely.

    Returns ``(touched, removed)`` where ``touched`` lists mount ids that
    were modified but still have other hosts, and ``removed`` lists mount
    ids that were removed because their host list emptied.
    """
    mounts = doc.get("mounts") or CommentedSeq()
    touched: list[str] = []
    removed: list[str] = []
    survivors = CommentedSeq()
    for mount in mounts:
        if not isinstance(mount, dict):
            survivors.append(mount)
            continue
        hosts_raw = mount.get("host")
        if isinstance(hosts_raw, str):
            hosts = [hosts_raw]
            scalar = True
        elif isinstance(hosts_raw, list):
            hosts = list(hosts_raw)
            scalar = False
        else:
            survivors.append(mount)
            continue
        if name not in hosts:
            survivors.append(mount)
            continue
        remaining = [h for h in hosts if h != name]
        mount_id = mount.get("id", "")
        if not remaining:
            removed.append(mount_id)
            continue
        if scalar:
            mount["host"] = remaining[0] if len(remaining) == 1 else remaining
        else:
            new_hosts = CommentedSeq(remaining)
            new_hosts.fa.set_flow_style()
            mount["host"] = new_hosts
        touched.append(mount_id)
        survivors.append(mount)
    doc["mounts"] = survivors
    return touched, removed


def remove_mounts_by_export(doc: CommentedMap, export_ids: list[str]) -> list[str]:
    """Remove every mount whose ``export`` is in ``export_ids``. Returns
    the list of removed mount ids in document order."""
    if not export_ids:
        return []
    target = set(export_ids)
    mounts = doc.get("mounts") or CommentedSeq()
    survivors = CommentedSeq()
    removed: list[str] = []
    for mount in mounts:
        if isinstance(mount, dict) and mount.get("export") in target:
            removed.append(mount.get("id", ""))
            continue
        survivors.append(mount)
    doc["mounts"] = survivors
    return removed


def remove_host_exports(doc: CommentedMap, name: str) -> tuple[list[str], list[str]]:
    """Remove every export whose ``server == name``. Returns
    ``(removed_export_ids, orphaned_mount_ids)`` where the orphaned list
    enumerates mounts that pointed at the removed exports (the caller
    decides whether that warrants a hard fail or a warning)."""
    exports = doc.get("exports") or CommentedSeq()
    survivors = CommentedSeq()
    removed_export_ids: list[str] = []
    for export in exports:
        if isinstance(export, dict) and export.get("server") == name:
            removed_export_ids.append(export.get("id", ""))
            continue
        survivors.append(export)
    doc["exports"] = survivors

    if not removed_export_ids:
        return [], []

    removed_set = set(removed_export_ids)
    orphaned: list[str] = []
    for mount in doc.get("mounts") or CommentedSeq():
        if isinstance(mount, dict) and mount.get("export") in removed_set:
            orphaned.append(mount.get("id", ""))
    return removed_export_ids, orphaned
