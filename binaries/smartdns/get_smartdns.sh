#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

UPX5="${UPX5:-upx-5.0.2}"
UPX4="${UPX4:-upx-4.2.4}"
RELEASES_API_URL="${RELEASES_API_URL:-${API_URL:-https://api.github.com/repos/pymumu/smartdns/releases?per_page=30}}"
TARGET_TAG="${TARGET_TAG:-${1:-}}"
RELEASE_JSON_FILE="${RELEASE_JSON_FILE:-}"
DRY_RUN_RESOLVE="${DRY_RUN_RESOLVE:-0}"
FORCE_REDOWNLOAD="${FORCE_REDOWNLOAD:-0}"
ALLOW_PRERELEASE_SMARTDNS="${ALLOW_PRERELEASE_SMARTDNS:-0}"

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

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

echo "fetching smartdns release info ..." >&2
if [ -n "${RELEASE_JSON_FILE}" ]; then
	JSON_FILE="${RELEASE_JSON_FILE}"
else
	CURL_API_ARGS=(-fsSL --retry 3 --retry-delay 1 -H "Accept: application/vnd.github+json")
	if [ -n "${GITHUB_TOKEN:-}" ]; then
		CURL_API_ARGS+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
	fi
	curl "${CURL_API_ARGS[@]}" -o "${TMP_DIR}/releases.json" "${RELEASES_API_URL}"
	JSON_FILE="${TMP_DIR}/releases.json"
fi

META_LINES="$(
	JSON_FILE="${JSON_FILE}" \
	TARGET_TAG="${TARGET_TAG}" \
	ALLOW_PRERELEASE_SMARTDNS="${ALLOW_PRERELEASE_SMARTDNS}" \
	python3 - <<'PY'
import json
import os
import re
import sys

with open(os.environ["JSON_FILE"], "r", encoding="utf-8") as fh:
    raw = json.load(fh)
releases = raw if isinstance(raw, list) else [raw]
target_tag = os.environ.get("TARGET_TAG", "").strip()
allow_pre = os.environ.get("ALLOW_PRERELEASE_SMARTDNS", "0") == "1"

unstable_re = re.compile(r"(?i)(?:-(?:dev|rc|alpha|beta|nightly)|^nightly)")
stable_re = re.compile(r"^Release[0-9]+(?:\.[0-9]+)*$")

def is_valid_release(rel):
    tag = (rel.get("tag_name") or "").strip()
    if not tag:
        return False, "empty tag"
    if rel.get("draft"):
        return False, f"draft release: {tag}"
    if not allow_pre:
        if rel.get("prerelease"):
            return False, f"prerelease skipped: {tag}"
        if unstable_re.search(tag):
            return False, f"prerelease/rc tag rejected: {tag}"
        if not stable_re.match(tag):
            return False, f"non-release tag skipped: {tag}"
    return True, ""

chosen = None
if target_tag:
    for rel in releases:
        if (rel.get("tag_name") or "").strip() == target_tag:
            ok, reason = is_valid_release(rel)
            if not ok:
                print(f"error: requested smartdns release {target_tag} rejected ({reason})", file=sys.stderr)
                sys.exit(1)
            chosen = rel
            break
    if chosen is None:
        print(f"error: requested smartdns release not found: {target_tag}", file=sys.stderr)
        sys.exit(1)
else:
    for rel in releases:
        ok, _ = is_valid_release(rel)
        if ok:
            chosen = rel
            break
    if chosen is None:
        print("error: no compatible stable smartdns release found", file=sys.stderr)
        sys.exit(1)

tag = chosen["tag_name"].strip()
assets = {a.get("name"): a for a in chosen.get("assets", []) if a.get("name")}
arm64 = assets.get("smartdns-aarch64")
arm = assets.get("smartdns-arm")
if not arm64 or not arm:
    print(f"error: missing required assets in smartdns release {tag}", file=sys.stderr)
    sys.exit(1)

def parse_sha256(asset):
    digest = (asset.get("digest") or "").strip()
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    return ""

url64 = (arm64.get("browser_download_url") or "").strip()
url32 = (arm.get("browser_download_url") or "").strip()
if not url64 or not url32:
    print(f"error: failed to locate asset download urls (smartdns-aarch64 / smartdns-arm) for {tag}", file=sys.stderr)
    sys.exit(1)

print(tag)
print(url64)
print(parse_sha256(arm64))
print(url32)
print(parse_sha256(arm))
PY
)"

{
	IFS= read -r TAG
	IFS= read -r URL_AARCH64
	IFS= read -r SHA256_AARCH64
	IFS= read -r URL_ARM
	IFS= read -r SHA256_ARM
} <<EOF
${META_LINES}
EOF

if [ -z "${TAG}" ] || [ -z "${URL_AARCH64}" ] || [ -z "${URL_ARM}" ]; then
	echo "error: failed to parse tag_name or asset URLs from GitHub API response" >&2
	exit 1
fi

VER="${TAG}"
VER="${VER#Release}"
VER="${VER#v}"
if [ -z "${VER}" ]; then
	VER="${TAG}"
fi

OUT_DIR="v${VER}"

if [ "${DRY_RUN_RESOLVE}" = "1" ]; then
	printf 'TAG=%s\n' "${TAG}"
	printf 'OUT_DIR=%s\n' "${OUT_DIR}"
	printf 'URL_AARCH64=%s\n' "${URL_AARCH64}"
	printf 'SHA256_AARCH64=%s\n' "${SHA256_AARCH64}"
	printf 'URL_ARM=%s\n' "${URL_ARM}"
	printf 'SHA256_ARM=%s\n' "${SHA256_ARM}"
	exit 0
fi

if [ -d "${OUT_DIR}" ] && [ "${FORCE_REDOWNLOAD}" != "1" ]; then
	echo -n "${OUT_DIR}" > latest.txt
	echo "already exists: ${SCRIPT_DIR}/${OUT_DIR}"
	exit 0
fi

echo "selected smartdns release: ${TAG}"
echo "downloading smartdns-aarch64 ..."
curl -fL --retry 3 --retry-delay 1 --progress-bar -o "${TMP_DIR}/smartdns-aarch64" "${URL_AARCH64}"
echo "downloading smartdns-arm ..."
curl -fL --retry 3 --retry-delay 1 --progress-bar -o "${TMP_DIR}/smartdns-arm" "${URL_ARM}"

if [ -n "${SHA256_AARCH64}" ]; then
	actual64="$(calc_sha256 "${TMP_DIR}/smartdns-aarch64")"
	if [ "${actual64}" != "${SHA256_AARCH64}" ]; then
		echo "error: sha256 mismatch for smartdns-aarch64: expected ${SHA256_AARCH64}, got ${actual64}" >&2
		exit 1
	fi
	echo "verified sha256: smartdns-aarch64 (${actual64})"
fi
if [ -n "${SHA256_ARM}" ]; then
	actualarm="$(calc_sha256 "${TMP_DIR}/smartdns-arm")"
	if [ "${actualarm}" != "${SHA256_ARM}" ]; then
		echo "error: sha256 mismatch for smartdns-arm: expected ${SHA256_ARM}, got ${actualarm}" >&2
		exit 1
	fi
	echo "verified sha256: smartdns-arm (${actualarm})"
fi

chmod +x "${TMP_DIR}/smartdns-aarch64" "${TMP_DIR}/smartdns-arm"

mkdir -p "${OUT_DIR}"

cp -f "${TMP_DIR}/smartdns-aarch64" "${OUT_DIR}/smartdns_arm64"
cp -f "${TMP_DIR}/smartdns-arm" "${OUT_DIR}/smartdns_armv7"
cp -f "${TMP_DIR}/smartdns-arm" "${OUT_DIR}/smartdns_armv5"

chmod +x "${OUT_DIR}/smartdns_arm64" "${OUT_DIR}/smartdns_armv7" "${OUT_DIR}/smartdns_armv5"

echo "compressing with ${UPX5}: smartdns_arm64"
"${UPX5}" --lzma --ultra-brute "${OUT_DIR}/smartdns_arm64"
echo "compressing with ${UPX5}: smartdns_armv7"
"${UPX5}" --lzma --ultra-brute "${OUT_DIR}/smartdns_armv7"
echo "compressing with ${UPX4}: smartdns_armv5"
"${UPX4}" --lzma --ultra-brute "${OUT_DIR}/smartdns_armv5"

"${UPX5}" -t "${OUT_DIR}/smartdns_arm64" >/dev/null
"${UPX5}" -t "${OUT_DIR}/smartdns_armv7" >/dev/null
"${UPX4}" -t "${OUT_DIR}/smartdns_armv5" >/dev/null

(cd "${OUT_DIR}" && md5sum smartdns_arm64 smartdns_armv7 smartdns_armv5 > md5sum.txt)
echo -n "${OUT_DIR}" > latest.txt

echo "done: ${SCRIPT_DIR}/${OUT_DIR}"
