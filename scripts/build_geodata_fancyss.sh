#!/bin/sh

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
[ "${FSS_SKIP_GEODATA_BUILD:-0}" = "1" ] && exit 0
"${SCRIPT_DIR}/build_geosite_fancyss.sh"
"${SCRIPT_DIR}/build_geoip_fancyss.sh"
