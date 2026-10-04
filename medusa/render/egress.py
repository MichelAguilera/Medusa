from pathlib import Path

from medusa.model.services import ServicesModel
from medusa.render.templates import render_template


def render_egress(
    model: ServicesModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    """Render the egress gateway's firewall and split-DNS resolver config and
    the tunnel-routing artifacts for hosts running tunnelled services; nothing
    when no gateway is configured. See T-066."""
    files: dict[Path, str] = {}
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
        # specific host), staged to every host running a tunneled service.
        egress_dir = generated_dir / "egress"
        files[egress_dir / "tunnel-routing.nft"] = render_template(
            templates_dir, "egress/tunnel-routing.nft.j2", ctx
        )
        files[egress_dir / "tunnel-routes.sh"] = render_template(
            templates_dir, "egress/tunnel-routes.sh.j2", ctx
        )
    return files
