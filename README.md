# Medusa

Homelab infrastructure source-of-truth and config generator. Model your
homelab once in YAML, then generate runnable configs for CoreDNS,
Traefik, Homepage, Docker Compose, monitoring, and NixOS hosts — and
deploy them with `nixos-rebuild`.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/MichelAguilera/Medusa/main/install.sh | bash
```

The installer bootstraps a controller (a small Debian LXC or VM that
holds the rendered artifacts and runs the deploys) and can optionally
install the `medusactl` workstation CLI. Run `medusactl --help` for the
full command surface: inventory git porcelain (`status`, `diff`,
`commit`, `push`), render/deploy (`check`, `render`, `deploy`,
`nixos-apply`, `compose`), and target onboarding (`add-target`,
`install-nixos-target`, `seed-target`).

## Deploying

Targets are NixOS hosts. `medusactl deploy` renders the inventory,
refreshes the controller's `/etc/hosts` + SSH aliases
(`controller-apply.yml`, the one Ansible playbook), then reconciles
each host with `nixos-rebuild switch --flake` — with a hostname
preflight that refuses to activate a configuration on the wrong
machine. A first stand-up is `medusactl install-nixos-target`
(nixos-anywhere + disko; the operator authors the disk layout).
Compose remains the container layer: stacks are rendered per host,
delivered by the flake, and driven at runtime with
`medusactl compose <up|down|restart|pull|logs|ps|exec>` directly over
SSH.

## Layout

This repo holds the **runnable code**: the `medusa` Python package, the
`medusactl` workstation CLI, the controller bootstrap
(`tools/bootstrap-controller.sh` + `ansible/`), and Jinja2 templates.
Operator inventory (DNS, services, storage, secrets) lives in a
separate private repo that you supply at bootstrap time; the demo
`inventory/` here shows the expected shape.

## Pipeline

```
inventory YAML → validated model → normalized model → renderers → generated artifacts → nixos-rebuild
```

- Human-authored: `inventory/` (in your inventory repo), `templates/`.
- Generated: never committed to either repo; written to an XDG state dir
  on the controller (`~/.local/state/medusa/generated` by default).

## Status

This project is at an early stage and things may break. I do not recommend using it until a stable branch is released.
