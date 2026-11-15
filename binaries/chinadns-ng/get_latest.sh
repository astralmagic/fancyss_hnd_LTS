#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

UPX5="${UPX5:-upx-5.0.2}"
UPX4="${UPX4:-upx-4.2.4}"

for command_name in curl python3 md5sum "${UPX5}" "${UPX4}"; do
	command -v "${command_name}" >/dev/null 2>&1 || {
		echo "error: missing command: ${command_name}" >&2
		exit 127
	}
done

TAG="${1:-}"
if [ -z "${TAG}" ]; then
	JSON="$(curl -fsSL --retry 3 --retry-delay 1 https://api.github.com/repos/zfl9/chinadns-ng/releases/latest)"
	TAG="$(JSON_PAYLOAD="${JSON}" python3 -c 'import json, os; print(json.loads(os.environ["JSON_PAYLOAD"])["tag_name"])')"
fi
[ -n "${TAG}" ] || {
	echo "error: failed to determine chinadns-ng release" >&2
	exit 1
}

if [ "$#" -eq 0 ] && [ -f latest.txt ]; then
	CURRENT_TAG="$(tr -d '\r\n' < latest.txt)"
	if [ "${CURRENT_TAG}" != "${TAG}" ] && [ "$(printf '%s\n' "${CURRENT_TAG}" "${TAG}" | sort -V | tail -n 1)" = "${CURRENT_TAG}" ]; then
		echo "current ${CURRENT_TAG} is newer than release ${TAG}; skipping downgrade"
		exit 0
	fi
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

ASSETS=(
	'chinadns-ng+wolfssl@aarch64-linux-musl@generic+v8a@fast+lto'
	'chinadns-ng+wolfssl@arm-linux-musleabi@generic+v7a@fast+lto'
	'chinadns-ng+wolfssl@arm-linux-musleabi@generic+v5te+soft_float@fast+lto'
)

for asset in "${ASSETS[@]}"; do
	curl -fL --retry 3 --retry-delay 1 --progress-bar \
		-o "${TMP_DIR}/${asset}" \
		"https://github.com/zfl9/chinadns-ng/releases/download/${TAG}/${asset}"
	chmod +x "${TMP_DIR}/${asset}"
	cp -f "${TMP_DIR}/${asset}" "${asset}"
	cp -f "${TMP_DIR}/${asset}" "${asset}.upx"
	case "${asset}" in
		*generic+v5te*)
			"${UPX4}" --lzma --ultra-brute "${asset}.upx" >/dev/null
			"${UPX4}" -t "${asset}.upx" >/dev/null
			;;
		*)
			"${UPX5}" --lzma --ultra-brute "${asset}.upx" >/dev/null
			"${UPX5}" -t "${asset}.upx" >/dev/null
			;;
	esac
done

md5sum "${ASSETS[@]}" > md5sum.txt
for asset in "${ASSETS[@]}"; do
	md5sum "${asset}.upx" >> md5sum.txt
done
printf '%s' "${TAG}" > latest.txt

echo "done: ${TAG}"
