"""Comment-preserving edits to ``inventory/services.yaml``.

Mirrors the shape of ``inventory/dns_edit.py`` and
``inventory/storage_edit.py``: pure-with-respect-to-FS mutations on a
ruamel round-trip document. Callers own load / save bracketing.
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


def load_services_doc(path: Path) -> CommentedMap:
    if not path.exists():
        raise FileNotFoundError(f"services inventory not found: {path}")
    data = _yaml().load(path.read_text(encoding="utf-8"))
    if data is None:
        data = CommentedMap()
    if not isinstance(data, CommentedMap):
        raise ValueError(f"services inventory must contain a YAML mapping: {path}")
    data.setdefault("services", CommentedSeq())
    return data


def save_services_doc(doc: CommentedMap, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        _yaml().dump(doc, fh)
    tmp.replace(path)


def serialize_services_doc(doc: CommentedMap) -> str:
    buf = StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


def remove_host_services(doc: CommentedMap, name: str) -> list[str]:
    """Remove every service entry whose ``host == name``. Returns the
    list of removed service ids in document order."""
    services = doc.get("services") or CommentedSeq()
    survivors = CommentedSeq()
    removed: list[str] = []
    for service in services:
        if isinstance(service, dict) and service.get("host") == name:
            removed.append(service.get("id", ""))
            continue
        survivors.append(service)
    doc["services"] = survivors
    return removed
