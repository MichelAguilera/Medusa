#!/usr/bin/env bash
# User-facing Medusa controller installer.
#
# This script runs from a workstation. It asks where the controller is, copies
# tools/bootstrap-controller.sh there over SSH, then runs the controller-side
# bootstrap interactively on that host.

set -euo pipefail

INSTALL_VERSION="0.3.0"
MEDUSA_INSTALL_REF="${MEDUSA_INSTALL_REF:-main}"
MEDUSA_GITHUB_REPO="${MEDUSA_GITHUB_REPO:-MichelAguilera/Medusa}"
MEDUSA_BOOTSTRAP_URL="${MEDUSA_BOOTSTRAP_URL:-https://raw.githubusercontent.com/MichelAguilera/Medusa/${MEDUSA_INSTALL_REF}/tools/bootstrap-controller.sh}"
MEDUSACTL_CLI_URL="${MEDUSACTL_CLI_URL:-https://raw.githubusercontent.com/MichelAguilera/Medusa/${MEDUSA_INSTALL_REF}/tools/medusactl}"
MEDUSACTL_CLI_PATH="${MEDUSACTL_CLI_PATH:-$HOME/.local/bin/medusactl}"

color_red=$'\033[31m'
color_green=$'\033[32m'
color_yellow=$'\033[33m'
color_reset=$'\033[0m'

info() { printf "  %s\n" "$1" >&2; }
warn() { printf "  %s%s%s\n" "$color_yellow" "$1" "$color_reset" >&2; }
fail() {
    printf "%s✗%s install: %s\n" "$color_red" "$color_reset" "$1" >&2
    exit 1
}

usage() {
    cat <<EOF
Medusa controller installer v${INSTALL_VERSION}

Usage:
  ./install.sh
  curl -fsSL https://raw.githubusercontent.com/MichelAguilera/Medusa/main/install.sh | bash

Environment:
  MEDUSA_INSTALL_REF       Git ref used when downloading the controller
                           bootstrap script. Default: main
  MEDUSA_GITHUB_REPO       GitHub repo used for authenticated gh fallback.
                           Default: MichelAguilera/Medusa
  MEDUSA_BOOTSTRAP_URL     Full URL to tools/bootstrap-controller.sh.
                           Overrides MEDUSA_INSTALL_REF.
  MEDUSACTL_CLI_URL        Full URL to tools/medusactl.
  MEDUSACTL_CLI_PATH       Local install path for optional CLI.
                           Default: ~/.local/bin/medusactl

The installer runs on your workstation. It connects to a Debian 12 controller
over SSH, copies the controller bootstrap script there, then runs that
bootstrap interactively on the controller.

Adding ansible target hosts is a separate step, handled by medusactl after
the controller is installed:
  medusactl add-target <name> --ip <ip> [--authorize]
EOF
}

prompt() {
    local var=$1 question=$2 default=${3:-}
    local prompt_text response
    if [[ -n "$default" ]]; then
        prompt_text="$question [$default]: "
    else
        prompt_text="$question: "
    fi
    read -r -p "$prompt_text" response < /dev/tty
    if [[ -z "$response" && -n "$default" ]]; then
        response="$default"
    fi
    printf -v "$var" "%s" "$response"
}

confirm() {
    local question=$1 default=${2:-y}
    local yn_hint response
    [[ "$default" == "y" ]] && yn_hint="Y/n" || yn_hint="y/N"
    read -r -p "$question [$yn_hint]: " response < /dev/tty
    response=${response:-$default}
    [[ "${response,,}" == "y" || "${response,,}" == "yes" ]]
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 is required on the workstation"
}

default_public_key() {
    if [[ -f "$HOME/.ssh/id_ed25519.pub" ]]; then
        printf "%s/.ssh/id_ed25519.pub" "$HOME"
        return 0
    fi
    if [[ -f "$HOME/.ssh/id_rsa.pub" ]]; then
        printf "%s/.ssh/id_rsa.pub" "$HOME"
        return 0
    fi
    return 1
}

ensure_public_key() {
    local public_key private_key
    if public_key=$(default_public_key); then
        printf "%s" "$public_key"
        return 0
    fi

    public_key="$HOME/.ssh/id_ed25519.pub"
    private_key="${public_key%.pub}"
    if ! confirm "No workstation SSH public key found. Generate ~/.ssh/id_ed25519 now" "y"; then
        fail "workstation SSH public key is required"
    fi
    command -v ssh-keygen >/dev/null 2>&1 || fail "ssh-keygen is required to generate an SSH key"
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$private_key" -N "" -C "${USER:-medusa}@$(hostname)-medusa-install" \
        || fail "failed to generate $private_key"
    printf "%s" "$public_key"
}

authorize_key_on_controller() {
    local target=$1 port=$2 public_key=$3
    [[ -f "$public_key" ]] || fail "SSH public key not found at $public_key"

    info "Authorizing workstation key on $target"
    if command -v ssh-copy-id >/dev/null 2>&1; then
        ssh-copy-id -p "$port" -i "$public_key" "$target" \
            || fail "ssh-copy-id failed for $target"
        return 0
    fi

    warn "ssh-copy-id not found; falling back to direct authorized_keys append"
    cat "$public_key" | ssh -p "$port" -o StrictHostKeyChecking=accept-new \
        "$target" 'key=$(cat); umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -qxF "$key" ~/.ssh/authorized_keys || printf "%s\n" "$key" >> ~/.ssh/authorized_keys' \
        || fail "failed to authorize key on $target"
}

fetch_file() {
    local label=$1 local_path=$2 url=$3 repo_path=$4 dest=$5

    if [[ -n "$local_path" && -f "$local_path" ]]; then
        cp "$local_path" "$dest" || fail "failed to copy $label from $local_path"
        return 0
    fi

    if command -v curl >/dev/null 2>&1; then
        info "Downloading $label from $url"
        if curl -fsSL "$url" -o "$dest"; then
            return 0
        fi
        warn "raw $label download failed; trying authenticated GitHub CLI fallback"
    fi

    if command -v gh >/dev/null 2>&1; then
        info "Downloading $label through gh api from $MEDUSA_GITHUB_REPO@$MEDUSA_INSTALL_REF"
        gh api "repos/${MEDUSA_GITHUB_REPO}/contents/${repo_path}?ref=${MEDUSA_INSTALL_REF}" \
            --jq .content | base64 -d > "$dest" \
            || fail "failed to download $label with gh api"
        return 0
    fi

    fail "failed to download $label; install curl for public repos or gh for private repos"
}

bootstrap_source() {
    # Fetch the controller bootstrap script (controller-only; target ops
    # live in medusactl).
    local script_dir="" local_bootstrap=""
    if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
        script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
        [[ -f "$script_dir/tools/bootstrap-controller.sh" ]] \
            && local_bootstrap="$script_dir/tools/bootstrap-controller.sh"
    fi

    local tmp_bootstrap
    tmp_bootstrap=$(mktemp)
    TEMP_FILES+=("$tmp_bootstrap")

    fetch_file "controller bootstrap" "$local_bootstrap" "$MEDUSA_BOOTSTRAP_URL" \
        "tools/bootstrap-controller.sh" "$tmp_bootstrap"

    printf "%s" "$tmp_bootstrap"
}

cli_source() {
    local script_dir=""
    if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
        script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    fi

    if [[ -n "$script_dir" && -f "$script_dir/tools/medusactl" ]]; then
        printf "%s/tools/medusactl" "$script_dir"
        return 0
    fi

    local tmp_cli
    tmp_cli=$(mktemp)
    TEMP_FILES+=("$tmp_cli")

    if command -v curl >/dev/null 2>&1; then
        info "Downloading workstation CLI from $MEDUSACTL_CLI_URL"
        if curl -fsSL "$MEDUSACTL_CLI_URL" -o "$tmp_cli"; then
            printf "%s" "$tmp_cli"
            return 0
        fi
        warn "raw CLI download failed; trying authenticated GitHub CLI fallback"
    fi

    if command -v gh >/dev/null 2>&1; then
        info "Downloading workstation CLI through gh api from $MEDUSA_GITHUB_REPO@$MEDUSA_INSTALL_REF"
        gh api "repos/${MEDUSA_GITHUB_REPO}/contents/tools/medusactl?ref=${MEDUSA_INSTALL_REF}" \
            --jq .content | base64 -d > "$tmp_cli" \
            || fail "failed to download workstation CLI with gh api"
        printf "%s" "$tmp_cli"
        return 0
    fi

    fail "failed to download workstation CLI; install curl for public repos or gh for private repos"
}

install_controller_cli() {
    local source=$1 destination=$2 destination_dir
    destination_dir=$(dirname "$destination")
    mkdir -p "$destination_dir" || fail "failed to create $destination_dir"
    cp "$source" "$destination" || fail "failed to install medusactl to $destination"
    chmod 0755 "$destination" || fail "failed to mark $destination executable"
    info "Installed medusactl at $destination"
    case ":$PATH:" in
        *":$destination_dir:"*) ;;
        *) warn "$destination_dir is not on PATH; run $destination directly or add it to PATH" ;;
    esac
}

install_medusa_runtime() {
    # T-037: workstation gains a local medusa runtime so mutation commands
    # (add-target, remove-target, promote-target) can run locally against
    # the operator's inventory clone instead of SSHing the controller.
    # `uv tool install` is the documented path; the binary lands at
    # ~/.local/share/uv/tools/medusa and a shim at ~/.local/bin/medusa.
    local source_url="git+https://github.com/${MEDUSA_GITHUB_REPO}.git@${MEDUSA_INSTALL_REF}"
    if ! command -v uv >/dev/null 2>&1; then
        warn "uv is not installed on this workstation; skipping medusa runtime install"
        warn "  install uv first (https://docs.astral.sh/uv/) then re-run 'medusactl install-runtime'"
        return 0
    fi
    info "Installing medusa runtime via 'uv tool install $source_url'"
    if uv tool install --force "$source_url"; then
        info "medusa runtime installed; check: medusa --help"
    else
        warn "uv tool install failed; you can retry later with 'medusactl install-runtime'"
    fi
}

cleanup() {
    local file
    for file in "${TEMP_FILES[@]:-}"; do
        rm -f "$file"
    done
    if [[ -n "${SSH_TARGET:-}" && -n "${CONTROL_PATH:-}" && -S "$CONTROL_PATH" ]]; then
        ssh -O exit -o ControlPath="$CONTROL_PATH" "$SSH_TARGET" >/dev/null 2>&1 || true
    fi
    if [[ -n "${CONTROL_DIR:-}" ]]; then
        rm -rf "$CONTROL_DIR"
    fi
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
esac

TEMP_FILES=()
trap cleanup EXIT

require_command ssh
require_command scp

echo "Medusa controller installer v$INSTALL_VERSION"
echo ""
info "This runs from your workstation and bootstraps a remote Debian 12 controller over SSH."
echo ""

if confirm "Install optional medusactl CLI locally" "n"; then
    CLI_SOURCE=$(cli_source)
    install_controller_cli "$CLI_SOURCE" "$MEDUSACTL_CLI_PATH"
    if confirm "Install medusa runtime locally (needed for local inventory mutations)" "y"; then
        install_medusa_runtime
    fi
    exec "$MEDUSACTL_CLI_PATH" install-controller
fi

CONTROLLER_HOST=""
CONTROLLER_USER=""
CONTROLLER_PORT=""
REMOTE_SCRIPT=""

if [[ -z "$CONTROLLER_HOST" ]]; then
    prompt CONTROLLER_HOST "Controller hostname or IP"
fi
if [[ -z "$CONTROLLER_USER" ]]; then
    prompt CONTROLLER_USER "Controller SSH user" "ansible"
fi
if [[ -z "$CONTROLLER_PORT" ]]; then
    prompt CONTROLLER_PORT "Controller SSH port" "22"
fi
prompt REMOTE_SCRIPT "Remote bootstrap script path" "~/medusa-bootstrap-controller.sh"

if [[ -z "$CONTROLLER_HOST" ]]; then
    fail "controller hostname or IP is required"
fi
if [[ -z "$CONTROLLER_USER" ]]; then
    fail "controller SSH user is required"
fi
if [[ ! "$CONTROLLER_PORT" =~ ^[0-9]+$ ]]; then
    fail "controller SSH port must be a number"
fi

BOOTSTRAP=$(bootstrap_source)
SSH_TARGET="${CONTROLLER_USER}@${CONTROLLER_HOST}"
CONTROL_DIR=$(mktemp -d)
CONTROL_PATH="${CONTROL_DIR}/ssh-control"
SSH_OPTS=(
    -p "$CONTROLLER_PORT"
    -o StrictHostKeyChecking=accept-new
    -o ControlMaster=auto
    -o ControlPersist=10m
    -o ControlPath="$CONTROL_PATH"
)
SCP_OPTS=(
    -P "$CONTROLLER_PORT"
    -o StrictHostKeyChecking=accept-new
    -o ControlMaster=auto
    -o ControlPersist=10m
    -o ControlPath="$CONTROL_PATH"
)

echo ""
info "About to:"
info "  Connect to: $SSH_TARGET"
info "  SSH port:   $CONTROLLER_PORT"
info "  Copy:       $BOOTSTRAP"
info "  Remote:     $REMOTE_SCRIPT"
info "  Run:        bash $REMOTE_SCRIPT"
echo ""
if ! confirm "Proceed" "y"; then
    fail "aborted by user"
fi

echo ""
if confirm "Authorize this workstation's SSH key on the controller now" "y"; then
    authorize_key_on_controller "$SSH_TARGET" "$CONTROLLER_PORT" "$(ensure_public_key)"
fi

echo ""
info "Checking SSH connectivity..."
ssh -n "${SSH_OPTS[@]}" "$SSH_TARGET" 'printf "controller-ssh-ok\n"' >/dev/null \
    || fail "unable to connect to $SSH_TARGET over SSH"

info "Checking controller prerequisites..."
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'bash -s' <<'REMOTE_PREFLIGHT' \
    || fail "controller prerequisite check failed"
set -euo pipefail
command -v sh >/dev/null 2>&1 || { echo "missing sh" >&2; exit 10; }
command -v bash >/dev/null 2>&1 || { echo "missing bash" >&2; exit 11; }
command -v sudo >/dev/null 2>&1 || { echo "missing sudo" >&2; exit 12; }
timeout 5 bash -c "</dev/tcp/1.1.1.1/80" >/dev/null 2>&1 || {
    echo "no outbound IPv4 connectivity" >&2
    exit 13
}
getent hosts deb.debian.org >/dev/null 2>&1 || {
    echo "cannot resolve deb.debian.org" >&2
    exit 14
}
sudo -n true >/dev/null 2>&1 || {
    echo "sudo is present; controller bootstrap may prompt for the sudo password" >&2
}
REMOTE_PREFLIGHT

info "Copying controller bootstrap..."
scp "${SCP_OPTS[@]}" "$BOOTSTRAP" "$SSH_TARGET:$REMOTE_SCRIPT" \
    || fail "failed to copy bootstrap script to controller"

info "Running controller bootstrap on $SSH_TARGET..."
echo ""
ssh -t "${SSH_OPTS[@]}" "$SSH_TARGET" "bash $REMOTE_SCRIPT" < /dev/tty
ssh_rc=$?
# Restore terminal: ssh -t leaves OSC query responses (color, title) in the
# input buffer that get echoed into the next prompt. stty sane + SGR reset
# clears the worst of it without nuking scrollback.
stty sane 2>/dev/null || true
printf '\033[0m' >/dev/tty 2>/dev/null || true
[[ "$ssh_rc" -ne 0 ]] && fail "controller bootstrap failed"

echo ""
printf "%s✓%s install: controller bootstrap completed\n" "$color_green" "$color_reset"
echo ""
info "Next: add ansible targets from your workstation with:"
info "  medusactl add-target <name> --ip <ip> [--authorize]"
info "Then deploy:"
info "  medusactl deploy"
