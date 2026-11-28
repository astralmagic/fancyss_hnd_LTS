#!/usr/bin/env bash
# Run the HND ARM binaries under Linux user-mode QEMU against loopback services.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
dns_pid=
upstream_pid=
cleanup() {
    local status=$?
    for pid in "$dns_pid" "$upstream_pid"; do
        if [[ -n $pid ]]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" || true
        fi
    done
    if (( status != 0 )); then cat "$work"/*.log >&2 || true; fi
    rm -rf "$work"
}
trap cleanup EXIT

upx=${1:-upx}
xray_ref=$(cat "$root/binaries/xray/latest_2.txt")
smartdns_ref=$(cat "$root/binaries/smartdns/latest.txt")
go_version=$(sed -n 's/^GO_VERSION="\([^"]*\)"$/\1/p' "$root/scripts/ensure_local_go.sh")
test -n "$go_version"
(cd "$root/binaries/xray/$xray_ref" && md5sum -c md5sum.txt)
(cd "$root/binaries/smartdns/$smartdns_ref" && md5sum -c md5sum.txt)
cd "$work"

# Give each transport a different answer so a fallback to plain DNS fails the test.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj '/CN=localhost' \
    -addext 'subjectAltName=IP:127.0.0.1,DNS:localhost' -keyout key.pem -out cert.pem 2>openssl.log
cat > dnsdist.conf <<CONF
setLocal("127.0.0.1:15453")
setSecurityPollSuffix("")
addDOHLocal("127.0.0.1:15443", "$work/cert.pem", "$work/key.pem", "/dns-query")
addAction(HTTPPathRule("/dns-query"), SpoofAction("192.0.2.22"))
addAction(AllRule(), SpoofAction("192.0.2.11"))
CONF
dnsdist --supervised --disable-syslog -C "$work/dnsdist.conf" >dnsdist.log 2>&1 &
upstream_pid=$!

cat > smartdns.conf <<CONF
bind 127.0.0.1:16053
speed-check-mode none
cache-size 0
log-file $work/smartdns.log
ca-file $work/cert.pem
domain-rules /outside.example/ -n airport_fixture
CONF
# Use the plugin's upstream generators, including its subscription DoH format.
for fn in smartdns_format_addr smartdns_server_flags smartdns_append_server_line \
          smartdns_airport_group_name smartdns_append_airport_node_servers_by_identity; do
    sed -n "/^${fn}() {/,/^}/p" "$root/fancyss/ss/ssconfig.sh" >> generators.sh
done
source ./generators.sh
fss_airport_runtime_iter_dns_items_tsv_by_identity() {
    printf 'https\037https://127.0.0.1:15443/dns-query\n'
}
smartdns_append_server_line smartdns.conf udp 127.0.0.1 15453 '' '' "$(smartdns_server_flags 1 chn_group)" 0
smartdns_append_airport_node_servers_by_identity smartdns.conf fixture

wait_for_answer() {
    local name=$1 expected=$2
    for _ in {1..20}; do
        kill -0 "$upstream_pid" && kill -0 "$dns_pid" || return 1
        if [[ $(dig +short +tries=1 +time=1 @127.0.0.1 -p 16053 "$name" A) == "$expected" ]]; then
            return 0
        fi
        sleep 0.25
    done
    echo "Expected $name to resolve to $expected" >&2
    return 1
}

cat > xray.json <<'JSON'
{"inbounds":[{"listen":"127.0.0.1","port":15480,"protocol":"socks","settings":{"udp":true}}],"outbounds":[{"protocol":"freedom"}]}
JSON
for arch in armv7 arm64; do
    emulator=qemu-arm
    goarch=arm
    if [[ $arch == arm64 ]]; then emulator=qemu-aarch64; goarch=arm64; fi
    "$upx" -d -o "xray_$arch" "$root/binaries/xray/$xray_ref/xray_$arch"
    "$upx" -d -o "smartdns_$arch" "$root/binaries/smartdns/$smartdns_ref/smartdns_$arch"
    chmod +x "xray_$arch" "smartdns_$arch"
    "$emulator" "./smartdns_$arch" -v 2>&1 | grep -F "(Release${smartdns_ref#v})"
    for start in 1 2; do
        "$emulator" "./smartdns_$arch" -f -c "$work/smartdns.conf" -p "$work/smartdns.pid" >>console.log 2>&1 &
        dns_pid=$!
        wait_for_answer inside.example 192.0.2.11
        wait_for_answer outside.example 192.0.2.22
        echo "SmartDNS $arch start $start passed UDP and DoH routing"
        kill "$dns_pid"
        wait "$dns_pid" || [[ $? == 143 ]]
        dns_pid=
    done
    version=$("$emulator" "./xray_$arch" version)
    xray_tag=${xray_ref%-*}
    grep -F "Xray ${xray_tag#v} " <<< "$version"
    grep -F " ${xray_ref##*-} (go$go_version linux/$goarch)" <<< "$version"
    "$emulator" "./xray_$arch" run -test -c xray.json
done
