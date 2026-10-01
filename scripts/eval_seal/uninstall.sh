#!/bin/bash
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_lib.sh
. "$HERE/_lib.sh"
seal_parse "$@"
seal_gate
seal_exec uninstall ${SEAL_REST[@]+"${SEAL_REST[@]}"}
