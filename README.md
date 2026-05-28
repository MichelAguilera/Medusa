# Medusa

Homelab infrastructure source-of-truth and config generator. Model your
homelab once in YAML, then generate runnable configs for CoreDNS,
Traefik, Homepage, Docker Compose, monitoring, and Ansible deploys.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/MichelAguilera/Medusa/main/install.sh | bash
```

See [`docs/bootstrap.md`](docs/bootstrap.md) for the full controller
bootstrap reference and [`docs/site-management.md`](docs/site-management.md)
for adding and deploying targets.

## Layout

This repo holds the **runnable code**: the `medusa` Python package, the
`medusactl` workstation CLI, Ansible playbooks/roles, Jinja2 templates,
and bootstrap scripts. Operator inventory (DNS, services, storage,
secrets) lives in a separate repo that you supply at bootstrap time — see
[`docs/bootstrap.md`](docs/bootstrap.md) for the two-repo layout.

## Pipeline

```
inventory YAML → validated model → normalized model → renderers → generated artifacts → Ansible deploy
```

- Human-authored: `inventory/` (in your inventory repo), `templates/`,
  `ansible/`, `docs/`.
- Generated: never committed to either repo; written to an XDG state dir
  on the controller (`~/.local/state/medusa/generated` by default).

## Status

This project is at an early stage and things may break. I do not recommend using it until a stable branch is released.
