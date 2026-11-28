"""Release-review regressions: execute real reload/error paths with isolated IO."""
import base64
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_partial_reload import (
    ROOT, SSCONFIG_PATH, SS_CONFIG_PATH, eval_js_router, extract_shell_function,
)


def functions(path, *names):
    return "\n".join(extract_shell_function(path, name) for name in names)


def run(script):
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=10)


class ReloadReviewTests(unittest.TestCase):
    def test_process_options_are_not_save_only(self):
        self.assertEqual(eval_js_router({"ss_basic_interval": "5"})["action"], "reload_status")
        self.assertEqual(eval_js_router({"ss_basic_mcore": "1"})["action"], "start")

    def test_shared_list_changes_force_restart_even_with_other_scopes(self):
        for key in ("ss_wan_white_ip", "ss_wan_black_ip", "ss_wan_white_domain",
                    "ss_wan_black_domain", "ss_basic_proxy_newb"):
            with self.subTest(key=key):
                self.assertEqual(eval_js_router({key: "changed", "ss_basic_interval": "5"})["action"], "start")

    def test_real_fatal_paths_reach_dispatcher_fallback_without_disabling(self):
        # Unlike a stub returning 1, load_iptables/detect_running_status can exit
        # the whole ssconfig process through close_in_five.
        base = functions(ROOT / "fancyss/scripts/ss_base.sh", "close_in_five", "detect_running_status")
        backend = functions(SSCONFIG_PATH, "reload_scopes", "reload_iptables_only",
                            "load_iptables", "reload_dnsmasq_only")
        common = """
ss_basic_status=1
ss_basic_type=3
ss_basic_mode=2
ss_basic_udpoff=1
echo_date() { :; }
fss_socks5_open_bin() { echo xray; }
set_default() { :; }
fss_base_load_current_node_env() { :; }
refresh_runtime_context() { :; }
add_wan_bypass_ips_to_ignlist() { :; }
flush_iptables() { :; }
flush_ipset() { :; }
ipset() { :; }
iptables() { echo 'active nat rule'; }
_start_iptables() { return 1; }
create_dnsmasq_conf() { :; }
restart_dnsmasq() { detect_running_status dnsmasq; }
pidof() { :; }
usleep() { :; }
sleep() { :; }
dbus() { echo DBUS_MUTATED; }
disable_ss() { echo DISABLED >&2; }
unset_lock() { echo UNLOCKED >&2; }
"""
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "ssconfig.sh"
            child.write_text(base + "\n" + backend + "\n" + common + '\nreload_scopes "$2"\n')
            dispatcher = functions(SS_CONFIG_PATH, "reload_fancyss_scopes")
            for scope in ("iptables", "dnsmasq"):
                with self.subTest(scope=scope):
                    result = run(dispatcher + f"""
echo_date() {{ :; }}
start_fancyss() {{ echo FULL_RESTART; }}
sh() {{ shift; command bash '{child}' "$@"; }}
reload_fancyss_scopes reload_{scope}
""")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "FULL_RESTART")
                    self.assertNotIn("DISABLED", result.stderr)
                    self.assertIn("UNLOCKED", result.stderr)

    def domain_fixture(self, root):
        """Use the actual generator and comparison, redirecting only /tmp paths."""
        temp_helper = functions(ROOT / "fancyss/scripts/ss_node_common.sh", "fss_mktemp_dir")
        return f"TMPDIR='{root}'\n{temp_helper}\n" + functions(SSCONFIG_PATH, "refresh_domain_white_black_files", "dns_domain_lists_match_runtime").replace("/tmp/", str(root) + "/") + """
echo_date() { :; }
ss_basic_mode=2
dbus() { :; }
fss_b64_decode() { printf '%s' "$1" | base64_decode; }
base64_decode() { python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read()).decode())'; }
is_domain() { echo "$1"; }
"""

    def test_domain_membership_comparison_preserves_live_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = self.domain_fixture(root)
            old = base64.b64encode(b"old.example.com").decode()
            for kind in ("white", "black"):
                for change in ("removed", "added", "unchanged", "reordered", "missing"):
                    with self.subTest(kind=kind, change=change):
                        setup = prefix + f'\nss_wan_{kind}_domain={old}\nrefresh_domain_white_black_files "{root}"\n'
                        self.assertEqual(run(setup).returncode, 0)
                        if change == "missing":
                            (root / f"{kind}_list.txt").unlink()
                        if change == "reordered":
                            file = root / f"{kind}_list.txt"
                            file.write_text("\n".join(reversed(file.read_text().splitlines())) + "\n")
                        before = {p.name: p.read_bytes() for p in root.glob("*_list.txt")}
                        value = {"removed": "", "added": base64.b64encode(b"new.example.com").decode()}.get(change, old)
                        result = run(prefix + f'\nss_wan_{kind}_domain="{value}"\ndns_domain_lists_match_runtime\n')
                        self.assertEqual(result.returncode == 0, change in ("unchanged", "reordered"), result.stderr)
                        self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob("*_list.txt")})
                        self.assertEqual(list(root.glob("fss_dns_lists.*")), [])

    def test_dns_reload_checks_lists_and_ipv6_before_stopping_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("chnlist.txt", "gfwlist.txt"):
                (root / name).touch()
            backend = functions(SSCONFIG_PATH, "reload_dns_only", "check_internet6_pre").replace("/tmp/", str(root) + "/")
            for changed in (True, False):
                for ipv6 in ("disabled", "native"):
                    with self.subTest(changed=changed, ipv6=ipv6):
                        result = run(backend + f"""
ss_basic_status=1
unset INTERNET6
echo_date() {{ :; }}
fss_socks5_open_bin() {{ echo xray; }}
fss_base_load_current_node_env() {{ :; }}
refresh_runtime_context() {{ :; }}
fss_require_base_dns() {{ :; }}
init_current_node_server_state() {{ :; }}
sync_dns_ipv6_policy() {{ :; }}
nvram() {{ echo {ipv6}; }}
ip() {{ printf 'eth0:\\n    inet6 2001:db8::1/64 scope global\\n'; }}
dns_udp_relay_targets_match_runtime() {{ return 0; }}
dns_domain_lists_match_runtime() {{ return {int(changed)}; }}
stop_dns_process() {{ echo STOP_DNS; }}
create_dnsmasq_conf() {{ :; }}
restart_dnsmasq() {{ :; }}
start_dns_x() {{ echo INTERNET6=$INTERNET6; }}
reload_dns_only
""")
                        if changed:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertNotIn("STOP_DNS", result.stdout)
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertIn(f"INTERNET6={int(ipv6 != 'disabled')}", result.stdout)

    def test_shunt_proxy_domain_change_requires_full_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proxy_file = root / "proxy_domains.txt"
            proxy_file.write_text("old.shunt.example\n")
            prefix = self.domain_fixture(root) + f"""
ss_basic_mode=7
fss_shunt_resolve_proxy_domain_file() {{ FSS_SHUNT_PROXY_DOMAIN_FILE_RESULT='{proxy_file}'; }}
"""
            self.assertEqual(run(prefix + f'\nrefresh_domain_white_black_files "{root}"').returncode, 0)
            before = (root / "black_list.txt").read_bytes()
            proxy_file.write_text("new.shunt.example\n")
            result = run(prefix + "\ndns_domain_lists_match_runtime")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / "black_list.txt").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
