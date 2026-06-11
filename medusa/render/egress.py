from pathlib import Path

from medusa.model.services import ServicesModel
from medusa.render.templates import render_template


def render_egress(
    model: ServicesModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    """Render the egress manifest (always) plus, when a gateway is configured,
    its split-DNS resolver config. The manifest drives the WireGuard gateway
    role and the host-routing role; the resolver config is deployed onto the
    gateway host. See T-066."""
    files: dict[Path, str] = {
        generated_dir / "egress-manifest.yaml": render_template(
            templates_dir,
            "compose/egress-manifest.yaml.j2",
            {
                "egress": model.egress,
                "tunnel_services_by_host": model.tunnel_services_by_host,
            },
        )
    }
    if model.egress is not None:
        ctx = {"egress": model.egress}
        gateway_dir = generated_dir / "egress" / model.egress.gateway
        # Gateway-host artifacts.
        files[gateway_dir / "resolver.conf"] = render_template(
            templates_dir, "egress/resolver.conf.j2", ctx
        )
        files[gateway_dir / "nftables.conf"] = render_template(
            templates_dir, "egress/nftables.conf.j2", ctx
        )
        # Docker-host routing artifacts (host-agnostic: keyed on subnets, not a
        # specific host). The tunnel_routing role deploys these to every host
        # running a tunneled service.
        egress_dir = generated_dir / "egress"
        files[egress_dir / "tunnel-routing.nft"] = render_template(
            templates_dir, "egress/tunnel-routing.nft.j2", ctx
        )
        files[egress_dir / "tunnel-routes.sh"] = render_template(
            templates_dir, "egress/tunnel-routes.sh.j2", ctx
        )
    return files
