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

## Prepare a Debian template

Run `prepare-template.sh` once on a fresh Debian 12 / 13 install (VM,
LXC, or bare metal) to bring it into a state where `medusactl
prep-target` can finish the bootstrap unattended on every clone.

Installs `sudo`, `openssh-server`, `python3`, `qemu-guest-agent` (VMs
only), wipes baked SSH host keys so each clone gets unique identities
on first boot, bakes a root password, and drops in a temporary
permissive sshd config so first-run root SSH password auth works. Once
`medusactl prep-target` runs against a full-mode host, the harden
block replaces the temporary drop-in and locks the root password.

Run on the target host as root, with the password piped via env so it
never lands in shell history:

```bash
read -r -s -p "Root password: " pw
curl -fsSL https://raw.githubusercontent.com/MichelAguilera/Medusa/main/tools/prepare-template.sh \
  | sudo env MEDUSA_TEMPLATE_ROOT_PASSWORD="$pw" bash
unset pw
```

Or download first to review the script before running:

```bash
curl -fsSL https://raw.githubusercontent.com/MichelAguilera/Medusa/main/tools/prepare-template.sh -o prepare-template.sh
sudo bash prepare-template.sh --root-password='<pw>'
```

After it finishes: shut the guest down, convert to a Proxmox template
in the UI, and clone away. Each clone boots ready for `medusactl
prep-target <name>`.

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
