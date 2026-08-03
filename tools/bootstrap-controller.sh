#!/usr/bin/env bash
# Medusa controller bootstrap. See docs/bootstrap.md for full reference.
#
# Two-repo layout (T-036): code lives in the public Medusa repo, operator
# inventory + secrets live in the private medusa-inventory repo. Generated
# artifacts go to an XDG state dir on the controller, never into either repo.
#
# Phases:
#   1. preflight    collect inputs, validate, confirm
#   2. install      apt deps, uv, code clone, inventory clone, python deps,
#                   ansible collections, controller sudoers
#   3. identity     age key, ssh key
#   4. inventory    .sops.yaml recipient management (in inventory repo)
#   5. smoke        medusa validate / render / check with split paths
#
# Idempotent: existing keys, clones, and configs are detected and reused
# rather than overwritten.

set -euo pipefail

SCRIPT_VERSION="0.4.0"
AGE_KEY_PATH="${HOME}/.config/sops/age/keys.txt"
SSH_KEY_PATH="${HOME}/.ssh/id_ed25519"

DEFAULT_CODE_REPO_URL="https://github.com/MichelAguilera/Medusa.git"
DEFAULT_CODE_REPO_REF="main"
DEFAULT_CODE_CLONE_DIR="${HOME}/Medusa"

# Inventory repo URL is operator-specific: only the operator who owns the
# private inventory repo can clone it. Read from MEDUSA_INVENTORY_REPO_URL
# when set; otherwise show a placeholder that the operator must edit. The
# upstream Medusa repo intentionally does not ship a real owner here.
DEFAULT_INVENTORY_REPO_URL="${MEDUSA_INVENTORY_REPO_URL:-https://github.com/<you>/medusa-inventory.git}"
DEFAULT_INVENTORY_REPO_REF="main"
DEFAULT_INVENTORY_CLONE_DIR="${HOME}/medusa-inventory"

DEFAULT_GENERATED_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/medusa/generated"

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

CODE_REPO_URL=""
CODE_REPO_REF=""
CODE_CLONE_DIR=""

INVENTORY_REPO_URL=""
INVENTORY_REPO_REF=""
INVENTORY_CLONE_DIR=""

GENERATED_DIR=""

INVENTORY_AUTH=""          # gh | ssh | https
INVENTORY_HTTPS_USER=""
INVENTORY_HTTPS_TOKEN=""

AGE_CHOICE=""              # generate | import | reuse
AGE_IMPORT_PATH=""
SSH_CHOICE=""              # generate | import | reuse
SSH_IMPORT_PATH=""
SUDO_PASSWORD=""

# Detect how to authenticate the private inventory clone. Order:
#   1. gh CLI installed and authenticated → use 'gh repo clone'
#   2. SSH key present and github.com reachable → use ssh URL form
#   3. fallback: prompt for HTTPS user + PAT
detect_inventory_auth() {
    if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        info "gh CLI authenticated; will use 'gh repo clone' for the private inventory repo"
        INVENTORY_AUTH="gh"
        return
    fi
    if [[ -f "$SSH_KEY_PATH" ]] && ssh -o BatchMode=yes -o ConnectTimeout=5 -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
        info "SSH key authenticated with github.com; will use SSH URL for the private inventory repo"
        INVENTORY_AUTH="ssh"
        return
    fi
    warn "no gh CLI auth and no SSH access to github.com; falling back to HTTPS + personal access token"
    INVENTORY_AUTH="https"
    prompt INVENTORY_HTTPS_USER "GitHub username for HTTPS auth"
    prompt_silent INVENTORY_HTTPS_TOKEN "GitHub personal access token (repo scope)"
    [[ -n "$INVENTORY_HTTPS_USER" && -n "$INVENTORY_HTTPS_TOKEN" ]] \
        || phase_fail "preflight" "HTTPS auth requires both username and token"
}

preflight() {
    info "Collecting setup inputs..."

    if ! sudo -n true 2>/dev/null; then
        prompt_silent SUDO_PASSWORD "Sudo password"
        if ! printf "%s\n" "$SUDO_PASSWORD" | sudo -S -v 2>/dev/null; then
            phase_fail "preflight" "sudo verification failed"
        fi
    fi

    info ""
    info "Code repo (public Medusa) — runnable medusa package + ansible playbooks"
    prompt CODE_REPO_URL "Git URL of the code repo" "$DEFAULT_CODE_REPO_URL"
    prompt CODE_REPO_REF "Git ref to check out" "$DEFAULT_CODE_REPO_REF"
    prompt CODE_CLONE_DIR "Path to clone the code repo into" "$DEFAULT_CODE_CLONE_DIR"

    info ""
    info "Inventory repo (private medusa-inventory) — DNS/services/storage YAML + secrets"
    prompt INVENTORY_REPO_URL "Git URL of the inventory repo" "$DEFAULT_INVENTORY_REPO_URL"
    prompt INVENTORY_REPO_REF "Git ref to check out" "$DEFAULT_INVENTORY_REPO_REF"
    prompt INVENTORY_CLONE_DIR "Path to clone the inventory repo into" "$DEFAULT_INVENTORY_CLONE_DIR"

    info ""
    info "Generated artifacts (XDG state, neither repo)"
    prompt GENERATED_DIR "Path for generated files" "$DEFAULT_GENERATED_DIR"

    info ""
    detect_inventory_auth

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
    info "  Code:        $CODE_REPO_URL  (ref: $CODE_REPO_REF) → $CODE_CLONE_DIR"
    info "  Inventory:   $INVENTORY_REPO_URL  (ref: $INVENTORY_REPO_REF) → $INVENTORY_CLONE_DIR"
    info "  Inv auth:    $INVENTORY_AUTH"
    info "  Generated:   $GENERATED_DIR"
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
    # as every other ansible user in this repo (bootstrap-infra.sh,
    # bootstrap-rig.sh).
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

# Clone or update a single repo. Public repos can pass auth="" to skip
# credential handling; private repos pass auth="gh", "ssh", or "https".
clone_repo() {
    local url=$1 ref=$2 dir=$3 label=$4 auth=$5
    local effective_url="$url"

    case "$auth" in
        gh)
            # gh repo clone normalizes URL based on gh's git_protocol setting;
            # passing the slug avoids drift. Extract owner/name from HTTPS URL.
            local slug
            slug=$(printf "%s" "$url" | sed -E 's|^https?://github\.com/||; s|\.git$||')
            if [[ -d "$dir/.git" ]]; then
                info "$label: existing checkout at $dir, fetching $ref"
                ( cd "$dir" && git fetch --quiet origin "$ref" \
                    && git checkout --quiet "$ref" \
                    && git pull --quiet --ff-only ) \
                    || phase_fail "install" "$label: git update failed"
            else
                info "$label: cloning $slug via gh into $dir"
                gh repo clone "$slug" "$dir" -- --branch "$ref" --quiet \
                    || phase_fail "install" "$label: gh repo clone failed"
            fi
            return
            ;;
        ssh)
            effective_url=$(printf "%s" "$url" | sed -E 's|^https?://github\.com/|git@github.com:|')
            ;;
        https)
            effective_url="${url/https:\/\//https:\/\/${INVENTORY_HTTPS_USER}:${INVENTORY_HTTPS_TOKEN}@}"
            ;;
        "")
            : # public, no auth munging
            ;;
        *)
            phase_fail "install" "$label: unknown auth method $auth"
            ;;
    esac

    if [[ -d "$dir/.git" ]]; then
        info "$label: existing checkout at $dir, fetching $ref"
        (
            cd "$dir"
            local original_url
            original_url=$(git remote get-url origin)
            restore_remote() {
                # Strip embedded token from origin URL after the operation,
                # leaving the plain HTTPS form behind.
                git remote set-url origin "$url" >/dev/null 2>&1 || true
            }
            trap restore_remote EXIT
            if [[ "$effective_url" != "$original_url" ]]; then
                git remote set-url origin "$effective_url"
            fi
            git fetch --quiet origin "$ref" \
                && git checkout --quiet "$ref" \
                && git pull --quiet --ff-only
        ) \
            || phase_fail "install" "$label: git update failed"
    else
        info "$label: cloning into $dir"
        git clone --quiet --branch "$ref" "$effective_url" "$dir" \
            || phase_fail "install" "$label: git clone failed"
        # Strip embedded token from origin URL so it isn't persisted to disk.
        if [[ "$auth" == "https" ]]; then
            ( cd "$dir" && git remote set-url origin "$url" )
        fi
    fi
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

    info "Preparing code repo at $CODE_CLONE_DIR..."
    clone_repo "$CODE_REPO_URL" "$CODE_REPO_REF" "$CODE_CLONE_DIR" "code" ""

    info "Preparing inventory repo at $INVENTORY_CLONE_DIR..."
    clone_repo "$INVENTORY_REPO_URL" "$INVENTORY_REPO_REF" "$INVENTORY_CLONE_DIR" "inventory" "$INVENTORY_AUTH"

    info "Creating generated artifacts directory at $GENERATED_DIR..."
    mkdir -p "$GENERATED_DIR" \
        || phase_fail "install" "failed to create $GENERATED_DIR"

    info "Installing Python deps (uv sync)..."
    ( cd "$CODE_CLONE_DIR" && uv sync --quiet ) \
        || phase_fail "install" "uv sync failed"

    info "Installing Ansible collections..."
    ( cd "$CODE_CLONE_DIR" && uv run --quiet ansible-galaxy collection install \
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
    info "Updating .sops.yaml recipient in inventory repo..."
    local sops_file="$INVENTORY_CLONE_DIR/.sops.yaml"
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
    # Run medusa from the code repo (where pyproject.toml is) with env
    # overrides pointing at the split inventory + generated locations.
    local -a env_overrides=(
        "MEDUSA_INVENTORY_DIR=$INVENTORY_CLONE_DIR/inventory"
        "MEDUSA_SECRETS_DIR=$INVENTORY_CLONE_DIR/secrets"
        "MEDUSA_GENERATED_DIR=$GENERATED_DIR"
    )

    info "Validating Medusa inventory..."
    ( cd "$CODE_CLONE_DIR" && env "${env_overrides[@]}" uv run --quiet medusa validate ) \
        || phase_fail "smoke" "medusa validate failed"

    info "Rendering Medusa generated files..."
    ( cd "$CODE_CLONE_DIR" && env "${env_overrides[@]}" uv run --quiet medusa render >/dev/null ) \
        || phase_fail "smoke" "medusa render failed"

    info "Checking generated files are fresh..."
    ( cd "$CODE_CLONE_DIR" && env "${env_overrides[@]}" uv run --quiet medusa check ) \
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
    echo "  Code:        $CODE_CLONE_DIR"
    echo "  Inventory:   $INVENTORY_CLONE_DIR"
    echo "  Generated:   $GENERATED_DIR"
    echo "  Age pubkey:  $AGE_PUBKEY"
    echo ""
    echo "Next steps:"
    echo "  Add targets from your workstation:"
    echo "    medusactl add-target <name> --ip <ip> [--prep] [--authorize]"
    echo "  Then deploy:"
    echo "    medusactl deploy"
}

main "$@"
