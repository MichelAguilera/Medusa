from ipaddress import IPv4Address, IPv6Address
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ZoneInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    upstreams: list[str] = Field(default_factory=list)
    # CoreDNS forward transport for this zone's upstreams. "udp" is the
    # historical default; "dot" switches to DNS-over-TLS (tcp/853) and
    # requires forwarder_tls_servername for cert validation. Operators
    # flip to dot when an upstream path is hijacked by an ISP or router
    # that rewrites/drops plain UDP/53 queries.
    forwarder_mode: Literal["udp", "dot"] = "udp"
    forwarder_tls_servername: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_zone_name(cls, value: str) -> str:
        normalized = value.strip().lower().removesuffix(".")
        if not normalized:
            raise ValueError("zone name cannot be empty")
        return normalized

    @field_validator("upstreams")
    @classmethod
    def upstreams_cannot_be_empty_strings(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("upstreams cannot contain empty values")
        return normalized

    @field_validator("forwarder_tls_servername")
    @classmethod
    def normalize_tls_servername(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("forwarder_tls_servername cannot be empty when set")
        if " " in normalized:
            raise ValueError(
                "forwarder_tls_servername must be a single hostname, not a list"
            )
        return normalized

    @model_validator(mode="after")
    def validate_forwarder_combo(self) -> Self:
        if self.forwarder_mode == "dot" and self.forwarder_tls_servername is None:
            raise ValueError(
                f"zone {self.name}: forwarder_mode 'dot' requires "
                f"forwarder_tls_servername"
            )
        if self.forwarder_mode == "udp" and self.forwarder_tls_servername is not None:
            raise ValueError(
                f"zone {self.name}: forwarder_tls_servername is only valid "
                f"with forwarder_mode 'dot'"
            )
        return self


class HostInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ip: IPv4Address | IPv6Address
    # Temporary cutover address used in the controller's /etc/hosts
    # bootstrap block when real DNS does not yet resolve this host
    # (e.g. host still on a DHCP lease, CoreDNS not yet authoritative
    # for the zone). When unset, the host is assumed to be reachable
    # via DNS and is NOT seeded into /etc/hosts. Clear with
    # `medusa promote-host` once DNS is live for this host.
    bootstrap_ip: IPv4Address | IPv6Address | None = None
    zones: list[str] = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    # Optional ansible inventory metadata. When ansible_user is set, the host
    # is considered "managed" and is emitted into ansible inventory + the
    # bootstrap /etc/hosts / ~/.ssh/config helper files. DNS-only hosts (no
    # ansible_user) are skipped by the ansible inventory renderer.
    ansible_user: str | None = None
    ansible_groups: list[str] = Field(default_factory=list)
    # When ansible_user is set, distinguishes how much access medusa has to
    # the host. "full" = medusa-built template (root SSH prep + hardening
    # audit allowed). "limited" = pre-existing/baremetal host (no prep, no
    # root-SSH audit). When omitted on a managed host, normalize defaults to
    # "limited" because "full" can run destructive prep flows and must be
    # deliberate. DNS-only hosts (no ansible_user) must leave this unset.
    ansible_managed_mode: Literal["full", "limited"] | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip().lower().removesuffix(".")
        if not normalized:
            raise ValueError("host name cannot be empty")
        if "." in normalized:
            raise ValueError("host name must be relative, not a FQDN")
        return normalized

    @field_validator("zones")
    @classmethod
    def normalize_zones(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower().removesuffix(".") for item in value]
        if any(not item for item in normalized):
            raise ValueError("host zones cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("host zones must be unique")
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower().removesuffix(".") for item in value]
        if any(not item for item in normalized):
            raise ValueError("aliases cannot contain empty values")
        if any("." in item for item in normalized):
            raise ValueError("aliases must be relative, not FQDNs")
        if len(set(normalized)) != len(normalized):
            raise ValueError("aliases must be unique per host")
        return normalized

    @field_validator("ansible_user")
    @classmethod
    def normalize_ansible_user(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("ansible_user cannot be empty when set")
        return normalized

    @field_validator("ansible_groups")
    @classmethod
    def normalize_ansible_groups(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("ansible_groups cannot contain empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("ansible_groups must be unique per host")
        return normalized

    @model_validator(mode="after")
    def validate_managed_mode_requires_user(self) -> Self:
        if self.ansible_user is None and self.ansible_managed_mode is not None:
            raise ValueError(
                f"host {self.name}: ansible_managed_mode requires ansible_user"
            )
        return self

    @model_validator(mode="after")
    def validate_bootstrap_ip_differs_from_ip(self) -> Self:
        if self.bootstrap_ip is not None and self.bootstrap_ip == self.ip:
            raise ValueError(
                f"host {self.name}: bootstrap_ip equals ip; drop bootstrap_ip "
                f"instead of duplicating the address"
            )
        return self


class DnsInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zones: list[ZoneInventory]
    hosts: list[HostInventory] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references_and_uniqueness(self) -> Self:
        zone_names = [zone.name for zone in self.zones]
        if len(zone_names) != len(set(zone_names)):
            raise ValueError("zone names must be unique")

        known_zones = set(zone_names)
        seen_names: set[str] = set()

        for host in self.hosts:
            unknown = sorted(set(host.zones) - known_zones)
            if unknown:
                formatted = ", ".join(unknown)
                raise ValueError(
                    f"host {host.name} references unknown zone(s): {formatted}"
                )

            if host.name in host.aliases:
                raise ValueError(f"host {host.name} repeats its own name as an alias")

            for label in [host.name, *host.aliases]:
                for zone in host.zones:
                    fqdn = f"{label}.{zone}"
                    if fqdn in seen_names:
                        raise ValueError(f"duplicate DNS name: {fqdn}")
                    seen_names.add(fqdn)

        return self


def parse_dns_inventory(data: dict) -> DnsInventory:
    return DnsInventory.model_validate(data)
