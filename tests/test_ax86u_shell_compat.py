"""RT-AX86U 388.11_0: no mktemp, cksum, od or shell command builtin."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_partial_reload import ROOT, SSCONFIG_PATH, extract_shell_function


SCRIPTS = ROOT / "fancyss/scripts"
HOT = SCRIPTS / "ss_shunt_hot_reload.sh"
STATUS = SCRIPTS / "ss_status_main.sh"


def functions(path, *names):
    return "\n".join(extract_shell_function(path, name) for name in names)


class FirmwareShellTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # CI selects the distro ash explicitly. Standalone BusyBox builds can
        # expose compiled applets even when they are absent from PATH.
        self.busybox = os.environ.get("FSS_TEST_BUSYBOX")
        for name in ("awk", "cat", "chmod", "cmp", "cp", "dirname", "grep", "jq", "mkdir",
                     "mv", "rm", "sed", "sort", "tail", "tr", "wc", "which"):
            executable = shutil.which(name)
            if name == "awk" and self.busybox:
                executable = self.busybox
            (self.bin / name).symlink_to(executable)
        md5 = self.bin / "md5sum"
        if shutil.which("md5sum"):
            md5.symlink_to(shutil.which("md5sum"))
        else:  # macOS has the same algorithm with a different command-line format.
            md5.write_text('#!/bin/sh\nd=$(/sbin/md5 -q) && printf "%s  -\\n" "$d"\n')
            md5.chmod(0o755)

    def run_script(self, script, **env):
        if self.busybox:
            shell = [self.busybox, "ash"]
            disable_command = "command() { return 127; }"
        else:
            shell = ["/bin/bash", "--noprofile", "--norc"]
            disable_command = "enable -n command"
        prelude = f"""
{disable_command}
if command -v jq >/dev/null 2>&1; then echo "unexpected command builtin" >&2; exit 80; fi
for name in mktemp cksum od; do
    if type "$name" >/dev/null 2>&1; then echo "unexpected firmware tool: $name" >&2; exit 80; fi
done
. '{SCRIPTS}/ss_node_common.sh'
"""
        result = subprocess.run(
            [*shell, "-ec", prelude + script],
            cwd=self.root, env=dict(PATH=str(self.bin), TMPDIR=str(self.root),
                                    KSROOT=str(self.root), LC_ALL="C", **env),
            text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout.strip()

    def test_private_workspaces_and_both_schema2_export_paths(self):
        self.run_script("""
umask 022
# Deterministic collision even on ash builds where RANDOM cannot be unset.
mkdir() {
    case "$1" in */work.*.0) /bin/mkdir "$1" 2>/dev/null; return 1 ;; esac
    /bin/mkdir "$@"
}
d=$(fss_mktemp_dir work)
case "$d" in *.1) ;; *) exit 1 ;; esac
printf '%s' "$d" > result
fss_detect_storage_schema() { echo 2; }
fss_pick_node_tool() { echo native; }
fss_run_node_tool() { echo "export ss_basic_name='Native Node'"; }
fss_export_current_node_env 24 name
[ "$ss_basic_name" = "Native Node" ]
fss_run_node_tool() { return 1; }
fss_v2_get_node_json_by_id() { echo '{"type":"4","name":"Fallback Node"}'; }
fss_export_current_node_env 24 name
[ "$ss_basic_name" = "Fallback Node" ]
unset ss_basic_name
fss_export_current_node_env 24
[ "$ss_basic_name" = "Fallback Node" ]
""")
        self.assertEqual(Path((self.root / "result").read_text()).stat().st_mode & 0o777, 0o700)
        self.assertEqual(list(self.root.glob("fss_*")), [])

    def test_identity_v1_matches_native_cksum(self):
        for value in ("", "abc", "本地节点\x1f东京", "a\nb\n", "x" * 1000):
            with self.subTest(value=value[:20]):
                crc = subprocess.check_output(["cksum"], input=value.encode()).split()[0]
                self.assertEqual(self.run_script('fss_identity_hash_v1 "$VALUE"', VALUE=value),
                                 f"{int(crc):08x}")

    def test_blank_baseline_rebuilt_once_and_retained_on_failure(self):
        code = functions(SSCONFIG_PATH, "fss_white_domain_sig", "reconcile_fail_closed_ipsets",
                         "fss_record_white_domain_baseline")
        self.run_script(code + """
FSS_WHITE_DOMAIN_FILE=$PWD/domains
FSS_WHITE_DOMAIN_SIG_FILE=$PWD/signature
FSS_STATIC_WHITE_SNAP_V4=$PWD/legacy4
FSS_STATIC_WHITE_SNAP_V6=$PWD/legacy6
printf '\\n' > signature
printf 'b.example\\na.example\\nb.example\\n' > domains
fss_collect_current_static_ip_lists() { for f in "$@"; do : > "$f"; done; }
fss_static_ignlist_v4() { :; }; fss_static_ignlist_v6() { :; }
add_current_server_ip_to_ignlist() { :; }
ipset() { cat > restore; [ "${FAIL_RESTORE:-0}" = 0 ]; }
FAIL_RESTORE=1
if reconcile_fail_closed_ipsets; then exit 1; fi
[ ! -s signature ] || [ -z "$(cat signature)" ]
FAIL_RESTORE=0
reconcile_fail_closed_ipsets
grep -qx 'swap white_list white_list_dyn_tmp' restore
grep -qx 'swap white_list6 white_list6_dyn_tmp' restore
cp signature good
printf 'a.example\\nb.example\\n' > domains
reconcile_fail_closed_ipsets
if grep -q 'swap white_list ' restore; then exit 1; fi
printf 'b.example\\n' > domains
reconcile_fail_closed_ipsets
grep -qx 'swap white_list white_list_dyn_tmp' restore
[ "$(cat good)" != "$(cat signature)" ]
cp signature good
md5sum() { return 1; }
if fss_record_white_domain_baseline domains; then exit 1; fi
if reconcile_fail_closed_ipsets; then exit 1; fi
[ "$(cat good)" = "$(cat signature)" ]
""")
        self.assertEqual((self.root / "signature").read_text().strip(),
                         "md5:" + hashlib.md5(b"b.example\n").hexdigest())

    def test_probe_and_shunt_can_distinguish_equal_length_changes(self):
        code = functions(STATUS, "failover_state_read", "failover_state_write",
                         "failover_record_probe_health")
        code += "\n" + functions(HOT, "hot_reload_apply", "hot_proxy_domains_sig")
        self.run_script(code + """
FAILOVER_STATE_FILE=$PWD/state; LOGFILE_F=$PWD/log; FAILOVER_RECOVERY_RESET=10
echo '50 2 0' > state
echo '10 204 OK' > log
failover_record_probe_health
failover_record_probe_health
read -r ts streak count sig < state; [ "$count" = 1 ]
echo '20 204 OK' > log
failover_record_probe_health
read -r ts streak count sig < state; [ "$count" = 2 ]

HOT_APPLIED_PROXY_DOMAINS_SIG_FILE=$PWD/hotsig
HOT_NEED_DNS_RELOAD_FILE=$PWD/dns
HOT_NEXT_FILE=$PWD/next
hot_log() { :; }; hot_require_ready() { :; }; hot_seed_exists() { :; }
hot_build_next_state() { :; }; hot_seed_matches_current() { :; }
fss_shunt_resolve_proxy_domain_file() { FSS_SHUNT_PROXY_DOMAIN_FILE_RESULT=$PWD/domains; }
hot_mark_need_dns_reload() { echo "$1" > dns; }
hot_mark_applied_state() { echo "$2" > hotsig; }
echo 'a.example' > domains; printf '\\n' > hotsig
hot_reload_apply; [ -s dns ]
hot_reload_apply; [ ! -f dns ]
echo 'b.example' > domains
hot_reload_apply; [ -s dns ]
""")

    def test_dns_comparison_and_hot_switch_use_private_staging(self):
        dns = functions(SSCONFIG_PATH, "dns_domain_lists_match_runtime").replace("/tmp/", "$PWD/")
        hot = functions(HOT, "hot_replace_default_routes")
        self.run_script(dns + "\n" + hot + """
echo a.example > white_list.txt; echo b.example > black_list.txt
refresh_domain_white_black_files() {
    cp white_list.txt "$1/white_list.txt"; cp black_list.txt "$1/black_list.txt"
}
dns_domain_lists_match_runtime
mkdir 'config dir'
XRAY_RUNTIME_CONF="$PWD/config dir/xray.json"
echo original > "$XRAY_RUNTIME_CONF"
hot_routes_match_runtime() {
    case "$1" in "$PWD/config dir/"*/previous.json) ;; *) return 1 ;; esac
    [ "$(cat "$1")" = original ] && echo inspected > marker
    return 1
}
if hot_replace_default_routes proxy24; then exit 1; fi
[ -f marker ] && [ "$(cat "$XRAY_RUNTIME_CONF")" = original ]
""")
        self.assertEqual(list(self.root.glob("fss_dns_lists.*")), [])
        self.assertEqual(list((self.root / "config dir").glob("*.switch.*")), [])

    def test_binary_function_discovery_and_ping_units(self):
        (self.bin / "flock").write_text("#!/bin/sh\nexit 1\n")
        (self.bin / "flock").chmod(0o755)
        code = functions(STATUS, "failover_is_ssconfig_locked")
        code += "\n" + functions(SSCONFIG_PATH, "check_internet4", "check_internet6")
        code += "\n" + functions(SCRIPTS / "ss_subscribe_profile_lib.sh", "subprof_log")
        out = self.run_script(code + """
[ "$(fss_pick_jq_bin)" = "$PATH/jq" ] || [ -x /koolshare/bin/jq ]
FAILOVER_LOCK_FILE=$PWD/lock; : > lock
failover_is_ssconfig_locked
profile_log() { echo called > logger; }; subprof_log test
[ -s logger ]
ping() { echo 'round-trip min/avg/max = 9.011/9.011/9.011 ms'; }
echo_date() { echo "$*"; }; check_internet4; check_internet6
""")
        self.assertEqual(out.count("9.011 ms"), 2)
        for script in (ROOT / "fancyss").rglob("*.sh"):
            self.assertNotIn("command -v ", script.read_text(), str(script))

    def test_bad_node_load_aborts_before_service_stop(self):
        self.run_script(functions(SSCONFIG_PATH, "apply_ss") + """
echo_date() { :; }; ss_basic_status=1
refresh_runtime_context() { :; }
fss_base_load_current_node_env() { return 1; }
ss_pre_stop() { : > stopped; exit 77; }
if apply_ss; then exit 1; fi
[ ! -e stopped ]
""")


if __name__ == "__main__":
    unittest.main()
