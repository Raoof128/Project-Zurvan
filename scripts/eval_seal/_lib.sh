#!/bin/bash
# Shared by every operator script: source it, never run it.
# Real mode: root only (via sudo), stages into a root-owned directory, then runs the SAME commands.txt that
# --render writes. --render DIR performs no privileged action at all.
set -euo pipefail
SEAL_ORIG_PATH=${PATH:-}
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
SEAL_PY=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3
SEAL_PYCACHE=/private/var/root/.zurvan-seal-pycache
SEAL_HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SEAL_REPO_DEFAULT=$(cd "$SEAL_HERE/../.." && pwd)
SEAL_RENDER=""
SEAL_REST=()

seal_die() {
    printf 'refusing: %s\n' "$1" >&2
    exit 2
}

# Split --render DIR out of the arguments; everything else lands in SEAL_REST.
seal_parse() {
    SEAL_RENDER=""
    SEAL_REST=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --render)
                [ $# -ge 2 ] || seal_die "--render needs a directory"
                SEAL_RENDER=$2
                shift 2
                ;;
            --render=*)
                SEAL_RENDER=${1#--render=}
                shift
                ;;
            *)
                SEAL_REST+=("$1")
                shift
                ;;
        esac
    done
    if [ "${SEAL_RENDER:-x}" = "" ]; then
        seal_die "--render needs a non-empty directory"
    fi
}

# Real mode only: root, through sudo, from a named development account.
seal_gate() {
    if [ -n "$SEAL_RENDER" ]; then
        return 0
    fi
    if [ "$(/usr/bin/id -u)" -ne 0 ]; then
        seal_die "real mode must run as root (sudo); use --render DIR to preview without changing anything"
    fi
    [ -n "${SUDO_USER:-}" ] || seal_die "SUDO_USER is not set: run this through sudo from the development account"
    [ "$SUDO_USER" != root ] || seal_die "SUDO_USER must be the development account, not root"
}

# seal_exec <kind> [render.py args...]
seal_exec() {
    local kind=$1
    shift
    [ -x "$SEAL_PY" ] || seal_die "the Command Line Tools python 3.9 is missing: $SEAL_PY"
    if [ -n "$SEAL_RENDER" ]; then
        "$SEAL_PY" -I -X "pycache_prefix=$SEAL_PYCACHE" "$SEAL_HERE/render.py" "$kind" \
            --repo "$SEAL_REPO_DEFAULT" --dev-user "${SUDO_USER:-devuser}" --search-path "$SEAL_ORIG_PATH" \
            "$@" --stage "$SEAL_RENDER"
        printf 'rendered the %s plan into %s (nothing was changed)\n' "$kind" "$SEAL_RENDER"
        return 0
    fi
    local dev_home stage
    dev_home=$(/usr/bin/dscl . -read "/Users/$SUDO_USER" NFSHomeDirectory | /usr/bin/awk '{print $2}')
    [ -n "$dev_home" ] || seal_die "could not resolve the home directory of $SUDO_USER"
    stage=$(/usr/bin/mktemp -d /private/var/root/zurvan-seal.XXXXXX)
    # shellcheck disable=SC2064
    trap "/bin/rm -rf '$stage'" EXIT
    "$SEAL_PY" -I -X "pycache_prefix=$SEAL_PYCACHE" "$SEAL_HERE/render.py" "$kind" \
        --repo "$SEAL_REPO_DEFAULT" --search-path "$SEAL_ORIG_PATH" \
        "$@" --stage "$stage" --real --dev-user "$SUDO_USER" --dev-home "$dev_home"
    /bin/bash -euo pipefail "$stage/commands.txt"
}
