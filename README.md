# Medusa

Medusa is a homelab infrastructure source-of-truth and config generator.
You describe your hosts, DNS, services, storage, and secrets once in
YAML, and Medusa generates the configuration that runs them: CoreDNS,
Traefik, Homepage, Docker Compose stacks, monitoring, and NixOS host
definitions.

## How it works

```
inventory YAML → validated model → normalized model → renderers → generated artifacts → nixos-rebuild
```

- **Inventory** is the only thing you author by hand. It lives in your
  own repo, separate from this one; the demo `inventory/` here shows the
  expected shape.
- **Validation and normalization** turn the YAML into typed models and
  reject anything inconsistent before it reaches a host.
- **Renderers** turn those models into per-host artifacts from Jinja2
  templates. Generated files are never edited by hand.
- **Deployment** reconciles each NixOS host against its rendered
  configuration with `nixos-rebuild`. Containers stay on Docker Compose,
  with each host receiving only its own stacks.

This repo holds the runnable code: the `medusa` Python package, the
`medusactl` workstation CLI, and the templates.

## Status

Medusa is under fast-paced development. Documentation may be outdated,
and things may break between releases.
