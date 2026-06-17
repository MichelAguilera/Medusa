#!/usr/bin/env bash
# Medusa OS-dispatching prep entrypoint.
#
# Detects the target's distro family and execs the matching prep-<family>.sh
# sitting beside it (prep-debian.sh / prep-arch.sh). All MEDUSA_PREP_* env
# vars pass straight through (exec inherits the environment), so the family
# scripts keep their existing contract unchanged.
#
# medusactl ships this file plus both family scripts to the target and runs
# this one as root, so a single prep flow now covers Debian- and Arch-family
# hosts without the caller having to know the OS in advance.

set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

family=""
if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
        *" debian "*|*" ubuntu "*) family=debian ;;
        *" arch "*)                family=arch ;;
    esac
fi
# Fallbacks when os-release is missing or unhelpful.
if [[ -z "$family" ]]; then
    if [[ -f /etc/debian_version ]]; then
        family=debian
    elif [[ -f /etc/arch-release ]]; then
        family=arch
    fi
fi

if [[ -z "$family" ]]; then
    echo "prep-host: unsupported distro (no debian/arch match in /etc/os-release)" >&2
    exit 1
fi

script="$here/prep-$family.sh"
if [[ ! -f "$script" ]]; then
    echo "prep-host: missing companion script $script" >&2
    exit 1
fi

echo "prep-host: detected $family family; running $(basename "$script")"
exec bash "$script"
