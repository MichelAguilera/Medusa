#!/usr/bin/env bash
# Medusa Debian root-stage prep.
#
# Runs as root on a fresh Debian 12 host (VM or LXC) to make it usable by
# the user-stage controller bootstrap. Creates a sudo user, installs the
# minimum prereqs, and authorizes an SSH public key for that user.
#
# Idempotent: re-running on an already-prepped host is safe and skips
# anything already in place.
#
# Inputs (env vars, or interactive prompts when missing):
#   MEDUSA_PREP_USER     Username to create or reuse. Default: ansible
#   MEDUSA_PREP_PUBKEY   SSH public key content to authorize for that user.
#                        Required when not interactive.
#
# Typical invocations:
#   sudo MEDUSA_PREP_USER=ansible MEDUSA_PREP_PUBKEY="ssh-ed25519 AAAA..." \
#       bash prep-debian.sh
#   # or from Proxmox host:
#   pct push <vmid> prep-debian.sh /root/prep-debian.sh
#   pct exec <vmid> -- env MEDUSA_PREP_PUBKEY="ssh-ed25519 AAAA..." \
#       bash /root/prep-debian.sh

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

echo "Medusa Debian prep v$SCRIPT_VERSION"

if [[ $EUID -ne 0 ]]; then
    phase_fail "preflight" "must run as root (got uid $EUID)"
fi

if [[ ! -f /etc/debian_version ]]; then
    phase_fail "preflight" "not a Debian system (missing /etc/debian_version)"
fi

USERNAME="${MEDUSA_PREP_USER:-}"
PUBKEY="${MEDUSA_PREP_PUBKEY:-}"

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

info "Installing base packages (sudo openssh-server python3 ca-certificates curl)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq \
    || phase_fail "install" "apt-get update failed"
apt-get install -y -qq sudo openssh-server python3 ca-certificates curl \
    || phase_fail "install" "apt-get install failed"
phase_ok "install"

info "Ensuring user '$USERNAME' exists..."
if id "$USERNAME" >/dev/null 2>&1; then
    info "  user already exists; leaving as-is"
else
    adduser --disabled-password --gecos '' "$USERNAME" >/dev/null \
        || phase_fail "user" "adduser failed for $USERNAME"
fi

info "Adding '$USERNAME' to sudo group..."
usermod -aG sudo "$USERNAME" \
    || phase_fail "user" "usermod -aG sudo failed"

info "Granting NOPASSWD sudo via /etc/sudoers.d/$USERNAME..."
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
systemctl enable --now ssh >/dev/null 2>&1 \
    || systemctl enable --now sshd >/dev/null 2>&1 \
    || phase_fail "ssh" "could not enable ssh service"
phase_ok "ssh"

info "Hardening sshd (PermitRootLogin prohibit-password) and locking root password..."
# Use a drop-in file under sshd_config.d to avoid editing the main config.
# Modern Debian (12+) ships with 'Include /etc/ssh/sshd_config.d/*.conf' in
# the default sshd_config so this is picked up automatically.
sshd_drop="/etc/ssh/sshd_config.d/00-medusa-hardening.conf"
install -m 0644 /dev/null "$sshd_drop"
cat > "$sshd_drop" <<'SSHD_EOF'
# Managed by medusa prep-debian.sh. Do not edit by hand.
# - root password login is blocked; root key login still works for emergency
#   recovery if you have an authorized key.
# - non-root password auth is left enabled so the regular user can SSH
#   casually from any device.
PermitRootLogin prohibit-password
SSHD_EOF

# Verify sshd accepts the new config before restarting, so a typo here can't
# lock everyone out.
if ! sshd -t 2>/dev/null; then
    rm -f "$sshd_drop"
    phase_fail "harden" "sshd config validation failed; reverted hardening drop-in"
fi

# Lock the root account password so password SSH/login cannot succeed via
# root regardless of sshd settings. Key-based root login (if a key was
# authorized) keeps working.
passwd -l root >/dev/null \
    || phase_fail "harden" "passwd -l root failed"

# Reload (not restart) to minimize the chance of dropping in-flight sessions.
systemctl reload ssh >/dev/null 2>&1 \
    || systemctl reload sshd >/dev/null 2>&1 \
    || systemctl restart ssh >/dev/null 2>&1 \
    || systemctl restart sshd >/dev/null 2>&1 \
    || phase_fail "harden" "could not reload sshd"
phase_ok "harden"

echo ""
phase_ok "prep: host ready for user-stage bootstrap as $USERNAME"
