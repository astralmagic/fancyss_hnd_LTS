#!/bin/sh

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
BASE_DIR="${REPO_ROOT}/.build_geodata"
GO_VERSION="1.27.4"
GO_SHA256_AMD64="7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840"
GO_SHA256_ARM64="3392582988a273a87ab5043ee1b2c9c17a2c3c00580bf9326c47a2e0aaaeac92"
GO_SHA256_DARWIN_AMD64="2b799377704ef12db39bd254995d5c348c9cc8d86562ca817724390e56c38f43"
GO_SHA256_DARWIN_ARM64="91a44a2b8a716600ad94a4f143e156116620db8828086a6e3602291be9bd0bb4"
OS_NAME="$(uname -s | tr 'A-Z' 'a-z')"
ARCH_NAME="$(uname -m)"
GO_ARCH=""
GO_TARBALL=""
GO_ROOT="${BASE_DIR}/go"
GO_BIN="${GO_ROOT}/bin/go"

case "${OS_NAME}" in
linux|darwin)
	;;
*)
	echo "unsupported host OS: ${OS_NAME}" >&2
	exit 1
	;;
esac

case "${ARCH_NAME}" in
x86_64|amd64)
	GO_ARCH="amd64"
	;;
aarch64|arm64)
	GO_ARCH="arm64"
	;;
*)
	echo "unsupported host arch: ${ARCH_NAME}" >&2
	exit 1
	;;
esac

mkdir -p "${BASE_DIR}"
GO_TARBALL="${BASE_DIR}/go${GO_VERSION}.${OS_NAME}-${GO_ARCH}.tar.gz"

if [ ! -x "${GO_BIN}" ]; then
	if [ ! -f "${GO_TARBALL}" ]; then
		curl -fsSL --connect-timeout 20 --retry 3 --retry-delay 1 \
			"https://dl.google.com/go/go${GO_VERSION}.${OS_NAME}-${GO_ARCH}.tar.gz" \
			-o "${GO_TARBALL}"
	fi
	if [ "${OS_NAME}" = "darwin" ]; then
		GO_SHA256="${GO_SHA256_DARWIN_AMD64}"
		[ "${GO_ARCH}" = "arm64" ] && GO_SHA256="${GO_SHA256_DARWIN_ARM64}"
	else
		GO_SHA256="${GO_SHA256_AMD64}"
		[ "${GO_ARCH}" = "arm64" ] && GO_SHA256="${GO_SHA256_ARM64}"
	fi
	if command -v sha256sum >/dev/null 2>&1; then
		printf '%s  %s\n' "${GO_SHA256}" "${GO_TARBALL}" | sha256sum -c -
	else
		printf '%s  %s\n' "${GO_SHA256}" "${GO_TARBALL}" | shasum -a 256 -c -
	fi
	rm -rf "${GO_ROOT}"
	tar -C "${BASE_DIR}" -xzf "${GO_TARBALL}"
fi

printf '%s\n' "${GO_BIN}"
