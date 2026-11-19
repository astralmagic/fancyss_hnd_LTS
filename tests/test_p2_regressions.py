import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def shell_function(path, name):
    match = re.search(
        r"^" + re.escape(name) + r"\(\)\s*\{.*?^\}",
        (ROOT / path).read_text(),
        re.M | re.S,
    )
    if not match:
        raise AssertionError(f"function not found: {path}:{name}")
    return match.group(0)


def shell_functions(path, *names):
    return "\n".join(shell_function(path, name) for name in names)


def run_shell(shell, script, **env):
    return subprocess.run(
        [shell, "-c", script],
        env=dict(os.environ, **env),
        capture_output=True,
        text=True,
        timeout=15,
    )


def write_patched_ss_base(dest_path):
    txt = (ROOT / "fancyss/scripts/ss_base.sh").read_text()
    txt = txt.replace("source $KSROOT/scripts/base.sh", ":")
    txt = txt.replace(
        "source $KSROOT/scripts/ss_node_common.sh",
        f'source "{ROOT / "fancyss/scripts/ss_node_common.sh"}"',
    )
    txt = txt.replace("source helper.sh", ":")
    p = Path(dest_path)
    p.write_text(txt)
    return p


def write_executable(path, content):
    p = Path(path)
    p.write_text(content)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def create_mock_dbus(bin_dir, db_file):
    write_executable(
        Path(bin_dir) / "dbus",
        f'''#!/usr/bin/env python3
import os, sys

db_path = {repr(str(db_file))}
log_path = os.environ.get("MOCK_DBUS_LOG", "")
os.makedirs(os.path.dirname(db_path), exist_ok=True)
data = {{}}
if os.path.exists(db_path):
    with open(db_path, "r") as f:
        for line in f:
            line = line.rstrip("\\n")
            if "=" in line:
                k, v = line.split("=", 1)
                data[k] = v

def save():
    with open(db_path, "w") as f:
        for k in sorted(data):
            f.write(f"{{k}}={{data[k]}}\\n")

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if log_path and cmd in ("set", "remove"):
    with open(log_path, "a") as lf:
        lf.write(" ".join(sys.argv[1:]) + "\\n")

if cmd == "get" and len(sys.argv) >= 3:
    val = data.get(sys.argv[2], "")
    if val:
        sys.stdout.write(val + "\\n")
elif cmd == "set" and len(sys.argv) >= 3:
    kv = sys.argv[2]
    if "=" in kv:
        k, v = kv.split("=", 1)
        data[k] = v
        save()
elif cmd == "remove" and len(sys.argv) >= 3:
    data.pop(sys.argv[2], None)
    save()
elif cmd == "list":
    prefix = sys.argv[2] if len(sys.argv) >= 3 else ""
    for k in sorted(data):
        if k.startswith(prefix):
            sys.stdout.write(f"{{k}}={{data[k]}}\\n")
elif cmd == "export":
    prefix = sys.argv[2] if len(sys.argv) >= 3 else ""
    for k in sorted(data):
        if k.startswith(prefix):
            v = data[k].replace("'", "'\\\\''")
            sys.stdout.write(f"export {{k}}='{{v}}'\\n")
''',
    )


class ReleaseFetchScriptTests(unittest.TestCase):
    def test_tuic_filters_dev_rc_and_v2_picks_v1_8_11(self):
        fixture = [
            {
                "tag_name": "v2.0.0-dev10",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "tuic-client-aarch64-linux-musl",
                        "browser_download_url": "https://example.invalid/dev10-arm64",
                        "digest": "sha256:" + ("a" * 64),
                    },
                    {
                        "name": "tuic-client-armv7-linux-musl",
                        "browser_download_url": "https://example.invalid/dev10-armv7",
                        "digest": "sha256:" + ("b" * 64),
                    },
                ],
            },
            {
                "tag_name": "v1.9.0-rc1",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "tuic-client-aarch64-linux-musl",
                        "browser_download_url": "https://example.invalid/rc1-arm64",
                        "digest": "sha256:" + ("c" * 64),
                    },
                    {
                        "name": "tuic-client-armv7-linux-musl",
                        "browser_download_url": "https://example.invalid/rc1-armv7",
                        "digest": "sha256:" + ("d" * 64),
                    },
                ],
            },
            {
                "tag_name": "v1.8.11",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "tuic-client-aarch64-linux-musl",
                        "browser_download_url": "https://example.invalid/1811-arm64",
                        "digest": "sha256:" + ("1" * 64),
                    },
                    {
                        "name": "tuic-client-armv7-linux-musl",
                        "browser_download_url": "https://example.invalid/1811-armv7",
                        "digest": "sha256:" + ("2" * 64),
                    },
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            json_file = Path(tmp) / "tuic_releases.json"
            json_file.write_text(json.dumps(fixture))
            env = dict(
                os.environ,
                RELEASE_JSON_FILE=str(json_file),
                DRY_RUN_RESOLVE="1",
                TARGET_TAG="",
            )
            res = subprocess.run(
                ["bash", str(ROOT / "binaries/tuic-client/get_latest.sh")],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("TAG=v1.8.11", res.stdout)
            self.assertIn("OUT_DIR=v1.8.11", res.stdout)
            self.assertIn("SHA256_ARM64=" + ("1" * 64), res.stdout)

            for bad_tag in ("v2.0.0-dev10", "v1.9.0-rc1"):
                bad_env = dict(env, TARGET_TAG=bad_tag)
                bad = subprocess.run(
                    ["bash", str(ROOT / "binaries/tuic-client/get_latest.sh")],
                    env=bad_env,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(bad.returncode, 0)

    def test_tuic_sha256_mismatch_aborts_before_upx(self):
        import hashlib

        fixture = [
            {
                "tag_name": "v1.8.11",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "tuic-client-aarch64-linux-musl",
                        "browser_download_url": "https://example.invalid/1811-arm64",
                        "digest": "sha256:" + ("f" * 64),
                    },
                    {
                        "name": "tuic-client-armv7-linux-musl",
                        "browser_download_url": "https://example.invalid/1811-armv7",
                        "digest": "sha256:" + hashlib.sha256(b"arm7-payload").hexdigest(),
                    },
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            work = t / "work"
            work.mkdir()
            script_copy = work / "get_latest.sh"
            script_copy.write_text((ROOT / "binaries/tuic-client/get_latest.sh").read_text())
            script_copy.chmod(0o755)
            json_file = t / "releases.json"
            json_file.write_text(json.dumps(fixture))
            bin_dir = t / "bin"
            bin_dir.mkdir()
            upx_log = t / "upx.log"
            write_executable(
                bin_dir / "curl",
                '''#!/usr/bin/env python3
import sys
out = None
for i, a in enumerate(sys.argv):
    if a == "-o" and i + 1 < len(sys.argv):
        out = sys.argv[i + 1]
if out:
    with open(out, "wb") as f:
        f.write(b"wrong-bytes")
''',
            )
            for upx_name in ("upx-5.0.2", "upx-4.2.4"):
                write_executable(
                    bin_dir / upx_name,
                    f'#!/bin/sh\necho "{upx_name} $*" >> "{upx_log}"\n',
                )
            write_executable(bin_dir / "md5sum", "#!/bin/sh\necho deadbeef\n")
            env = dict(
                os.environ,
                PATH=f"{bin_dir}:{os.environ['PATH']}",
                RELEASE_JSON_FILE=str(json_file),
                TARGET_TAG="",
            )
            res = subprocess.run(
                ["bash", str(script_copy)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("sha256 mismatch", res.stderr)
            self.assertFalse((work / "v1.8.11").exists())
            self.assertFalse(upx_log.exists())

    def test_smartdns_filters_rc_and_nightly_picks_release48_4(self):
        fixture = [
            {
                "tag_name": "Release48.5-RC1",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {"name": "smartdns-aarch64", "browser_download_url": "https://example.invalid/rc-64"},
                    {"name": "smartdns-arm", "browser_download_url": "https://example.invalid/rc-32"},
                ],
            },
            {
                "tag_name": "nightly",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {"name": "smartdns-aarch64", "browser_download_url": "https://example.invalid/n-64"},
                    {"name": "smartdns-arm", "browser_download_url": "https://example.invalid/n-32"},
                ],
            },
            {
                "tag_name": "Release48.4",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "smartdns-aarch64",
                        "browser_download_url": "https://example.invalid/484-64",
                        "digest": "sha256:" + ("3" * 64),
                    },
                    {
                        "name": "smartdns-arm",
                        "browser_download_url": "https://example.invalid/484-32",
                        "digest": "sha256:" + ("4" * 64),
                    },
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            json_file = Path(tmp) / "sd_releases.json"
            json_file.write_text(json.dumps(fixture))
            env = dict(
                os.environ,
                RELEASE_JSON_FILE=str(json_file),
                DRY_RUN_RESOLVE="1",
                TARGET_TAG="",
            )
            res = subprocess.run(
                ["bash", str(ROOT / "binaries/smartdns/get_smartdns.sh")],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("TAG=Release48.4", res.stdout)
            self.assertIn("OUT_DIR=v48.4", res.stdout)

            bad = subprocess.run(
                ["bash", str(ROOT / "binaries/smartdns/get_smartdns.sh")],
                env=dict(env, TARGET_TAG="Release48.5-RC1"),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(bad.returncode, 0)


class SubscriptionUADownloadTests(unittest.TestCase):
    def setUp(self):
        self.curl_link = Path("/tmp/curl-subscribe")
        self.update_link = Path("/tmp/curl-update")
        self.saved_links = {}
        for p in (self.curl_link, self.update_link):
            if p.is_symlink():
                self.saved_links[p] = os.readlink(p)
                p.unlink()
            elif p.exists():
                raise unittest.SkipTest(f"unexpected regular file at {p}")

    def tearDown(self):
        for p in (self.curl_link, self.update_link):
            if p.is_symlink() or p.exists():
                p.unlink()
        for p, target in self.saved_links.items():
            os.symlink(target, p)

    def test_curl_and_wget_preserve_ua_with_spaces_parens_and_globs(self):
        complex_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Clash.Meta/* [x;y]"
        secret_url = "https://sub.example.test/api?token=SECRET_XYZ_999"
        fns = shell_functions(
            "fancyss/scripts/ss_node_subscribe.sh",
            "download_by_curl",
            "download_by_wget",
        )
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bin_dir = t / "bin"
            bin_dir.mkdir()
            work_dir = t / "dir"
            work_dir.mkdir()
            (work_dir / "glob_trap.yaml").write_text("x")
            argv_log = t / "argv.jsonl"
            proxy_rule_log = t / "proxy_rule.log"
            write_executable(
                bin_dir / "mock_curl",
                f'''#!/usr/bin/env python3
import json, sys
with open({repr(str(argv_log))}, "a") as f:
    f.write(json.dumps({{"tool": "curl", "argv": sys.argv[1:]}}) + "\\n")
sys.stdout.write("mock-curl-payload")
''',
            )
            write_executable(
                bin_dir / "wget",
                f'''#!/usr/bin/env python3
import json, os, sys
with open({repr(str(argv_log))}, "a") as f:
    f.write(json.dumps({{"tool": "wget", "argv": sys.argv[1:]}}) + "\\n")
rc = int(os.environ.get("MOCK_WGET_RC", "0"))
if rc == 0:
    for i, a in enumerate(sys.argv):
        if a == "-O" and i + 1 < len(sys.argv):
            with open(sys.argv[i + 1], "w") as out:
                out.write("mock-wget-payload")
sys.exit(rc)
''',
            )
            shells = ["/bin/sh", "bash"]
            if Path("/bin/dash").exists():
                shells.append("/bin/dash")
            for shell in shells:
                argv_log.unlink(missing_ok=True)
                proxy_rule_log.unlink(missing_ok=True)
                for p in (self.curl_link, self.update_link):
                    if p.is_symlink() or p.exists():
                        p.unlink()
                os.symlink("/bin/echo", self.update_link)
                script = f'''
set -u
DIR="{work_dir}"
SUB_LINK_HASH="abcdef123456"
SUB_BY_PROXY="2"
CURR_NODE="1"
DOMAIN_NAME="sub.example.test"
MOCK_CURL_BIN="{bin_dir}/mock_curl"
echo_date() {{ printf '%s\\n' "$*"; }}
sub_header_file_path() {{ printf '%s/hdr_%s.txt' "$DIR" "$1"; }}
get_type_name() {{ printf 'vmess'; }}
sub_get_node_field_plain() {{ printf 'MockNode'; }}
fss_socks5_open_bin() {{ printf '%s' "${{MOCK_SOCKS5:-}}"; }}
get_ua() {{ printf '%s' "$MOCK_UA"; }}
proxy_rule() {{ printf '%s %s\\n' "$1" "$2" >> "{proxy_rule_log}"; return 0; }}
run() {{
  cmd="$1"; shift
  if [ "$cmd" = "/tmp/curl-subscribe" ]; then
    [ -L "/tmp/curl-subscribe" ] || return 99
    "$MOCK_CURL_BIN" "$@"
    return $?
  fi
  "$cmd" "$@"
}}
run5() {{ "$@"; }}
{fns}
cd "{work_dir}"
export MOCK_UA={repr(complex_ua)}
download_by_curl {repr(secret_url)}
SUB_BY_PROXY="0"
download_by_wget {repr(secret_url)}
export MOCK_UA=""
SUB_BY_PROXY="2"
download_by_curl "http://plain.example.test/sub"
download_by_wget "http://plain.example.test/sub"
'''
                res = run_shell(shell, script, PATH=f"{bin_dir}:{os.environ['PATH']}")
                self.assertEqual(res.returncode, 0, f"{shell} failed: {res.stderr}\n{res.stdout}")
                self.assertTrue(self.curl_link.is_symlink())
                self.assertEqual(os.readlink(self.curl_link), "/koolshare/bin/curl-fancyss")
                self.assertNotIn("SECRET_XYZ_999", res.stdout + res.stderr)

                calls = [json.loads(line) for line in argv_log.read_text().splitlines()]
                self.assertEqual(len(calls), 4)
                # curl with complex UA
                c0 = calls[0]["argv"]
                ua_idx = c0.index("--user-agent")
                self.assertEqual(c0[ua_idx + 1], complex_ua)
                # wget https with complex UA -> has --user-agent <UA> and --no-check-certificate
                w0 = calls[1]["argv"]
                w_idx = w0.index("--user-agent")
                self.assertEqual(w0[w_idx + 1], complex_ua)
                self.assertIn("--no-check-certificate", w0)
                # empty UA + http -> neither --user-agent nor --no-check-certificate
                self.assertNotIn("--user-agent", calls[2]["argv"])
                self.assertNotIn("--user-agent", calls[3]["argv"])
                self.assertNotIn("--no-check-certificate", calls[3]["argv"])

            # Verify wget proxy fallback preserves non-zero exit code across proxy_rule del
            proxy_rule_log.unlink(missing_ok=True)
            fail_script = f'''
DIR="{work_dir}"
SUB_LINK_HASH="abcdef123456"
SUB_BY_PROXY="1"
CURR_NODE="1"
DOMAIN_NAME="sub.example.test"
echo_date() {{ printf '%s\\n' "$*"; }}
sub_header_file_path() {{ printf '%s/hdr_%s.txt' "$DIR" "$1"; }}
get_type_name() {{ printf 'vmess'; }}
sub_get_node_field_plain() {{ printf 'MockNode'; }}
fss_socks5_open_bin() {{ printf 'xray'; }}
get_ua() {{ printf 'UA With Spaces'; }}
proxy_rule() {{ printf '%s %s\\n' "$1" "$2" >> "{proxy_rule_log}"; return 0; }}
run5() {{ "$@"; }}
{fns}
MOCK_WGET_RC=4 download_by_wget "https://sub.example.test/api"
rc=$?
exit $rc
'''
            fail_res = run_shell("/bin/sh", fail_script, PATH=f"{bin_dir}:{os.environ['PATH']}")
            self.assertEqual(fail_res.returncode, 4)
            self.assertEqual(
                proxy_rule_log.read_text().splitlines(),
                ["add sub.example.test", "del sub.example.test"],
            )


class NatAndWanRecoveryTests(unittest.TestCase):
    def test_ss_base_skip_dbus_export_and_readonly_acl_do_not_wipe_or_write_acl(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bin_dir = t / "bin"
            bin_dir.mkdir()
            db_file = t / "dbus.db"
            dbus_log = t / "dbus.log"
            db_file.write_text(
                "ss_basic_mode=2\n"
                "ss_acl_ip_1=192.168.1.50\n"
                "ss_acl_mac_1=aa:bb:cc:dd:ee:ff\n"
                "ss_acl_mode_1=1\n"
                "ss_acl_port_1=all\n"
            )
            create_mock_dbus(bin_dir, db_file)
            ss_base_file = write_patched_ss_base(t / "ss_base.sh")

            # 1. FSS_BASE_SKIP_DBUS_EXPORT=1 must not remove ss_acl_* rules
            res1 = run_shell(
                "bash",
                f'''
FSS_BASE_SKIP_SHUNT_SOURCE=1
FSS_BASE_SKIP_DBUS_EXPORT=1
FSS_BASE_EAGER_NODE_ENV=0
source "{ss_base_file}" || exit 1
''',
                PATH=f"{bin_dir}:{os.environ['PATH']}",
                MOCK_DBUS_LOG=str(dbus_log),
            )
            self.assertEqual(res1.returncode, 0, res1.stderr)
            self.assertFalse(dbus_log.exists(), dbus_log.read_text() if dbus_log.exists() else "")
            self.assertIn("ss_acl_ip_1=192.168.1.50", db_file.read_text())

            # 2. Normal source with unchanged MAC must be idempotent (0 dbus writes)
            res2 = run_shell(
                "bash",
                f'''
FSS_BASE_SKIP_SHUNT_SOURCE=1
FSS_BASE_EAGER_NODE_ENV=0
source "{ss_base_file}" || exit 1
''',
                PATH=f"{bin_dir}:{os.environ['PATH']}",
                MOCK_DBUS_LOG=str(dbus_log),
            )
            self.assertEqual(res2.returncode, 0, res2.stderr)
            self.assertFalse(dbus_log.exists(), dbus_log.read_text() if dbus_log.exists() else "")

            # 3. FSS_BASE_READONLY_ACL=1 must not modify incomplete ACL or missing MAC
            db_file.write_text(
                "ss_basic_mode=2\n"
                "ss_acl_ip_1=192.168.1.50\n"
                "ss_acl_mode_1=1\n"
                "ss_acl_port_1=all\n"
                "ss_acl_mode_2=1\n"
            )
            write_executable(
                bin_dir / "arp",
                "#!/bin/sh\necho '? (192.168.1.50) at 11:22:33:44:55:66 [ether] on br0'\n",
            )
            res3 = run_shell(
                "bash",
                f'''
FSS_BASE_SKIP_SHUNT_SOURCE=1
FSS_BASE_EAGER_NODE_ENV=0
FSS_BASE_READONLY_ACL=1
source "{ss_base_file}" || exit 1
''',
                PATH=f"{bin_dir}:{os.environ['PATH']}",
                MOCK_DBUS_LOG=str(dbus_log),
            )
            self.assertEqual(res3.returncode, 0, res3.stderr)
            self.assertFalse(dbus_log.exists(), dbus_log.read_text() if dbus_log.exists() else "")
            self.assertIn("ss_acl_mode_2=1", db_file.read_text())

    def test_apply_ss_by_nat_reloads_anytls_env_mangle0_ignlist_and_merlinbox(self):
        import base64

        node_json = json.dumps(
            {
                "name": "AnyNode",
                "type": "9",
                "mode": "1",
                "anytls_server": "any.example.com",
                "anytls_port": "443",
                "anytls_pass": "secret",
                "_source": "manual",
                "_profile_id": "",
                "_airport_identity": "local",
                "_source_scope": "local",
                "_source_url_hash": "",
                "_identity_primary": "41f8ed17",
                "_identity_secondary": "14dda5b2",
                "_identity": "41f8ed17_14dda5b2",
                "_identity_ver": "1",
            },
            separators=(",", ":"),
        )
        node_b64 = base64.b64encode(node_json.encode()).decode()
        fns = "\n".join(
            [
                shell_function("fancyss/ss/ssconfig.sh", name)
                for name in (
                    "refresh_runtime_context",
                    "add_wan_bypass_ips_to_ignlist",
                    "remove_anytls_merlinbox_compat_one",
                    "remove_anytls_merlinbox_compat",
                    "prepare_anytls_merlinbox_compat",
                    "ensure_chain",
                    "ensure_chain6",
                    "ensure_chain_with_cmd",
                    "append_if_not_exists",
                    "append_if_not_exists6",
                    "append_if_not_exists_with_cmd",
                    "insert_if_not_exists",
                    "apply_ss_by_nat",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bin_dir = t / "bin"
            bin_dir.mkdir()
            db_file = t / "dbus.db"
            dbus_log = t / "dbus.log"
            ipt_log = t / "ipt.log"
            ipset_log = t / "ipset.log"
            version_file = t / "version"
            version_file.write_text("3.5.31\n")
            db_file.write_text(
                "fss_data_schema=2\n"
                "fss_node_order=1\n"
                "fss_node_current=1\n"
                f"fss_node_1={node_b64}\n"
                "ss_basic_mode=1\n"
                "ss_basic_version_local=3.5.31\n"
                "ss_acl_default_udp=1\n"
            )
            create_mock_dbus(bin_dir, db_file)
            write_executable(
                bin_dir / "nvram",
                '''#!/bin/sh
case "$2" in
wan0_dns) echo "223.5.5.5 203.0.113.2" ;;
wan0_ipaddr) echo "203.0.113.55" ;;
lan_ipaddr) echo "192.168.50.1" ;;
esac
''',
            )
            write_executable(
                bin_dir / "ipset",
                f'''#!/bin/sh
echo "ipset $*" >> "{ipset_log}"
if [ "$1" = "-q" ] && [ "$2" = "test" ]; then
  return 1 2>/dev/null || exit 1
fi
exit 0
''',
            )
            write_executable(
                bin_dir / "iptables",
                f'''#!/bin/sh
echo "iptables $*" >> "{ipt_log}"
# Pretend MERLINKBOX_PROXY jump exists in mangle PREROUTING
if [ "$*" = "-t mangle -C PREROUTING -i br+ -j MERLINKBOX_PROXY" ]; then
  exit 0
fi
if [ "$*" = "-t mangle -L FSS_ANYTLS_MB4" ]; then
  exit 1
fi
if [ "$*" = "-t mangle -C MERLINKBOX_PROXY -j FSS_ANYTLS_MB4" ]; then
  exit 1
fi
exit 0
''',
            )
            write_executable(bin_dir / "ip6tables", "#!/bin/sh\nexit 1\n")

            script_file = write_patched_ss_base(t / "run.sh")
            test_driver = f'''
export FSS_BASE_EAGER_NODE_ENV=0
export FSS_BASE_SKIP_SHUNT_SOURCE=1
source "{script_file}" || exit 1
unset FSS_BASE_EAGER_NODE_ENV FSS_BASE_SKIP_SHUNT_SOURCE
THREAD=4
WAN_IPADDR=""
ISP_DNS1=""
ISP_DNS2=""
lan_ipaddr=""
ip_prefix_hex=""
ps() {{ :; }}
echo_date() {{ printf '%s\\n' "$*"; }}
flush_iptables() {{ remove_anytls_merlinbox_compat; }}
load_iptables() {{
  prepare_anytls_merlinbox_compat
  echo "STATE:type=${{ss_basic_type}}:mangle=${{mangle}}:hex=${{ip_prefix_hex}}"
}}
{fns.replace('/koolshare/ss/version', str(version_file))}
apply_ss_by_nat
'''
            res = run_shell(
                "bash",
                test_driver,
                PATH=f"{bin_dir}:{os.environ['PATH']}",
                MOCK_DBUS_LOG=str(dbus_log),
            )
            self.assertEqual(res.returncode, 0, res.stderr + "\n" + res.stdout)
            self.assertIn("STATE:type=9:mangle=0:hex=0xc0a83200/0xffffff00", res.stdout)
            self.assertIn("ipset -! add ignlist 223.5.5.5", ipset_log.read_text())
            self.assertIn("ipset -! add ignlist 203.0.113.2", ipset_log.read_text())
            self.assertIn("ipset -! add ignlist 203.0.113.55", ipset_log.read_text())
            self.assertIn(
                "iptables -t mangle -I MERLINKBOX_PROXY 1 -j FSS_ANYTLS_MB4",
                ipt_log.read_text(),
            )
            self.assertFalse(dbus_log.exists())

    def test_obfs_and_m99_postmount_fixes_present_and_idempotent(self):
        ssconfig = (ROOT / "fancyss/ss/ssconfig.sh").read_text()
        self.assertIn("M99shadowsocks.sh", ssconfig)
        self.assertNotIn('[ -z "{WEB_ACTION}" ]', ssconfig)
        self.assertNotIn("IFIP_DNS2", ssconfig)
        self.assertNotIn("killall -9 ${haveged_pid}", ssconfig)
        self.assertIn("kill -9 ${haveged_pid}", ssconfig)

        # Simulate M99shadowsocks.sh start with vs without running socks5 daemon
        case_match = re.search(r"case \$ACTION in\nstart\).*?unset_lock\n\t;;", ssconfig, re.S)
        self.assertIsNotNone(case_match)
        start_arm = case_match.group(0) + "\nesac\n"
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            runner = t / "runner.sh"
            runner.write_text(
                f'''#!/bin/sh
ACTION=start
ss_basic_enable=1
LOG_FILE="{t}/log.txt"
mkdir -p /tmp/upload 2>/dev/null || true
set_lock() {{ :; }}
unset_lock() {{ :; }}
logger() {{ printf 'LOG:%s\\n' "$*"; }}
start_ws() {{ printf 'CALLED:start_ws\\n'; }}
apply_ss() {{ printf 'CALLED:apply_ss\\n'; }}
fss_socks5_open_bin() {{ printf '%s' "${{MOCK_SOCKS5:-}}"; }}
{start_arm}
'''
            )
            runner.chmod(0o755)
            m99 = t / "M99shadowsocks.sh"
            os.symlink(runner, m99)

            already = subprocess.run([str(m99)], env=dict(os.environ, MOCK_SOCKS5="xray"), capture_output=True, text=True)
            self.assertEqual(already.returncode, 0, already.stderr)
            self.assertNotIn("CALLED:apply_ss", already.stdout)
            self.assertIn("跳过重复启动", already.stdout)

            if Path("/bin/dash").exists():
                already_dash = subprocess.run(
                    ["/bin/dash", str(m99)],
                    env=dict(os.environ, MOCK_SOCKS5="xray"),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(already_dash.returncode, 0, already_dash.stderr)
                self.assertIn("跳过重复启动", already_dash.stdout)

            cold = subprocess.run([str(m99)], env=dict(os.environ, MOCK_SOCKS5=""), capture_output=True, text=True)
            self.assertEqual(cold.returncode, 0, cold.stderr)
            self.assertIn("CALLED:apply_ss", cold.stdout)
            self.assertIn("post-mount补启动", cold.stdout)

            # Verify kill_process keeps start-obfs.sh during restart with ss obfs=tls, removes on stop
            kp = shell_function("fancyss/ss/ssconfig.sh", "kill_process")
            fake_obfs = t / "start-obfs.sh"
            fake_obfs.write_text("#!/bin/sh\n")
            kp_script = (
                kp.replace("/koolshare/ss/start-obfs.sh", str(fake_obfs))
                .replace("/proc/sys/net/ipv4/tcp_fastopen", str(t / "tcp_fastopen"))
            )
            res_keep = run_shell(
                "bash",
                kp_script + "\necho_date() { :; }\npidof() { return 1; }\nps() { :; }\nLINUX_VER=419\nACTION=restart\nss_basic_enable=1\nss_basic_mode=1\nss_basic_type=0\nss_basic_ss_obfs=tls\nkill_process\n",
            )
            self.assertEqual(res_keep.returncode, 0, res_keep.stderr)
            self.assertTrue(fake_obfs.exists())

            res_drop = run_shell(
                "bash",
                kp_script + "\necho_date() { :; }\npidof() { return 1; }\nps() { :; }\nLINUX_VER=419\nACTION=stop\nss_basic_enable=1\nss_basic_mode=1\nss_basic_type=0\nss_basic_ss_obfs=tls\nkill_process\n",
            )
            self.assertEqual(res_drop.returncode, 0, res_drop.stderr)
            self.assertFalse(fake_obfs.exists())


class NodeCommonAndStabilityTests(unittest.TestCase):
    def test_node_common_resolve_jq_fallback_booleans_escapes_and_anytls(self):
        import base64

        node_json = json.dumps(
            {
                "name": 'Node "Quoted" \\ Test',
                "type": "9",
                "mode": "1",
                "anytls_server": "anytls.example.net",
                "anytls_port": 8443,
                "anytls_pass": 'p"a\\ss',
                "anytls_sni": "",
                "anytls_ai": False,
                "xray_show": True,
                "xray_vcn": "cert.example.net",
                "_source": "manual",
            },
            separators=(",", ":"),
        )
        node_b64 = base64.b64encode(node_json.encode()).decode()
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bin_dir = t / "bin"
            bin_dir.mkdir()
            db_file = t / "dbus.db"
            db_file.write_text(f"fss_data_schema=2\nfss_node_order=1\nfss_node_1={node_b64}\n")
            create_mock_dbus(bin_dir, db_file)

            # Force jq fallback by disabling node-tool discovery inside subshell
            script = f'''
set -e
source "{ROOT / 'fancyss/scripts/ss_node_common.sh'}"
fss_pick_node_tool() {{ return 1; }}
fss_pick_jq_bin() {{ command -v jq; }}

# 1. Alias resolution
test "$(fss_resolve_node_field_name xray_svn)" = "xray_vcn"
test "$(fss_resolve_node_field_name hy2_svn)" = "hy2_vcn"
test "$(fss_resolve_node_field_name password 9)" = "anytls_pass"
test "$(fss_resolve_node_field_name server 6)" = "naive_server"
test "$(fss_resolve_node_field_name port 8)" = "hy2_port"
test "$(fss_resolve_node_field_name password 5)" = "trojan_uuid"

# 2. Empty string "" and escaped quotes/backslashes in fss_node_json_simple_value & fss_get_node_field_plain
sni="$(fss_get_node_field_plain 1 anytls_sni)"
test "$sni" = ""
pass_val="$(fss_get_node_field_plain 1 password)"
printf 'PASS=%s\\n' "$pass_val"
name_val="$(fss_get_node_field_plain 1 name)"
printf 'NAME=%s\\n' "$name_val"

# 3. 0-arg export & booleans via jq fallback
fss_export_current_node_env 1
printf 'AI=%s\\n' "${{ss_basic_anytls_ai:-UNSET}}"
printf 'SHOW=%s\\n' "${{ss_basic_xray_show:-UNSET}}"

# 4. Generic field export on type=9
unset ss_basic_server ss_basic_port ss_basic_password
fss_export_current_node_env 1 server port password
printf 'GEN=%s:%s:%s\\n' "${{ss_basic_server:-}}" "${{ss_basic_port:-}}" "${{ss_basic_password:-}}"

# 5. Server host/port & snapshot TSV for AnyTLS type=9
hp="$(fss_get_node_server_host_port 1 | tr '\\n' ':')"
printf 'HP=%s\\n' "$hp"
snap="$(fss_node_json_snapshot_tsv "$(fss_v2_get_node_json_by_id 1)")"
printf 'SNAP=%s\\n' "$snap"

# 6. fss_set_node_field_plain normalizes xray_svn -> xray_vcn
fss_set_node_field_plain 1 xray_svn updated.cert.example
printf 'VCN=%s\\n' "$(fss_get_node_field_plain 1 xray_vcn)"
'''
            res = run_shell("bash", script, PATH=f"{bin_dir}:{os.environ['PATH']}")
            self.assertEqual(res.returncode, 0, res.stderr + "\n" + res.stdout)
            self.assertIn('PASS=p"a\\ss', res.stdout)
            self.assertIn('NAME=Node "Quoted" \\ Test', res.stdout)
            self.assertIn("AI=0", res.stdout)
            self.assertIn("SHOW=1", res.stdout)
            self.assertIn('GEN=anytls.example.net:8443:p"a\\ss', res.stdout)
            self.assertIn("HP=anytls.example.net:8443:", res.stdout)
            self.assertIn("anytls.example.net", res.stdout)
            self.assertIn("VCN=updated.cert.example", res.stdout)

    def test_allow_insecure_warnings_ui_mangle0_and_haveged_guards(self):
        warn_fn = shell_function("fancyss/ss/ssconfig.sh", "warn_xray_allow_insecure_removed")
        wt_fn = shell_function("fancyss/scripts/ss_webtest_gen.sh", "wt_warn_xray_allow_insecure")
        res = run_shell(
            "bash",
            f'''
echo_date() {{ printf '%s\\n' "$*"; }}
{warn_fn}
{wt_fn}
echo "=== CASE1 ==="
warn_xray_allow_insecure_removed "VLESS" "1" ""
echo "=== CASE2 ==="
warn_xray_allow_insecure_removed "VLESS" "1" "deadbeef"
echo "=== CASE3 ==="
warn_xray_allow_insecure_removed "VLESS" "0" ""
echo "=== CASE4 ==="
wt_warn_xray_allow_insecure "7" "Trojan" "1" "" 2>&1
''',
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("pinnedPeerCertSha256", res.stdout)
        self.assertIn("allowInsecure", res.stdout)
        self.assertIn("按证书指纹校验", res.stdout)
        self.assertIn("节点#7(Trojan)", res.stdout)

        asp = (ROOT / "fancyss/webs/Module_shadowsocks.asp").read_text()
        self.assertIn("已移除 allowInsecure", asp)
        for field_id in (
            "ss_node_table_xray_pcs",
            "ss_node_table_xray_vcn",
            "ss_node_table_trojan_pcs",
            "ss_node_table_trojan_vcn",
            "ss_node_table_hy2_pcs",
            "ss_node_table_hy2_vcn",
            "ss_basic_xray_pcs",
            "ss_basic_xray_vcn",
            "ss_basic_trojan_pcs",
            "ss_basic_trojan_vcn",
            "ss_basic_hy2_pcs",
            "ss_basic_hy2_vcn",
        ):
            m = re.search(rf"id:'{field_id}'.*", asp)
            self.assertIsNotNone(m, field_id)
            self.assertNotIn("_ai_off", m.group(0), m.group(0))

        # Verify mangle=0 skips TPROXY/table 310/OUTPUT fwmark 0x7
        ipt_fn = shell_functions(
            "fancyss/ss/ssconfig.sh",
            "remove_anytls_merlinbox_compat_one",
            "remove_anytls_merlinbox_compat",
            "prepare_anytls_merlinbox_compat",
            "ensure_chain",
            "ensure_chain_with_cmd",
            "append_if_not_exists",
            "append_if_not_exists_with_cmd",
            "insert_if_not_exists",
            "get_shunt_ingress_mode",
            "init_shunt_chain_v4",
            "_start_iptables",
        )
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bin_dir = t / "bin"
            bin_dir.mkdir()
            calls = t / "calls.log"
            write_executable(
                bin_dir / "iptables",
                f'''#!/bin/sh
echo "iptables $*" >> "{calls}"
case "$*" in
  *" -C "*) exit 1 ;;
  "-nvL"*) echo "1 dummy"; exit 0 ;;
esac
exit 0
''',
            )
            write_executable(bin_dir / "ip", f'#!/bin/sh\necho "ip $*" >> "{calls}"\n')
            res_m0 = run_shell(
                "bash",
                f'''
mangle=0
ss_basic_type=9
ss_basic_mode=1
ss_basic_dns_hijack=0
ss_basic_proxy_ipv6=0
ss_basic_shunt_ingress_mode=2
ip_prefix_hex="0xc0a80100/0xffffff00"
acl_nu=""
echo_date() {{ :; }}
load_tproxy() {{ echo "load_tproxy" >> "{calls}"; }}
ipv6_proxy_enabled() {{ return 1; }}
lan_access_control() {{ :; }}
apply_quic_block() {{ :; }}
dns_hijack_control() {{ :; }}
resolve_acl_default_mode() {{ echo "1"; }}
resolve_acl_ports() {{ echo "all"; }}
resolve_acl_default_ports_raw() {{ echo "all"; }}
get_acl_udp_flag() {{ echo "0"; }}
get_acl_quic_flag() {{ echo "0"; }}
apply_acl_udp_rule() {{ :; }}
get_action_chain() {{ echo "SHADOWSOCKS_GFW"; }}
factor() {{ :; }}
ifconfig() {{ echo "br0"; }}
{ipt_fn}
_start_iptables
''',
                PATH=f"{bin_dir}:{os.environ['PATH']}",
            )
            self.assertEqual(res_m0.returncode, 0, res_m0.stderr)
            logged = calls.read_text()
            self.assertNotIn("load_tproxy", logged)
            self.assertNotIn("table 310", logged)
            self.assertNotIn("TPROXY", logged)
            self.assertNotIn("--set-mark 0x7", logged)

        # Verify BE86U/5.4 Linux_VER does not start haveged, while 41 does
        ssconfig = (ROOT / "fancyss/ss/ssconfig.sh").read_text()
        self.assertIn("26|41)", ssconfig)
        triage = ROOT / "doc/analysis/p2_hnd_v8_stability_triage.md"
        self.assertTrue(triage.is_file())
        triage_txt = triage.read_text()
        for issue_ref in ("#33899", "#33904", "#33936", "#33943"):
            self.assertIn(issue_ref, triage_txt)


class ModelInstallAndCommunityTests(unittest.TestCase):
    def setUp(self):
        self.upload_dir = Path("/tmp/upload")
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.test_upload = self.upload_dir / "ss_yaml_upload.yaml"
        self.other_upload = self.upload_dir / "other_unrelated.yaml"
        self.test_upload.unlink(missing_ok=True)
        self.other_upload.unlink(missing_ok=True)

    def tearDown(self):
        self.test_upload.unlink(missing_ok=True)
        self.other_upload.unlink(missing_ok=True)

    def test_install_sh_tuf_be6500_all_branches_and_data_offload(self):
        install_txt = (ROOT / "fancyss/install.sh").read_text()
        self.assertEqual(install_txt.count('"TUF_6500"|"TUF-BE6500"|"TUF_BE6500")'), 7)
        self.assertIn("tuic-client", install_txt)
        self.assertIn("M99shadowsocks.sh", install_txt)

        pt_raw = shell_function("fancyss/install.sh", "platform_test")
        pt_fn, n_subs = re.subn(
            r'\tif \[ -d "/koolshare".*?PKG_TYPE=\$\(get_pkg_field_from_file[^\n]+\n',
            "",
            pt_raw,
            count=1,
            flags=re.S,
        )
        self.assertEqual(n_subs, 1)
        for model in ("TUF-BE6500", "TUF_BE6500", "TUF_6500"):
            with self.subTest(model=model):
                ok = run_shell(
                    "bash",
                    f'''
KEL_VERS="5.4.213"
LINUX_VER="54"
ROT_ARCH="aarch64"
PKG_ARCH="ipq64"
PKG_TYPE="full"
MODEL="{model}"
echo_date() {{ printf '%s\\n' "$*"; }}
exit_install() {{ exit "${{1:-1}}"; }}
{pt_fn}
platform_test
''',
                )
                self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

                wrong_arch = run_shell(
                    "bash",
                    f'''
KEL_VERS="5.4.213"
LINUX_VER="54"
ROT_ARCH="aarch64"
PKG_ARCH="hnd_v8"
PKG_TYPE="full"
MODEL="{model}"
echo_date() {{ printf '%s\\n' "$*"; }}
exit_install() {{ exit "${{1:-1}}"; }}
{pt_fn}
platform_test
''',
                )
                self.assertEqual(wrong_arch.returncode, 1)
                self.assertIn("fancyss_ipq64", wrong_arch.stdout)

    def test_merlinbox_compat_noop_and_anytls_toggle(self):
        fns = shell_functions(
            "fancyss/ss/ssconfig.sh",
            "ensure_chain_with_cmd",
            "remove_anytls_merlinbox_compat_one",
            "remove_anytls_merlinbox_compat",
            "prepare_anytls_merlinbox_compat",
        )
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bin_dir = t / "bin"
            bin_dir.mkdir()
            ipt_log = t / "ipt.log"
            write_executable(
                bin_dir / "iptables",
                f'''#!/bin/sh
echo "iptables $*" >> "{ipt_log}"
if [ "${{MB_PRESENT:-0}}" = "1" ] && [ "$*" = "-t mangle -C PREROUTING -i br+ -j MERLINKBOX_PROXY" ]; then
  exit 0
fi
if [ "${{MB_BYPASS_PRESENT:-0}}" = "1" ] && [ "$*" = "-t mangle -C MERLINKBOX_PROXY -j FSS_ANYTLS_MB4" ]; then
  exit 0
fi
exit 1
''',
            )
            write_executable(bin_dir / "ip6tables", f'#!/bin/sh\necho "ip6tables $*" >> "{ipt_log}"\nexit 1\n')

            # Case 1: absent MERLINKBOX_PROXY -> no-op
            res_noop = run_shell(
                "bash",
                f'ss_basic_type=9\necho_date() {{ printf "%s\\n" "$*"; }}\n{fns}\nprepare_anytls_merlinbox_compat\n',
                PATH=f"{bin_dir}:{os.environ['PATH']}",
                MB_PRESENT="0",
            )
            self.assertEqual(res_noop.returncode, 0, res_noop.stderr)
            self.assertNotIn("FSS_ANYTLS_MB4", res_noop.stdout)

            # Case 2: MERLINKBOX_PROXY present + type=9 -> configure chain; type=4 -> remove
            ipt_log.unlink(missing_ok=True)
            res_on = run_shell(
                "bash",
                f'''
ss_basic_type=9
echo_date() {{ printf '%s\\n' "$*"; }}
{fns}
mb_chain=0
mb_jump=0
iptables() {{
  echo "iptables $*" >> "{ipt_log}"
  case "$*" in
    "-t mangle -C PREROUTING -i br+ -j MERLINKBOX_PROXY") return 0 ;;
    "-t mangle -L FSS_ANYTLS_MB4") [ "$mb_chain" = "1" ] && return 0 || return 1 ;;
    "-t mangle -N FSS_ANYTLS_MB4") mb_chain=1; return 0 ;;
    "-t mangle -X FSS_ANYTLS_MB4") mb_chain=0; return 0 ;;
    "-t mangle -C MERLINKBOX_PROXY -j FSS_ANYTLS_MB4") [ "$mb_jump" = "1" ] && return 0 || return 1 ;;
    "-t mangle -I MERLINKBOX_PROXY 1 -j FSS_ANYTLS_MB4") mb_jump=1; return 0 ;;
    "-t mangle -D MERLINKBOX_PROXY -j FSS_ANYTLS_MB4") mb_jump=0; return 0 ;;
    *) return 0 ;;
  esac
}}
ip6tables() {{ return 1; }}
prepare_anytls_merlinbox_compat
ss_basic_type=4
prepare_anytls_merlinbox_compat
''',
                PATH=f"{bin_dir}:{os.environ['PATH']}",
            )
            self.assertEqual(res_on.returncode, 0, res_on.stderr)
            log_text = ipt_log.read_text()
            self.assertIn("iptables -t mangle -A FSS_ANYTLS_MB4 -p tcp -j ACCEPT", log_text)
            self.assertIn("iptables -t mangle -I MERLINKBOX_PROXY 1 -j FSS_ANYTLS_MB4", log_text)
            self.assertIn("iptables -t mangle -D MERLINKBOX_PROXY -j FSS_ANYTLS_MB4", log_text)

    def test_hardened_local_yaml_import(self):
        asp = (ROOT / "fancyss/webs/Module_shadowsocks.asp").read_text()
        self.assertIn("ss_yaml_upload.yaml", asp)
        self.assertIn("4194304", asp)

        profile_lib = (ROOT / "fancyss/scripts/ss_subscribe_profile_lib.sh").read_text()
        fns = shell_functions(
            "fancyss/scripts/ss_node_subscribe.sh",
            "subprof_create_local_import_profile",
            "start_yaml_file_import",
        )
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bin_dir = t / "bin"
            bin_dir.mkdir()
            db_file = t / "dbus.db"
            db_file.write_text("ss_basic_pkg_type=full\nfss_data_schema=2\n")
            create_mock_dbus(bin_dir, db_file)

            common_prefix = f'''
DIR="{t / 'work'}"
SUB_ACTIVE_PROFILE_ID=""
SUB_STORAGE_SCHEMA=2
echo_date() {{ printf '%s\\n' "$*"; }}
pick_sub_tool() {{ echo "{bin_dir / 'sub-tool'}"; }}
sub_try_parse_uri_lines_with_tool() {{
  printf '{{"name":"Y1","type":"0"}}\\n' > "$2"
  return 0
}}
sub_filter_offline_duplicate_nodes() {{ :; }}
json2skipd() {{ test "${{MOCK_WRITE_OK:-1}}" = "1"; }}
sub_after_nodes_written() {{ :; }}
{profile_lib.split('# main')[0] if '# main' in profile_lib else profile_lib}
{fns}
'''
            write_executable(bin_dir / "sub-tool", '#!/bin/sh\necho \'{"kind":"clash-yaml"}\'\n')

            # 1. Symlink attack rejected
            target_secret = t / "secret.txt"
            target_secret.write_text("proxies:\n  - {name: secret}\n")
            os.symlink(target_secret, self.test_upload)
            res_sym = run_shell("bash", common_prefix + "\nstart_yaml_file_import\n", PATH=f"{bin_dir}:{os.environ['PATH']}")
            self.assertNotEqual(res_sym.returncode, 0)
            self.assertIn("符号链接", res_sym.stdout)
            self.assertFalse(self.test_upload.exists() or self.test_upload.is_symlink())

            # 2. Unrelated *.yaml file is NOT picked up when ss_yaml_upload.yaml is absent
            self.other_upload.write_text("proxies:\n  - {name: wrong}\n")
            res_other = run_shell("bash", common_prefix + "\nstart_yaml_file_import\n", PATH=f"{bin_dir}:{os.environ['PATH']}")
            self.assertNotEqual(res_other.returncode, 0)
            self.assertIn("未找到上传的 YAML 文件", res_other.stdout)

            # 3. > 4 MiB file rejected and removed
            self.test_upload.write_bytes(b"a" * (4 * 1024 * 1024 + 5))
            res_big = run_shell("bash", common_prefix + "\nstart_yaml_file_import\n", PATH=f"{bin_dir}:{os.environ['PATH']}")
            self.assertNotEqual(res_big.returncode, 0)
            self.assertIn("超过 4MB", res_big.stdout)
            self.assertFalse(self.test_upload.exists())

            # 4. Valid YAML import creates disabled profile + sets last_ok_ts + deletes upload
            self.test_upload.write_text("proxies:\n  - {name: testnode, type: ss}\n")
            res_ok = run_shell("bash", common_prefix + "\nstart_yaml_file_import\n", PATH=f"{bin_dir}:{os.environ['PATH']}")
            self.assertEqual(res_ok.returncode, 0, res_ok.stdout + res_ok.stderr)
            self.assertFalse(self.test_upload.exists())
            db_txt = db_file.read_text()
            self.assertIn("ss_subprof_ids=", db_txt)
            # Check profile enabled == false and last_ok_ts > 0
            state_lines = [l for l in db_txt.splitlines() if l.startswith("ss_subprof_") and "_state=" in l]
            prof_lines = [l for l in db_txt.splitlines() if l.startswith("ss_subprof_") and not l.startswith("ss_subprof_ids=") and "_state=" not in l and "=" in l]
            self.assertEqual(len(prof_lines), 1)
            self.assertEqual(len(state_lines), 1)
            import base64
            prof_json = json.loads(base64.b64decode(prof_lines[0].split("=", 1)[1]).decode())
            state_json = json.loads(base64.b64decode(state_lines[0].split("=", 1)[1]).decode())
            self.assertFalse(prof_json["enabled"])
            self.assertFalse(prof_json["schedule"]["enabled"])
            self.assertGreater(state_json["last_ok_ts"], 0)

            # 5. json2skipd failure rolls back newly created local import profile and cleans upload
            self.test_upload.write_text("proxies:\n  - {name: failnode, type: ss}\n")
            res_fail = run_shell(
                "bash",
                common_prefix + "\nstart_yaml_file_import\n",
                PATH=f"{bin_dir}:{os.environ['PATH']}",
                MOCK_WRITE_OK="0",
            )
            self.assertNotEqual(res_fail.returncode, 0)
            self.assertFalse(self.test_upload.exists())
            db_after = db_file.read_text()
            ids_lines = [l for l in db_after.splitlines() if l.startswith("ss_subprof_ids=")]
            self.assertEqual(len(ids_lines), 1)
            self.assertEqual(ids_lines[0].split("=", 1)[1], prof_json["id"])


if __name__ == "__main__":
    unittest.main()
