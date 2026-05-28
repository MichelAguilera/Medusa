from typing import Literal

from pydantic import BaseModel, ConfigDict

from medusa.model.dns import DnsModel


class CorednsModel(BaseModel):
    """Renderer-ready CoreDNS context.

    ``forwarder_mode`` and ``forwarder_tls_servername`` are derived from
    the per-zone fields in normalize: every zone with non-empty upstreams
    must agree on the transport, otherwise a CoreDNS instance would need
    multiple forward stanzas (out of scope; documented as such in
    docs/dns-forwarder-mode-plan.md).
    """

    model_config = ConfigDict(frozen=True)

    dns: DnsModel
    upstreams: tuple[str, ...]
    rewrite_zones: tuple[str, ...]
    forwarder_mode: Literal["udp", "dot"] = "udp"
    forwarder_tls_servername: str | None = None
