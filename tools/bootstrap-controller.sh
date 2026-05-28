#!/usr/bin/env bash
# Medusa controller bootstrap. See docs/bootstrap.md for full reference.
#
# Scope (post Stage 3 of scope-split): controller-only. This script
# installs the controller, sets up keys, and runs 'medusa render' once.
# Target onboarding (add-target, prep-target, authorize-target,
# /etc/hosts seed, ~/.ssh/config aliases) lives in 'medusactl' and is
# never invoked from here.
#
# Phases:
#   1. preflight   collect inputs, validate, confirm
#   2. install     apt deps, uv, repo clone, python deps, ansible collections
#   3. identity    age key, ssh key
#   4. inventory   .sops.yaml recipient management
#   5. smoke       medusa validate / render / check
#
# Idempotent: existing keys, clones, and configs are detected and reused
# rather than overwritten.

set -euo pipefail

SCRIPT_VERSION="0.3.0"
AGE_KEY_PATH="${HOME}/.config/sops/age/keys.txt"
SSH_KEY_PATH="${HOME}/.ssh/id_ed25519"
DEFAULT_REPO_URL="https://github.com/MichelAguilera/Medusa.git"
DEFAULT_REPO_REF="master"
DEFAULT_CLONE_DIR="${HOME}/Medusa"
SOPS_VERSION="${SOPS_VERSION:-v3.10.2}"

# --- Output helpers ---------------------------------------------------------

color_red=$'\033[31m'
color_green=$'\033[32m'
color_yellow=$'\033[33m'
color_reset=$'\033[0m'

phase_ok()   { printf "%s✓%s %s\n" "$color_green" "$color_reset" "$1"; }
phase_fail() {
    printf "%s✗%s %s: %s\n" "$color_red" "$color_reset" "$1" "$2" >&2
    exit 1
}
info()       { printf "  %s\n" "$1" >&2; }
warn()       { printf "  %s%s%s\n" "$color_yellow" "$1" "$color_reset" >&2; }

prompt() {
    local var=$1 question=$2 default=${3:-}
    local prompt_text
    if [[ -n "$default" ]]; then
        prompt_text="$question [$default]: "
    else
        prompt_text="$question: "
    fi
    local response
    read -r -p "$prompt_text" response
    if [[ -z "$response" && -n "$default" ]]; then
        response="$default"
    fi
    printf -v "$var" "%s" "$response"
}

prompt_silent() {
    local var=$1 question=$2
    local response
    read -r -s -p "$question: " response
    printf "\n"
    printf -v "$var" "%s" "$response"
}

confirm() {
    local question=$1 default=${2:-y}
    local yn_hint
    [[ "$default" == "y" ]] && yn_hint="Y/n" || yn_hint="y/N"
    local response
    read -r -p "$question [$yn_hint]: " response
    response=${response:-$default}
    [[ "${response,,}" == "y" || "${response,,}" == "yes" ]]
}

# --- Preflight --------------------------------------------------------------

REPO_URL=""
REPO_REF=""
REPO_USER=""
REPO_TOKEN=""
CLONE_DIR=""
AGE_CHOICE=""        # generate | import | reuse
AGE_IMPORT_PATH=""
SSH_CHOICE=""        # generate | import | reuse
SSH_IMPORT_PATH=""
SUDO_PASSWORD=""

preflight() {
    info "Collecting setup inputs..."

    if ! sudo -n true 2>/dev/null; then
        prompt_silent SUDO_PASSWORD "Sudo password"
        if ! printf "%s\n" "$SUDO_PASSWORD" | sudo -S -v 2>/dev/null; then
            phase_fail "preflight" "sudo verification failed"
        fi
    fi

    prompt REPO_URL "Git URL of the Medusa repo (HTTPS)" "$DEFAULT_REPO_URL"
    prompt REPO_REF "Git ref to check out" "$DEFAULT_REPO_REF"
    prompt CLONE_DIR "Path to clone the repo into" "$DEFAULT_CLONE_DIR"

    if [[ "$REPO_URL" =~ ^https:// ]]; then
        prompt REPO_USER "Git username for HTTPS auth (blank for public repo)" ""
        if [[ -n "$REPO_USER" ]]; then
            prompt_silent REPO_TOKEN "Git personal access token"
        fi
    fi

    info ""
    if [[ -f "$AGE_KEY_PATH" ]]; then
        info "Existing age key found at $AGE_KEY_PATH"
        if confirm "Reuse it" "y"; then
            AGE_CHOICE="reuse"
        else
            phase_fail "preflight" \
              "refusing to overwrite existing age key; move it aside and re-run"
        fi
    else
        info "Age key options:"
        info "  g  generate a new age key"
        info "  i  import an age key from a file"
        local age_input
        prompt age_input "Choose g or i" "g"
        case "$age_input" in
            g|G) AGE_CHOICE="generate" ;;
            i|I)
                AGE_CHOICE="import"
                prompt AGE_IMPORT_PATH "Path to age key file"
                if [[ ! -f "$AGE_IMPORT_PATH" ]]; then
                    phase_fail "preflight" \
                      "age key file not found at $AGE_IMPORT_PATH"
                fi
                ;;
            *) phase_fail "preflight" "unknown age key choice: $age_input" ;;
        esac
    fi

    info ""
    if [[ -f "$SSH_KEY_PATH" ]]; then
        info "Existing SSH key found at $SSH_KEY_PATH"
        if confirm "Reuse it" "y"; then
            SSH_CHOICE="reuse"
        else
            phase_fail "preflight" \
              "refusing to overwrite existing SSH key; move it aside and re-run"
        fi
    else
        info "SSH key options:"
        info "  g  generate a new ed25519 ssh key"
        info "  i  import an existing ssh private key from a file"
        local ssh_input
        prompt ssh_input "Choose g or i" "g"
        case "$ssh_input" in
            g|G) SSH_CHOICE="generate" ;;
            i|I)
                SSH_CHOICE="import"
                prompt SSH_IMPORT_PATH "Path to SSH private key file"
                if [[ ! -f "$SSH_IMPORT_PATH" ]]; then
                    phase_fail "preflight" \
                      "SSH key file not found at $SSH_IMPORT_PATH"
                fi
                ;;
            *) phase_fail "preflight" "unknown ssh key choice: $ssh_input" ;;
        esac
    fi

    info ""
    info "About to do the following:"
    info "  Repo:        $REPO_URL  (ref: $REPO_REF)"
    info "  Clone to:    $CLONE_DIR"
    info "  Age key:     $AGE_CHOICE${AGE_IMPORT_PATH:+ from $AGE_IMPORT_PATH}"
    info "  SSH key:     $SSH_CHOICE${SSH_IMPORT_PATH:+ from $SSH_IMPORT_PATH}"
    info ""
    if ! confirm "Proceed" "y"; then
        phase_fail "preflight" "aborted by user"
    fi

    phase_ok "preflight"
}

# --- Install ----------------------------------------------------------------

sudo_run() {
    if [[ -n "${SUDO_PASSWORD:-}" ]]; then
        printf "%s\n" "$SUDO_PASSWORD" | sudo -S -p "" "$@"
    else
        sudo "$@"
    fi
}

install_controller_sudoers() {
    # Ansible 'become: true' wraps every module in `sudo -H -S -n -u root
    # /bin/sh -c '...AnsiballZ_<module>.py'`. The command argv that sudo
    # sees is /bin/sh, not cp / install / python3 — sudoers cannot match
    # the tmp paths or wildcarded argv that Ansible generates. The
    # controller user therefore needs unrestricted NOPASSWD sudo, same
    # as every other ansible user in this repo (prep-debian.sh,
    # bootstrap-infra.sh, bootstrap-rig.sh).
    local sudoers_user sudoers_file tmp
    sudoers_user=$(id -un)
    sudoers_file="/etc/sudoers.d/medusa-controller-bootstrap"

    tmp=$(mktemp) || phase_fail "install" "creating sudoers temp file failed"
    {
        printf '# Managed by Medusa controller bootstrap.\n'
        printf '%s ALL=(ALL) NOPASSWD: ALL\n' "$sudoers_user"
    } > "$tmp"

    sudo_run install -m 0440 "$tmp" "$sudoers_file" \
        || phase_fail "install" "installing $sudoers_file failed"
    rm -f "$tmp"
    sudo_run visudo -cf "$sudoers_file" >/dev/null \
        || phase_fail "install" "$sudoers_file failed visudo validation"
}

install_phase() {
    info "Installing apt dependencies..."
    sudo_run env DEBIAN_FRONTEND=noninteractive apt-get update -qq \
        || phase_fail "install" "apt-get update failed"
    sudo_run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        age python3 git curl ca-certificates openssh-client ansible \
        || phase_fail "install" "apt-get install failed"

    info "Installing sops..."
    if ! command -v sops >/dev/null 2>&1; then
        local arch sops_arch
        arch=$(uname -m)
        case "$arch" in
            x86_64|amd64) sops_arch=amd64 ;;
            aarch64|arm64) sops_arch=arm64 ;;
            *) phase_fail "install" "unsupported architecture for sops: $arch" ;;
        esac
        curl -fsSL \
            "https://github.com/getsops/sops/releases/download/${SOPS_VERSION}/sops-${SOPS_VERSION}.linux.${sops_arch}" \
            -o /tmp/sops \
            || phase_fail "install" "sops download failed"
        sudo_run install -m 0755 /tmp/sops /usr/local/bin/sops \
            || phase_fail "install" "sops install failed"
        rm -f /tmp/sops
    fi
    command -v sops >/dev/null 2>&1 \
        || phase_fail "install" "sops installed but not on PATH"

    info "Installing uv..."
    if ! command -v uv >/dev/null 2>&1; then
        curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null \
            || phase_fail "install" "uv install failed"
    fi
    export PATH="${HOME}/.local/bin:${PATH}"
    if ! command -v uv >/dev/null 2>&1; then
        phase_fail "install" "uv installed but not on PATH"
    fi

    local clone_url="$REPO_URL"
    if [[ -n "$REPO_USER" && -n "$REPO_TOKEN" ]]; then
        clone_url="${REPO_URL/https:\/\//https:\/\/${REPO_USER}:${REPO_TOKEN}@}"
    fi

    info "Preparing repo at $CLONE_DIR..."
    if [[ -d "$CLONE_DIR/.git" ]]; then
        info "  already a git checkout; fetching $REPO_REF"
        (
            cd "$CLONE_DIR"
            original_url=$(git remote get-url origin)
            restore_remote() {
                git remote set-url origin "$original_url" >/dev/null 2>&1 || true
            }
            trap restore_remote EXIT
            if [[ "$clone_url" != "$REPO_URL" ]]; then
                git remote set-url origin "$clone_url"
            fi
            git fetch --quiet origin "$REPO_REF" \
                && git checkout --quiet "$REPO_REF" \
                && git pull --quiet --ff-only
        ) \
            || phase_fail "install" "git update failed in existing clone"
    else
        git clone --quiet --branch "$REPO_REF" "$clone_url" "$CLONE_DIR" \
            || phase_fail "install" "git clone failed"
        if [[ "$clone_url" != "$REPO_URL" ]]; then
            ( cd "$CLONE_DIR" && git remote set-url origin "$REPO_URL" )
        fi
    fi

    info "Installing Python deps (uv sync)..."
    ( cd "$CLONE_DIR" && uv sync --quiet ) \
        || phase_fail "install" "uv sync failed"

    info "Installing Ansible collections..."
    ( cd "$CLONE_DIR" && uv run --quiet ansible-galaxy collection install \
        -r ansible/requirements.yml >/dev/null ) \
        || phase_fail "install" "ansible-galaxy collection install failed"

    info "Installing controller bootstrap sudoers drop-in..."
    install_controller_sudoers

    phase_ok "install"
}

# --- Identity ---------------------------------------------------------------

AGE_PUBKEY=""

identity_phase() {
    info "Setting up age key at $AGE_KEY_PATH..."
    mkdir -p "$(dirname "$AGE_KEY_PATH")"
    chmod 700 "$(dirname "$AGE_KEY_PATH")"

    case "$AGE_CHOICE" in
        reuse)
            info "  reusing existing key"
            ;;
        generate)
            age-keygen -o "$AGE_KEY_PATH" 2>/dev/null \
                || phase_fail "identity" "age-keygen failed"
            ;;
        import)
            cp "$AGE_IMPORT_PATH" "$AGE_KEY_PATH" \
                || phase_fail "identity" "copying imported age key failed"
            ;;
    esac
    chmod 600 "$AGE_KEY_PATH"

    AGE_PUBKEY=$(age-keygen -y "$AGE_KEY_PATH" 2>/dev/null) \
        || phase_fail "identity" "unable to derive public key from $AGE_KEY_PATH"
    info "  public key: $AGE_PUBKEY"

    info "Setting up SSH key at $SSH_KEY_PATH..."
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"

    case "$SSH_CHOICE" in
        reuse)
            info "  reusing existing key"
            ;;
        generate)
            ssh-keygen -t ed25519 -N "" -f "$SSH_KEY_PATH" \
                -C "medusa-controller@$(hostname)" >/dev/null \
                || phase_fail "identity" "ssh-keygen failed"
            ;;
        import)
            cp "$SSH_IMPORT_PATH" "$SSH_KEY_PATH" \
                || phase_fail "identity" "copying imported SSH key failed"
            if [[ -f "${SSH_IMPORT_PATH}.pub" ]]; then
                cp "${SSH_IMPORT_PATH}.pub" "${SSH_KEY_PATH}.pub"
            else
                ssh-keygen -y -f "$SSH_KEY_PATH" > "${SSH_KEY_PATH}.pub" \
                    || phase_fail "identity" "deriving public key from imported SSH key failed"
            fi
            ;;
    esac
    chmod 600 "$SSH_KEY_PATH"
    chmod 644 "${SSH_KEY_PATH}.pub"

    phase_ok "identity"
}

# --- Inventory --------------------------------------------------------------

inventory_phase() {
    info "Updating .sops.yaml recipient..."
    local sops_file="$CLONE_DIR/.sops.yaml"
    if [[ -f "$sops_file" ]]; then
        if grep -q "$AGE_PUBKEY" "$sops_file"; then
            info "  .sops.yaml already lists the current age public key"
        elif grep -qE "^[[:space:]]+age:" "$sops_file"; then
            sed -i -E "0,/^([[:space:]]+age:[[:space:]]*)(.*)$/s//\1\2,${AGE_PUBKEY}/" "$sops_file" \
                || phase_fail "inventory" "updating .sops.yaml recipient failed"
            info "  appended current age public key to .sops.yaml"
        else
            cat >> "$sops_file" <<EOF

  - path_regex: secrets/.*\\.sops\\.yaml\$
    age: $AGE_PUBKEY
EOF
            info "  appended creation rule for current age public key"
        fi
    else
        cat > "$sops_file" <<EOF
creation_rules:
  - path_regex: secrets/.*\\.sops\\.yaml\$
    age: $AGE_PUBKEY
EOF
        info "  wrote $sops_file"
    fi

    phase_ok "inventory"
}

# --- Smoke test -------------------------------------------------------------

smoke_phase() {
    info "Validating Medusa inventory..."
    ( cd "$CLONE_DIR" && uv run --quiet medusa validate ) \
        || phase_fail "smoke" "medusa validate failed"

    info "Rendering Medusa generated files..."
    ( cd "$CLONE_DIR" && uv run --quiet medusa render >/dev/null ) \
        || phase_fail "smoke" "medusa render failed"

    info "Checking generated files are fresh..."
    ( cd "$CLONE_DIR" && uv run --quiet medusa check ) \
        || phase_fail "smoke" "medusa check failed"

    phase_ok "smoke"
}

# --- Main -------------------------------------------------------------------

main() {
    echo "Medusa controller bootstrap v$SCRIPT_VERSION"
    echo ""
    preflight
    echo ""
    install_phase
    echo ""
    identity_phase
    echo ""
    inventory_phase
    echo ""
    smoke_phase
    echo ""
    echo "Controller bootstrap complete."
    echo "  Repo:        $CLONE_DIR"
    echo "  Age pubkey:  $AGE_PUBKEY"
    echo ""
    echo "Next steps:"
    echo "  Add targets from your workstation:"
    echo "    medusactl add-target <name> --ip <ip> [--prep] [--authorize]"
    echo "  Then deploy:"
    echo "    medusactl deploy"
}

main "$@"
