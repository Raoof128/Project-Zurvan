#!/bin/bash
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_lib.sh
. "$HERE/_lib.sh"
seal_parse "$@"
seal_gate
[ "${#SEAL_REST[@]}" -ge 1 ] || seal_die "usage: install.sh <40-hex commit sha> [options]"
SHA=${SEAL_REST[0]}
[[ $SHA =~ ^[0-9a-f]{40}$ ]] || seal_die "argument 1 must be a 40-hex lowercase commit sha"
REST=("${SEAL_REST[@]:1}")
seal_exec install --sha="$SHA" ${REST[@]+"${REST[@]}"}
