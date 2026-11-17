#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

UPX5="${UPX5:-upx-5.0.2}"
UPX4="${UPX4:-upx-4.2.4}"
RELEASES_API_URL="${RELEASES_API_URL:-${API_URL:-https://api.github.com/repos/Itsusinn/tuic/releases?per_page=30}}"
TARGET_TAG="${TARGET_TAG:-${1:-}}"
RELEASE_JSON_FILE="${RELEASE_JSON_FILE:-}"
DRY_RUN_RESOLVE="${DRY_RUN_RESOLVE:-0}"
FORCE_REDOWNLOAD="${FORCE_REDOWNLOAD:-0}"
ALLOW_UNSTABLE_TUIC="${ALLOW_UNSTABLE_TUIC:-0}"
ALLOW_TUIC_V2="${ALLOW_TUIC_V2:-0}"

require_cmd() {
	if ! command -v "$1" >/dev/null 2>&1; then
		echo "error: missing command: $1" >&2
		exit 127
	fi
}

calc_sha256() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$1" | awk '{print $1}'
	else
		python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
	fi
}

require_cmd python3
if [ -z "${RELEASE_JSON_FILE}" ]; then
	require_cmd curl
fi
if [ "${DRY_RUN_RESOLVE}" != "1" ]; then
	require_cmd curl
	require_cmd md5sum
	require_cmd "${UPX5}"
	require_cmd "${UPX4}"
fi

echo "fetching tuic release info ..." >&2
if [ -n "${RELEASE_JSON_FILE}" ]; then
	JSON="$(cat "${RELEASE_JSON_FILE}")"
else
	JSON="$(curl -fsSL --retry 3 --retry-delay 1 "${RELEASES_API_URL}")"
fi

META_LINES="$(
	JSON_PAYLOAD="${JSON}" \
	TARGET_TAG="${TARGET_TAG}" \
	ALLOW_UNSTABLE_TUIC="${ALLOW_UNSTABLE_TUIC}" \
	ALLOW_TUIC_V2="${ALLOW_TUIC_V2}" \
	python3 - <<'PY'
import json
import os
import re
import sys

raw = json.loads(os.environ["JSON_PAYLOAD"])
releases = raw if isinstance(raw, list) else [raw]
target_tag = os.environ.get("TARGET_TAG", "").strip()
allow_unstable = os.environ.get("ALLOW_UNSTABLE_TUIC", "0") == "1"
allow_v2 = os.environ.get("ALLOW_TUIC_V2", "0") == "1"

unstable_re = re.compile(r"(?i)(?:-(?:dev|rc|alpha|beta|nightly)|^nightly)")
stable_v1_re = re.compile(r"^v1\.[0-9]+\.[0-9]+$")
any_ver_re = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")

def is_valid_release(rel):
    tag = (rel.get("tag_name") or "").strip()
    if not tag:
        return False, "empty tag"
    if rel.get("draft"):
        return False, f"draft release: {tag}"
    if not allow_unstable:
        if rel.get("prerelease"):
            return False, f"prerelease skipped: {tag}"
        if unstable_re.search(tag):
            return False, f"unstable tag rejected: {tag}"
    if not allow_v2:
        if not stable_v1_re.match(tag):
            return False, f"incompatible non-v1 tuic tag skipped: {tag}"
    else:
        if not any_ver_re.match(tag) and not allow_unstable:
            return False, f"non-semver tag skipped: {tag}"
    return True, ""

chosen = None
if target_tag:
    for rel in releases:
        if (rel.get("tag_name") or "").strip() == target_tag:
            ok, reason = is_valid_release(rel)
            if not ok:
                print(f"error: requested tuic release {target_tag} rejected ({reason})", file=sys.stderr)
                sys.exit(1)
            chosen = rel
            break
    if chosen is None:
        print(f"error: requested tuic release not found: {target_tag}", file=sys.stderr)
        sys.exit(1)
else:
    for rel in releases:
        ok, _ = is_valid_release(rel)
        if ok:
            chosen = rel
            break
    if chosen is None:
        print("error: no compatible stable v1 tuic release found", file=sys.stderr)
        sys.exit(1)

tag = chosen["tag_name"].strip()
assets = {a.get("name"): a for a in chosen.get("assets", []) if a.get("name")}
arm64 = assets.get("tuic-client-aarch64-linux-musl")
armv7 = assets.get("tuic-client-armv7-linux-musl")
if not arm64 or not armv7:
    print(f"error: missing required assets in tuic release {tag}", file=sys.stderr)
    sys.exit(1)

def parse_sha256(asset):
    digest = (asset.get("digest") or "").strip()
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    return ""

url64 = (arm64.get("browser_download_url") or "").strip()
urlv7 = (armv7.get("browser_download_url") or "").strip()
if not url64 or not urlv7:
    print(f"error: failed to locate one or more tuic-client asset download urls for {tag}", file=sys.stderr)
    sys.exit(1)

print(tag)
print(url64)
print(parse_sha256(arm64))
print(urlv7)
print(parse_sha256(armv7))
PY
)"

{
	IFS= read -r TAG
	IFS= read -r URL_ARM64
	IFS= read -r SHA256_ARM64
	IFS= read -r URL_ARMV7
	IFS= read -r SHA256_ARMV7
} <<EOF
${META_LINES}
EOF

if [ -z "${TAG}" ] || [ -z "${URL_ARM64}" ] || [ -z "${URL_ARMV7}" ]; then
	echo "error: failed to parse release metadata" >&2
	exit 1
fi

VER="${TAG#v}"
OUT_DIR="v${VER}"

if [ "${DRY_RUN_RESOLVE}" = "1" ]; then
	printf 'TAG=%s\n' "${TAG}"
	printf 'OUT_DIR=%s\n' "${OUT_DIR}"
	printf 'URL_ARM64=%s\n' "${URL_ARM64}"
	printf 'SHA256_ARM64=%s\n' "${SHA256_ARM64}"
	printf 'URL_ARMV7=%s\n' "${URL_ARMV7}"
	printf 'SHA256_ARMV7=%s\n' "${SHA256_ARMV7}"
	exit 0
fi

ASSET_ARM64="tuic-client-aarch64-linux-musl"
ASSET_ARMV7="tuic-client-armv7-linux-musl"

if [ -d "${OUT_DIR}" ] && [ "${FORCE_REDOWNLOAD}" != "1" ]; then
	echo "already exists: ${SCRIPT_DIR}/${OUT_DIR}"
	echo -n "${OUT_DIR}" > latest.txt
	exit 0
fi

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

echo "selected tuic release: ${TAG}"
echo "downloading ${ASSET_ARM64} ..."
curl -fL --retry 3 --retry-delay 1 --progress-bar -o "${TMP_DIR}/tuic-client_arm64" "${URL_ARM64}"
echo "downloading ${ASSET_ARMV7} ..."
curl -fL --retry 3 --retry-delay 1 --progress-bar -o "${TMP_DIR}/tuic-client_armv7" "${URL_ARMV7}"

if [ -n "${SHA256_ARM64}" ]; then
	actual64="$(calc_sha256 "${TMP_DIR}/tuic-client_arm64")"
	if [ "${actual64}" != "${SHA256_ARM64}" ]; then
		echo "error: sha256 mismatch for ${ASSET_ARM64}: expected ${SHA256_ARM64}, got ${actual64}" >&2
		exit 1
	fi
	echo "verified sha256: ${ASSET_ARM64} (${actual64})"
fi
if [ -n "${SHA256_ARMV7}" ]; then
	actualv7="$(calc_sha256 "${TMP_DIR}/tuic-client_armv7")"
	if [ "${actualv7}" != "${SHA256_ARMV7}" ]; then
		echo "error: sha256 mismatch for ${ASSET_ARMV7}: expected ${SHA256_ARMV7}, got ${actualv7}" >&2
		exit 1
	fi
	echo "verified sha256: ${ASSET_ARMV7} (${actualv7})"
fi

chmod +x "${TMP_DIR}/tuic-client_arm64" "${TMP_DIR}/tuic-client_armv7"

mkdir -p "${OUT_DIR}"
cp -f "${TMP_DIR}/tuic-client_arm64" "${OUT_DIR}/tuic-client_arm64"
cp -f "${TMP_DIR}/tuic-client_armv7" "${OUT_DIR}/tuic-client_armv7"

echo "compressing with ${UPX5}: tuic-client_arm64"
"${UPX5}" --lzma --ultra-brute "${OUT_DIR}/tuic-client_arm64"
echo "compressing with ${UPX4}: tuic-client_armv7"
"${UPX4}" --lzma --ultra-brute "${OUT_DIR}/tuic-client_armv7"

"${UPX5}" -t "${OUT_DIR}/tuic-client_arm64" >/dev/null
"${UPX4}" -t "${OUT_DIR}/tuic-client_armv7" >/dev/null

(cd "${OUT_DIR}" && md5sum tuic-client_arm64 tuic-client_armv7 > md5sum.txt)

echo -n "${OUT_DIR}" > latest.txt
echo "done: ${SCRIPT_DIR}/${OUT_DIR}"
