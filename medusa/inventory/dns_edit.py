"""Comment-preserving edits to ``inventory/dns.yaml``.

PyYAML strips comments on round-trip, so we use ruamel.yaml's round-trip
loader for any operation that writes the file. Read-only paths can keep
using PyYAML via the existing loader; this module is only for the
medusa CLI commands that mutate dns.yaml (add-host, remove-host, etc.).

All functions are pure with respect to the file system: they take the
loaded document as input and return a new document or a derived view.
The caller is responsible for ``load_dns_doc`` / ``save_dns_doc``
bracketing.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _yaml() -> YAML:
    """Return a configured round-trip YAML instance.

    Defaults tuned to match the project's existing dns.yaml formatting:
    block style, 2-space indent, sequences not indented further than the
    parent key.
    """
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=2, offset=0)
    y.width = 4096
    return y


def load_dns_doc(path: Path) -> CommentedMap:
    if not path.exists():
        raise FileNotFoundError(f"dns inventory not found: {path}")
    data = _yaml().load(path.read_text(encoding="utf-8"))
    if data is None:
        # File exists but is empty / only whitespace + comments.
        data = CommentedMap()
    if not isinstance(data, CommentedMap):
        raise ValueError(f"dns inventory must contain a YAML mapping: {path}")
    data.setdefault("zones", CommentedSeq())
    data.setdefault("hosts", CommentedSeq())
    return data


def save_dns_doc(doc: CommentedMap, path: Path) -> None:
    """Atomic write: serialise to a sibling tempfile then rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        _yaml().dump(doc, fh)
    tmp.replace(path)


def serialize_dns_doc(doc: CommentedMap) -> str:
    """Return the document as a YAML string using the same formatter as
    ``save_dns_doc``. Used by ``--dry-run`` paths to diff a proposed edit
    against the on-disk file without touching it."""
    buf = StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


@dataclass(frozen=True)
class HostFields:
    name: str
    ip: str
    zones: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    ansible_user: str | None = None
    ansible_groups: tuple[str, ...] = ()
    ansible_managed_mode: Literal["full", "limited"] | None = None
    bootstrap_ip: str | None = None
    # Managed static-networking opt-in + optional per-host override. Unset
    # override fields fall back to the global `network:` defaults at
    # validation time (resolve_host_network). See T-055.
    manage_network: bool = False
    net_interface: str | None = None
    net_prefix: int | None = None
    net_gateway: str | None = None
    net_nameservers: tuple[str, ...] = ()


def find_host_index(doc: CommentedMap, name: str) -> int | None:
    hosts = doc.get("hosts", [])
    for index, entry in enumerate(hosts):
        if isinstance(entry, dict) and entry.get("name") == name:
            return index
    return None


def add_host(doc: CommentedMap, fields: HostFields, *, replace: bool = False) -> bool:
    """Add a host entry to the dns doc.

    Returns True when the document was mutated, False when an entry with
    the same name + identical fields already existed (no-op for
    idempotency). When ``replace`` is False and a host with the same name
    but different fields exists, raises ``ValueError`` and leaves the doc
    untouched. When ``replace`` is True, overwrites in place.
    """
    existing_index = find_host_index(doc, fields.name)
    serialized = _serialize_host(fields)

    if existing_index is None:
        doc["hosts"].append(serialized)
        return True

    existing = doc["hosts"][existing_index]
    if _hosts_equal(existing, serialized):
        return False

    if not replace:
        raise ValueError(
            f"host {fields.name!r} already exists with different fields; "
            f"pass replace=True to overwrite"
        )

    doc["hosts"][existing_index] = serialized
    return True


def remove_host(doc: CommentedMap, name: str) -> bool:
    """Remove the host by name. Returns True when an entry was removed,
    False when no such host was present (idempotent no-op)."""
    index = find_host_index(doc, name)
    if index is None:
        return False
    del doc["hosts"][index]
    return True


def clear_bootstrap_ip(doc: CommentedMap, name: str) -> bool:
    """Remove the bootstrap_ip key from the named host. Returns True when
    the document was mutated, False when the host either does not exist
    or already has no bootstrap_ip (idempotent no-op).

    Raises ``KeyError`` when the host is not in the document; callers can
    distinguish "absent" from "already promoted" if they need to.
    """
    index = find_host_index(doc, name)
    if index is None:
        raise KeyError(name)
    entry = doc["hosts"][index]
    if not isinstance(entry, dict) or "bootstrap_ip" not in entry:
        return False
    del entry["bootstrap_ip"]
    return True


def list_hosts(doc: CommentedMap) -> tuple[dict[str, Any], ...]:
    """Return a tuple of plain-dict host snapshots in document order."""
    out: list[dict[str, Any]] = []
    for entry in doc.get("hosts", []):
        if isinstance(entry, dict):
            out.append(dict(entry))
    return tuple(out)


def list_managed_hosts(doc: CommentedMap) -> tuple[dict[str, Any], ...]:
    return tuple(h for h in list_hosts(doc) if h.get("ansible_user"))


def _serialize_host(fields: HostFields) -> CommentedMap:
    out: CommentedMap = CommentedMap()
    out["name"] = fields.name
    out["ip"] = fields.ip
    if fields.bootstrap_ip is not None:
        out["bootstrap_ip"] = fields.bootstrap_ip
    out["zones"] = CommentedSeq(fields.zones)
    out["zones"].fa.set_flow_style()
    out["aliases"] = CommentedSeq(fields.aliases)
    out["aliases"].fa.set_flow_style()
    if fields.ansible_user is not None:
        out["ansible_user"] = fields.ansible_user
    if fields.ansible_groups:
        out["ansible_groups"] = CommentedSeq(fields.ansible_groups)
        out["ansible_groups"].fa.set_flow_style()
    if fields.ansible_managed_mode is not None:
        out["ansible_managed_mode"] = fields.ansible_managed_mode
    if fields.manage_network:
        out["manage_network"] = True
    network = CommentedMap()
    if fields.net_interface is not None:
        network["interface"] = fields.net_interface
    if fields.net_prefix is not None:
        network["prefix"] = fields.net_prefix
    if fields.net_gateway is not None:
        network["gateway"] = fields.net_gateway
    if fields.net_nameservers:
        network["nameservers"] = CommentedSeq(fields.net_nameservers)
        network["nameservers"].fa.set_flow_style()
    if network:
        out["network"] = network
    return out


def _hosts_equal(a: Any, b: Any) -> bool:
    """Compare two host entries by their semantic fields, ignoring
    yaml-style metadata that ruamel attaches to its CommentedMap."""
    keys = {
        "name",
        "ip",
        "bootstrap_ip",
        "zones",
        "aliases",
        "ansible_user",
        "ansible_groups",
        "ansible_managed_mode",
        "manage_network",
        "network",
    }
    return {k: _normalize_field(a.get(k)) for k in keys} == {
        k: _normalize_field(b.get(k)) for k in keys
    }


def _normalize_field(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return tuple(value)
    return value
