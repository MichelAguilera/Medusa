#!/usr/bin/env bash
# Medusa Arch root-stage prep.
#
# Arch Linux counterpart to prep-debian.sh. Runs as root on an Arch host
# (incl. Omarchy and other Arch derivatives) to make it ansible-reachable
# for medusa. Same env contract, phases, and idempotency guarantees as the
# Debian variant; only the package manager and distro-specific group/service
# names differ.
#
# Two phases:
#
#   1. General prep (always runs, non-breaking on pre-existing infra).
#      Installs base packages, creates the ansible user with NOPASSWD
#      sudo, authorizes an SSH public key. Each step is idempotent and
#      safe to re-run: pacman --needed no-ops when packages are present,
#      useradd is skipped when the user exists, sudoers and authorized_keys
#      writes overwrite/append with the same content.
#
#   2. Hardening (opt-in, BREAKS pre-existing infra). Drops in
#      'PermitRootLogin prohibit-password' and locks the root password.
#      Correct for greenfield templates (full managed mode), breaks
#      hand-tuned hosts. Gate via MEDUSA_PREP_HARDEN.
#
# Idempotent: re-running on an already-prepped host is safe.
#
# Inputs (env vars, or interactive prompts when missing):
#   MEDUSA_PREP_USER     Username to create or reuse. Default: ansible
#   MEDUSA_PREP_PUBKEY   SSH public key content to authorize for that user.
#                        Required when not interactive.
#   MEDUSA_PREP_PUBKEY_B64  Same key, base64-encoded (dodges nested
#                        shell-quoting when medusactl chains ssh hops).
#   MEDUSA_PREP_HARDEN   '1' to run the hardening phase, '0' to skip it.
#                        Default: 1. medusactl passes 0 for limited-mode hosts.

set -euo pipefail

SCRIPT_VERSION="0.1.0"

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

prompt() {
    local var=$1 question=$2 default=${3:-}
    local prompt_text response
    if [[ -n "$default" ]]; then
        prompt_text="$question [$default]: "
    else
        prompt_text="$question: "
    fi
    read -r -p "$prompt_text" response
    if [[ -z "$response" && -n "$default" ]]; then
        response="$default"
    fi
    printf -v "$var" "%s" "$response"
}

echo "Medusa Arch prep v$SCRIPT_VERSION"

# === Preflight ==============================================================

if [[ $EUID -ne 0 ]]; then
    phase_fail "preflight" "must run as root (got uid $EUID)"
fi

# Accept Arch and Arch-family derivatives (Omarchy, EndeavourOS, ...).
is_arch=0
if [[ -f /etc/arch-release ]]; then
    is_arch=1
elif [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
        *" arch "*) is_arch=1 ;;
    esac
fi
if [[ "$is_arch" -ne 1 ]]; then
    phase_fail "preflight" "not an Arch system (no /etc/arch-release, ID!=arch)"
fi

if ! command -v pacman >/dev/null 2>&1; then
    phase_fail "preflight" "pacman not found"
fi

USERNAME="${MEDUSA_PREP_USER:-}"
PUBKEY="${MEDUSA_PREP_PUBKEY:-}"
PUBKEY_B64="${MEDUSA_PREP_PUBKEY_B64:-}"
if [[ -z "$PUBKEY" && -n "$PUBKEY_B64" ]]; then
    PUBKEY=$(printf '%s' "$PUBKEY_B64" | base64 -d 2>/dev/null) \
        || phase_fail "preflight" "MEDUSA_PREP_PUBKEY_B64 failed to decode"
fi
HARDEN="${MEDUSA_PREP_HARDEN:-1}"
case "$HARDEN" in
    0|1) ;;
    *) phase_fail "preflight" "MEDUSA_PREP_HARDEN must be '0' or '1' (got '$HARDEN')" ;;
esac

if [[ -z "$USERNAME" ]]; then
    if [[ -t 0 ]]; then
        prompt USERNAME "Username to create" "ansible"
    else
        USERNAME="ansible"
    fi
fi

if [[ ! "$USERNAME" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    phase_fail "preflight" "invalid username: $USERNAME"
fi

if [[ -z "$PUBKEY" ]]; then
    if [[ -t 0 ]]; then
        prompt PUBKEY "Authorized SSH public key (paste full line)" ""
    fi
fi

if [[ -z "$PUBKEY" ]]; then
    phase_fail "preflight" "MEDUSA_PREP_PUBKEY (or paste) is required"
fi

if ! [[ "$PUBKEY" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp[0-9]+|ssh-dss)[[:space:]] ]]; then
    phase_fail "preflight" "MEDUSA_PREP_PUBKEY does not look like an SSH public key"
fi

phase_ok "preflight"

# === General prep (non-breaking on pre-existing infra) =====================
# Idempotent and safe on a host the operator already manages: pacman --needed
# no-ops when packages exist, useradd skips when the user exists, sudoers +
# authorized_keys writes use guarded overwrites/appends. No sshd_config edits,
# no root account changes below this banner.

info "Installing base packages (sudo openssh python ca-certificates curl)..."
# -Sy refreshes the package db so a stale mirror doesn't fail the install.
# The base set rarely carries soname-breaking pending upgrades, and --needed
# makes this a no-op on the typical (already-current) Arch/Omarchy host, so
# the usual partial-upgrade caveat is acceptable for bootstrap.
pacman -Sy --needed --noconfirm sudo openssh python ca-certificates curl \
    || phase_fail "install" "pacman install failed"
phase_ok "install"

info "Ensuring user '$USERNAME' exists..."
if id "$USERNAME" >/dev/null 2>&1; then
    info "  user already exists; leaving as-is"
else
    # -p '*' creates a disabled-but-NOT-locked password (shadow field '*'),
    # matching Debian's `adduser --disabled-password`. A bare useradd leaves
    # the field '!' (locked), which sshd rejects even for pubkey auth when
    # UsePAM is off (e.g. Omarchy's sshd: "account is locked"). '*' allows
    # key auth while still permitting no password login.
    useradd -m -s /bin/bash -p '*' "$USERNAME" \
        || phase_fail "user" "useradd failed for $USERNAME"
fi

# Repair a locked password field on a pre-existing/previously-prepped account
# ('!' or '!!' with no hash) so key auth works under UsePAM=no. Only touches
# the locked-with-no-hash case; never clobbers a real password the operator set.
current_pw=$(getent shadow "$USERNAME" 2>/dev/null | cut -d: -f2 || true)
if [[ "$current_pw" == "!" || "$current_pw" == "!!" || -z "$current_pw" ]]; then
    usermod -p '*' "$USERNAME" \
        || phase_fail "user" "could not clear locked password field for $USERNAME"
fi

# On Arch the sudo group is 'wheel'. Parity with the Debian variant's
# 'sudo' group; NOPASSWD is granted explicitly via sudoers.d below either way.
info "Adding '$USERNAME' to wheel group..."
usermod -aG wheel "$USERNAME" \
    || phase_fail "user" "usermod -aG wheel failed"

info "Granting NOPASSWD sudo via /etc/sudoers.d/$USERNAME..."
mkdir -p /etc/sudoers.d
sudoers_file="/etc/sudoers.d/$USERNAME"
printf "%s ALL=(ALL) NOPASSWD: ALL\n" "$USERNAME" > "$sudoers_file"
chmod 440 "$sudoers_file"
visudo -cf "$sudoers_file" >/dev/null \
    || phase_fail "user" "sudoers fragment failed validation"
phase_ok "user"

info "Authorizing SSH public key for '$USERNAME'..."
home_dir=$(getent passwd "$USERNAME" | cut -d: -f6)
[[ -n "$home_dir" && -d "$home_dir" ]] \
    || phase_fail "ssh" "home directory missing for $USERNAME"
ssh_dir="$home_dir/.ssh"
auth_file="$ssh_dir/authorized_keys"
install -d -m 700 -o "$USERNAME" -g "$USERNAME" "$ssh_dir"
touch "$auth_file"
chmod 600 "$auth_file"
chown "$USERNAME":"$USERNAME" "$auth_file"
if grep -qxF "$PUBKEY" "$auth_file"; then
    info "  key already present; leaving as-is"
else
    printf "%s\n" "$PUBKEY" >> "$auth_file"
fi

info "Enabling sshd..."
# Arch's openssh ships the service as 'sshd' (no 'ssh' alias).
systemctl enable --now sshd >/dev/null 2>&1 \
    || phase_fail "ssh" "could not enable sshd service"
phase_ok "ssh"

# === Hardening (opt-in, BREAKS pre-existing infra) =========================
# Skipped when MEDUSA_PREP_HARDEN=0 (limited mode). Only run against
# greenfield templates where root is not expected post-bootstrap.

run_hardening() {
    info "Hardening sshd (PermitRootLogin prohibit-password) and locking root password..."
    # Drop-in under sshd_config.d to avoid editing the main config. Arch's
    # packaged /etc/ssh/sshd_config carries 'Include /etc/ssh/sshd_config.d/*.conf'
    # since openssh 8.7; on much older configs without it the drop-in is inert
    # (no-op, not breaking) — sshd -t below still passes.
    rm -f /etc/ssh/sshd_config.d/00-medusa-bootstrap.conf
    local sshd_drop="/etc/ssh/sshd_config.d/00-medusa-hardening.conf"
    install -d -m 0755 /etc/ssh/sshd_config.d
    install -m 0644 /dev/null "$sshd_drop"
    cat > "$sshd_drop" <<'SSHD_EOF'
# Managed by medusa prep-arch.sh. Do not edit by hand.
# - root password login is blocked; root key login still works for emergency
#   recovery if you have an authorized key.
# - non-root password auth is left enabled so the regular user can SSH
#   casually from any device.
PermitRootLogin prohibit-password
SSHD_EOF

    if ! grep -qiE '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*\.conf' /etc/ssh/sshd_config 2>/dev/null; then
        warn "sshd_config has no 'Include sshd_config.d/*.conf'; hardening drop-in may be inert on this host"
    fi

    # Validate before reload so a typo can't lock everyone out.
    if ! sshd -t 2>/dev/null; then
        rm -f "$sshd_drop"
        phase_fail "harden" "sshd config validation failed; reverted hardening drop-in"
    fi

    passwd -l root >/dev/null \
        || phase_fail "harden" "passwd -l root failed"

    systemctl reload sshd >/dev/null 2>&1 \
        || systemctl restart sshd >/dev/null 2>&1 \
        || phase_fail "harden" "could not reload sshd"
    phase_ok "harden"
}

if [[ "$HARDEN" == "1" ]]; then
    run_hardening
else
    info "harden: skipped (MEDUSA_PREP_HARDEN=0; limited mode — pre-existing host)"
fi

echo ""
phase_ok "prep: host ready for user-stage bootstrap as $USERNAME"
