#!/bin/bash
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_lib.sh
. "$HERE/_lib.sh"
seal_parse "$@"
seal_gate
[ "${#SEAL_REST[@]}" -ge 1 ] || seal_die "usage: install_corpus.sh <export-dir> [options]"
EXPORT=${SEAL_REST[0]}
REST=("${SEAL_REST[@]:1}")
seal_exec install-corpus --export-dir="$EXPORT" ${REST[@]+"${REST[@]}"}
