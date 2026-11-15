#!/usr/bin/env bash
set -euo pipefail

resolve_real_zig() {
    local self_dir=""
    local self_path=""
    local candidate=""
    local -a candidates=()
    local -a path_dirs=()

    if [[ -n "${REAL_ZIG:-}" ]]; then
        if [[ ! -x "${REAL_ZIG}" ]]; then
            echo "error: REAL_ZIG is not executable: ${REAL_ZIG}" >&2
            exit 1
        fi
        printf '%s\n' "${REAL_ZIG}"
        return 0
    fi

    self_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    self_path="${self_dir}/$(basename -- "${BASH_SOURCE[0]}")"

    while IFS= read -r candidate; do
        [[ -x "$candidate" ]] && candidates+=("$candidate")
    done < <(find /opt -maxdepth 3 -type f -name zig 2>/dev/null | (sort -V 2>/dev/null || sort))

    IFS=':' read -ra path_dirs <<< "${PATH:-}"
    for candidate in "${path_dirs[@]}"; do
        [[ -n "$candidate" ]] || continue
        if [[ -x "${candidate}/zig" ]]; then
            local resolved_dir=""
            resolved_dir="$(cd -- "$candidate" 2>/dev/null && pwd || true)"
            if [[ "${resolved_dir}/zig" != "${self_path}" ]]; then
                candidates+=("${candidate}/zig")
            fi
        fi
    done

    while IFS= read -r candidate; do
        [[ -x "$candidate" ]] && candidates+=("$candidate")
    done < <(find /tmp/zig "$HOME/.local/zig" -maxdepth 3 -type f -name zig 2>/dev/null | (sort -V 2>/dev/null || sort))

    if [[ ${#candidates[@]} -eq 0 ]]; then
        echo "error: real zig binary not found (set REAL_ZIG=/path/to/zig)" >&2
        exit 1
    fi

    printf '%s\n' "${candidates[0]}"
}

rewrite_triple() {
    local triple="$1"
    local is_armv5="$2"

    if [[ "$triple" == *linux.* ]]; then
        printf '%s\n' "$triple"
        return 0
    fi

    case "$triple" in
        x86_64-linux-musl)
            printf 'x86_64-linux.4.1.0...4.1.0-musl\n'
            ;;
        aarch64-linux-musl)
            printf 'aarch64-linux.4.1.0...4.1.0-musl\n'
            ;;
        arm-linux-musleabihf)
            printf 'arm-linux.4.1.0...4.1.0-musleabihf\n'
            ;;
        arm-linux-musleabi)
            if [[ "$is_armv5" == "1" ]]; then
                printf 'arm-linux.2.6.36...2.6.36-musleabi\n'
            else
                printf 'arm-linux.4.1.0...4.1.0-musleabi\n'
            fi
            ;;
        *)
            printf '%s\n' "$triple"
            ;;
    esac
}

real_zig="$(resolve_real_zig)"

is_armv5=0
for arg in "$@"; do
    case "$arg" in
        *arm926ej_s*|*arm1176jzf_s*)
            is_armv5=1
            break
            ;;
    esac
done

out_args=()
expect_target_next=0
for arg in "$@"; do
    if [[ "$expect_target_next" == "1" ]]; then
        out_args+=("$(rewrite_triple "$arg" "$is_armv5")")
        expect_target_next=0
        continue
    fi

    case "$arg" in
        -target)
            out_args+=("$arg")
            expect_target_next=1
            ;;
        -target=*)
            out_args+=("-target=$(rewrite_triple "${arg#-target=}" "$is_armv5")")
            ;;
        -Dtarget=*)
            out_args+=("-Dtarget=$(rewrite_triple "${arg#-Dtarget=}" "$is_armv5")")
            ;;
        *)
            out_args+=("$arg")
            ;;
    esac
done

if [[ "${ZIG_COMPAT_VERBOSE:-0}" == "1" ]]; then
    expect_log_next=0
    for arg in "${out_args[@]}"; do
        if [[ "$expect_log_next" == "1" ]]; then
            printf '[zig-linux-compat] effective target: %s\n' "$arg" >&2
            expect_log_next=0
            continue
        fi
        case "$arg" in
            -target)
                expect_log_next=1
                ;;
            -target=*)
                printf '[zig-linux-compat] effective target: %s\n' "${arg#-target=}" >&2
                ;;
            -Dtarget=*)
                printf '[zig-linux-compat] effective target: %s\n' "${arg#-Dtarget=}" >&2
                ;;
        esac
    done
fi

if [[ ${#out_args[@]} -eq 0 ]]; then
    exec "$real_zig"
else
    exec "$real_zig" "${out_args[@]}"
fi
