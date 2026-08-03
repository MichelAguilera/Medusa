#!/usr/bin/env bash
# Medusa Debian host preparer.
#
# Run this ONCE on a fresh Debian 12 or 13 install, as root. Works for:
#   - Proxmox VM templates  (auto-detected as VM)
#   - Proxmox LXC templates (auto-detected as LXC)
#   - Bare-metal hosts      (auto-detected; clone-prep skipped)
#
# What it always does:
#   - Installs core packages Medusa's bootstrap expects:
#     sudo openssh-server python3 ca-certificates curl
#   - Installs ops extras (skip with --no-extra):
#     vim-nox tmux htop less ncdu rsync bash-completion chrony
#     unattended-upgrades
#   - Disables systemd-networkd-wait-online to avoid 2-min boot hangs
#
# What it does on VMs only:
#   - Installs and enables qemu-guest-agent
#
# What it does on VM and LXC (skipped on bare metal):
#   - Resets /etc/machine-id and removes baked SSH host keys so every
#     clone made from the resulting template gets unique identities on
#     first boot. NEVER do this on a long-lived bare-metal install.
#
# Flags:
#   --no-upgrade            skip apt full-upgrade
#   --no-extra              skip ops-friendly extras
#   --as-template           force clone-prep even on bare metal (rare; you
#                           must plan to image and clone this disk)
#   --no-clone-prep         skip clone-prep even on a detected VM/LXC
#                           (useful when reusing a VM as a long-lived host,
#                           not a template source)
#   --root-password=<pw>    bake a root password into the template AND
#                           enable temporary root SSH password auth via
#                           /etc/ssh/sshd_config.d/00-medusa-bootstrap.conf.
#                           Required so 'medusactl prep-target' can do its
#                           first-run root-SSH bootstrap on clones. The
#                           prep-debian harden block replaces this drop-in
#                           and locks the root password on full-mode hosts.
#                           Also readable from env MEDUSA_TEMPLATE_ROOT_PASSWORD.
#   --no-root-bootstrap     skip the root password + sshd drop-in step
#                           (use when the operator provisions root access
#                           some other way: pre-baked SSH key, cloud-init,
#                           etc).
#
# Idempotent. Re-running on an already-prepped host is safe.

set -euo pipefail

# Ensure /usr/sbin + /sbin are reachable. Debian 13 + non-login root
# shells (su without '-', some sudo configs) can strip these, leaving
# chpasswd / usermod / sshd / ssh-keygen findable only by absolute path.
# Prepend so the script works regardless of how the operator invoked it.
export PATH="/usr/sbin:/sbin:/usr/local/sbin:$PATH"

SCRIPT_VERSION="0.2.1"

color_red=$'\033[31m'
color_green=$'\033[32m'
color_yellow=$'\033[33m'
color_reset=$'\033[0m'

phase_ok()   { printf "%s✓%s %s\n" "$color_green" "$color_reset" "$1"; }
phase_fail() {
    printf "%s✗%s %s: %s\n" "$color_red" "$color_reset" "$1" "$2" >&2
    exit 1
}
info() { printf "  %s\n" "$1"; }
warn() { printf "  %s%s%s\n" "$color_yellow" "$1" "$color_reset"; }

# --- Argument parsing -------------------------------------------------------

DO_UPGRADE=1
DO_EXTRA=1
# CLONE_PREP_OVERRIDE: "" = auto (skip on baremetal, run on VM/LXC),
#                     "force" = always run, "skip" = always skip.
CLONE_PREP_OVERRIDE=""
ROOT_PASSWORD="${MEDUSA_TEMPLATE_ROOT_PASSWORD:-}"
DO_ROOT_BOOTSTRAP=1
for arg in "$@"; do
    case "$arg" in
        --no-upgrade)         DO_UPGRADE=0 ;;
        --no-extra)           DO_EXTRA=0 ;;
        --as-template)        CLONE_PREP_OVERRIDE="force" ;;
        --no-clone-prep)      CLONE_PREP_OVERRIDE="skip" ;;
        --root-password=*)    ROOT_PASSWORD="${arg#*=}" ;;
        --no-root-bootstrap)  DO_ROOT_BOOTSTRAP=0 ;;
        -h|--help)
            sed -n '2,/^set -e/p' "$0" | sed '$d' | sed 's/^# //;s/^#//'
            exit 0
            ;;
        *) phase_fail "args" "unknown argument: $arg" ;;
    esac
done

# --- Preflight --------------------------------------------------------------

echo "Medusa template prep v$SCRIPT_VERSION"

if [[ $EUID -ne 0 ]]; then
    phase_fail "preflight" "must run as root (got uid $EUID)"
fi

if [[ ! -f /etc/debian_version ]]; then
    phase_fail "preflight" "not a Debian system (missing /etc/debian_version)"
fi

# Detect environment so we can skip VM-only steps on LXC, clone-uniqueness
# steps on bare metal, etc.
IS_LXC=0
IS_VM=0
IS_BAREMETAL=0
DETECTED=""
if command -v systemd-detect-virt >/dev/null 2>&1; then
    DETECTED="$(systemd-detect-virt 2>/dev/null || true)"
    case "$DETECTED" in
        lxc|lxc-libvirt|systemd-nspawn) IS_LXC=1 ;;
        kvm|qemu|bochs|vmware|virtualbox|hyperv|xen|microsoft|parallels) IS_VM=1 ;;
        none|"") IS_BAREMETAL=1 ;;
        *) IS_VM=1 ;;  # unknown virtualization tech; safer to treat as VM
    esac
fi
# Fallback when systemd-detect-virt is unavailable: container hint comes
# from /proc/1/environ; otherwise assume bare metal rather than VM.
if [[ "$IS_LXC" -eq 0 && "$IS_VM" -eq 0 && "$IS_BAREMETAL" -eq 0 ]]; then
    if [[ -e /proc/1/environ ]] && grep -qaE 'container=lxc' /proc/1/environ; then
        IS_LXC=1
    else
        IS_BAREMETAL=1
    fi
fi

# Decide whether to run the clone-prep phase (wipe machine-id + SSH host
# keys). Default: on for VM/LXC (presumed template prep), off for bare
# metal (would destroy the long-lived host's identity). Overridable.
case "$CLONE_PREP_OVERRIDE" in
    force) DO_CLONE_PREP=1 ;;
    skip)  DO_CLONE_PREP=0 ;;
    *)     DO_CLONE_PREP=$(( IS_BAREMETAL == 0 ? 1 : 0 )) ;;
esac

debian_version="$(cat /etc/debian_version 2>/dev/null || echo unknown)"
info "Debian version: $debian_version"
if [[ "$IS_LXC" -eq 1 ]]; then
    info "Environment:    LXC"
elif [[ "$IS_VM" -eq 1 ]]; then
    info "Environment:    VM"
elif [[ "$IS_BAREMETAL" -eq 1 ]]; then
    info "Environment:    bare metal"
fi
[[ -n "$DETECTED" ]] && info "  systemd-detect-virt: $DETECTED"
if [[ "$DO_CLONE_PREP" -eq 1 ]]; then
    info "Clone-prep:     enabled (machine-id reset + host-key regen on first boot)"
else
    info "Clone-prep:     skipped (keeping host identity intact)"
fi
phase_ok "preflight"

# --- Apt --------------------------------------------------------------------

export DEBIAN_FRONTEND=noninteractive

info "Updating apt cache..."
apt-get update -qq \
    || phase_fail "apt" "apt-get update failed"

if [[ "$DO_UPGRADE" -eq 1 ]]; then
    info "Running apt full-upgrade..."
    apt-get full-upgrade -y -qq \
        || phase_fail "apt" "apt-get full-upgrade failed"
fi
phase_ok "apt"

# --- Core packages ----------------------------------------------------------

CORE_PACKAGES=(sudo openssh-server python3 ca-certificates curl)
info "Installing core packages: ${CORE_PACKAGES[*]}"
apt-get install -y -qq "${CORE_PACKAGES[@]}" \
    || phase_fail "core" "core package install failed"
phase_ok "core"

# --- Bootstrap auth (root password + permissive sshd drop-in) --------------
#
# Goal: a clone of this template is reachable by 'medusactl prep-target'
# WITHOUT operator console intervention. prep-target's first-run path
# uses sshpass + root SSH password to scp prep-debian.sh and run it as
# root. Debian's defaults block that path (PermitRootLogin
# prohibit-password, no root password). This section bakes a root
# password into the template AND installs a sshd drop-in that
# temporarily allows root password auth.
#
# Lifecycle: medusa prep-debian.sh's harden block (full mode) replaces
# /etc/ssh/sshd_config.d/00-medusa-bootstrap.conf with a 'PermitRootLogin
# prohibit-password' drop-in and locks the root password. Limited-mode
# hosts keep the bootstrap drop-in (operator owns the root surface), so
# the password stays usable. Either way, the template-baked credential
# is intentional.

if [[ "$DO_ROOT_BOOTSTRAP" -eq 1 ]]; then
    if [[ -z "$ROOT_PASSWORD" && -t 0 ]]; then
        printf "  Root password to bake into template: " >&2
        read -r -s ROOT_PASSWORD
        printf "\n" >&2
    fi
    if [[ -z "$ROOT_PASSWORD" ]]; then
        phase_fail "bootstrap-auth" \
            "no root password provided. Pass --root-password=<pw>, set MEDUSA_TEMPLATE_ROOT_PASSWORD, or use --no-root-bootstrap to skip"
    fi

    info "Setting root password (baked into template)..."
    echo "root:$ROOT_PASSWORD" | chpasswd \
        || phase_fail "bootstrap-auth" "chpasswd failed"

    info "Installing temporary sshd drop-in (PermitRootLogin yes + password auth)..."
    install -d -m 0755 /etc/ssh/sshd_config.d
    sshd_drop="/etc/ssh/sshd_config.d/00-medusa-bootstrap.conf"
    cat > "$sshd_drop" <<'EOF'
# Managed by medusa prepare-template.sh.
# TEMPORARY: enables 'medusactl prep-target' to bootstrap as root via
# SSH password on first run. The prep-debian.sh harden block (full mode)
# replaces this drop-in with 'PermitRootLogin prohibit-password' and
# runs 'passwd -l root'. Limited-mode hosts keep this drop-in; the
# operator owns the root surface on those.
PermitRootLogin yes
PasswordAuthentication yes
EOF
    chmod 0644 "$sshd_drop"

    if ! sshd -t 2>/dev/null; then
        rm -f "$sshd_drop"
        phase_fail "bootstrap-auth" "sshd config validation failed; reverted drop-in"
    fi
    phase_ok "bootstrap-auth"
else
    info "Skipping root password + sshd drop-in (--no-root-bootstrap)."
fi

# --- VM-only: qemu-guest-agent ---------------------------------------------

if [[ "$IS_VM" -eq 1 ]]; then
    info "Installing qemu-guest-agent (VM detected)..."
    apt-get install -y -qq qemu-guest-agent \
        || phase_fail "qga" "qemu-guest-agent install failed"
    systemctl enable --now qemu-guest-agent >/dev/null 2>&1 \
        || warn "  could not start qemu-guest-agent now (may need a reboot to pick up the virtio device)"
    phase_ok "qga"
else
    info "Skipping qemu-guest-agent (LXC detected)."
fi

# --- Ops-friendly extras ----------------------------------------------------

if [[ "$DO_EXTRA" -eq 1 ]]; then
    EXTRA_PACKAGES=(vim-nox tmux htop less ncdu rsync bash-completion chrony unattended-upgrades)
    info "Installing extras: ${EXTRA_PACKAGES[*]}"
    apt-get install -y -qq "${EXTRA_PACKAGES[@]}" \
        || phase_fail "extras" "extras install failed"
    if dpkg -l unattended-upgrades >/dev/null 2>&1; then
        dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true
    fi
    phase_ok "extras"
else
    info "Skipping extras (--no-extra)."
fi

# --- Boot speed -------------------------------------------------------------

info "Disabling systemd-networkd-wait-online (avoids 2-min boot hang)..."
for unit in systemd-networkd-wait-online.service NetworkManager-wait-online.service; do
    if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
        systemctl disable "$unit" >/dev/null 2>&1 || true
        systemctl mask "$unit" >/dev/null 2>&1 || true
    fi
done
phase_ok "boot-speed"

# --- Clone uniqueness (template-only) ---------------------------------------

if [[ "$DO_CLONE_PREP" -eq 1 ]]; then
    info "Resetting machine-id (regenerated on first boot of each clone)..."
    truncate -s 0 /etc/machine-id || phase_fail "uniqueness" "could not truncate /etc/machine-id"
    if [[ -f /var/lib/dbus/machine-id ]] && [[ ! -L /var/lib/dbus/machine-id ]]; then
        rm -f /var/lib/dbus/machine-id
    fi
    [[ -e /var/lib/dbus/machine-id ]] || ln -s /etc/machine-id /var/lib/dbus/machine-id

    info "Removing baked SSH host keys (regenerated on first boot of each clone)..."
    rm -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub

    # Ensure a regeneration unit exists. Debian's openssh-server ships
    # regenerate-ssh-host-keys.service on some versions but not others;
    # install our own oneshot if missing so behavior is consistent
    # across 12/13.
    if ! systemctl list-unit-files regenerate-ssh-host-keys.service >/dev/null 2>&1; then
        info "Installing first-boot SSH host key regeneration oneshot..."
        cat > /etc/systemd/system/medusa-regen-ssh-host-keys.service <<'EOF'
[Unit]
Description=Regenerate SSH host keys on first boot (Medusa template)
ConditionPathExistsGlob=!/etc/ssh/ssh_host_*_key
Before=ssh.service sshd.service
Wants=ssh.service

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable medusa-regen-ssh-host-keys.service >/dev/null 2>&1 \
            || phase_fail "uniqueness" "could not enable medusa-regen-ssh-host-keys.service"
    else
        systemctl enable regenerate-ssh-host-keys.service >/dev/null 2>&1 \
            || warn "  could not enable regenerate-ssh-host-keys.service"
    fi
    phase_ok "uniqueness"
else
    info "Skipping clone-prep (would wipe host identity); host is ready as-is."
fi

# --- Final notes ------------------------------------------------------------

echo ""
phase_ok "host prep complete"
echo ""
if [[ "$DO_CLONE_PREP" -eq 1 ]]; then
    echo "Next steps in Proxmox:"
    echo "  1. Shut down this guest cleanly."
    if [[ "$IS_VM" -eq 1 ]]; then
        echo "  2. In Hardware -> QEMU Guest Agent, ensure Enabled."
    fi
    echo "  3. Convert to template."
    echo "  4. Clone away. Each clone will:"
    echo "     - generate a fresh machine-id"
    echo "     - generate unique SSH host keys on first boot"
    if [[ "$IS_VM" -eq 1 ]]; then
        echo "     - run qemu-guest-agent (Proxmox UI shows guest IP)"
    fi
    echo "     - already have python3 + sudo + openssh-server for Medusa bootstrap"
    if [[ "$DO_ROOT_BOOTSTRAP" -eq 1 ]]; then
        echo "     - accept root SSH password auth (baked credential) so"
        echo "       'medusactl prep-target' can run unattended on first boot"
    else
        echo "     - NOT accept root SSH password auth (--no-root-bootstrap);"
        echo "       provision root access out-of-band before prep-target"
    fi
else
    echo "Host is ready for Medusa's bootstrap. Identity is preserved (no"
    echo "machine-id or SSH host key wipe), so this host can keep its current"
    echo "SSH connections and DHCP lease."
    echo ""
    echo "Add this host to your target list when you run ./install.sh:"
    echo "  <name> <hostname> <ansible-user> <ip>"
fi
echo ""
echo "Run Medusa's installer next:"
echo "  ./install.sh"
