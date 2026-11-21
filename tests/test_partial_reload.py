from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASP_PATH = ROOT / "fancyss/webs/Module_shadowsocks.asp"
SS_CONFIG_PATH = ROOT / "fancyss/scripts/ss_config.sh"
SSCONFIG_PATH = ROOT / "fancyss/ss/ssconfig.sh"
WEBSOCKET_PATH = ROOT / "fancyss/ss/websocket"


def extract_shell_function(path: Path, name: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"{name}() {{") or line.startswith(f"{name}(){{"):
            start = idx
            break
    if start is None:
        raise AssertionError(f"Function {name} not found in {path}")
    out = []
    for line in lines[start:]:
        out.append(line)
        if line == "}":
            return "\n".join(out)
    raise AssertionError(f"Function {name} did not terminate in {path}")


def extract_js_router_block() -> str:
    text = ASP_PATH.read_text(encoding="utf-8")
    start = text.index("var PARTIAL_RELOAD_KEY_SCOPES = {")
    end = text.index("var realtimeLogStartTimer = null;", start)
    return text[start:end]


def eval_js_router(post_dbus: dict, dom: dict | None = None, db_ss: dict | None = None) -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is not installed")
    js_block = extract_js_router_block()
    dom_map = {
        "ss_basic_enable": {"checked": True, "value": "1"},
        "ss_basic_mode": {"value": "2"},
        "ss_basic_shunt_hot_reload": {"checked": True, "value": "1"},
        "ss_basic_block_webrtc_action": {"value": "reject"},
    }
    if dom:
        dom_map.update(dom)
    store = {
        "ss_basic_status": "1",
        "ss_basic_mode": "2",
        "ss_basic_block_webrtc_action": "reject",
    }
    if db_ss:
        store.update(db_ss)

    harness = f"""
const domMap = {json.dumps(dom_map)};
const db_ss = {json.dumps(store)};
function E(id) {{ return domMap[id] || null; }}
{js_block}
const postDbus = {json.dumps(post_dbus)};
const result = {{
    action: resolve_config_submit_action(postDbus),
    shuntHot: should_use_shunt_hot_reload(postDbus)
}};
process.stdout.write(JSON.stringify(result));
"""
    proc = subprocess.run([node, "-e", harness], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


class FrontendPartialReloadRouterTests(unittest.TestCase):
    def test_empty_diff_falls_back_to_full_restart(self):
        res = eval_js_router({})
        self.assertEqual(res["action"], "start")
        self.assertFalse(res["shuntHot"])

    def test_stopped_service_falls_back_to_full_restart(self):
        res = eval_js_router({"ss_basic_tablet": "1"}, db_ss={"ss_basic_status": "0"})
        self.assertEqual(res["action"], "start")

    def test_l0_save_only_keys(self):
        res = eval_js_router({
            "ss_basic_tablet": "1",
            "ss_basic_exclude": "HK",
            "ss_basic_lt_web_time": "5",
        })
        self.assertEqual(res["action"], "save_only")

    def test_l1_cron_only(self):
        res = eval_js_router({
            "ss_basic_rule_update": "1",
            "ss_basic_rule_update_time": "4",
            "ss_reboot_check": "1",
        })
        self.assertEqual(res["action"], "reload_cron")

    def test_l1_status_only(self):
        res = eval_js_router({
            "ss_basic_furl": "http://example.com/204",
            "ss_failover_enable": "1",
        })
        self.assertEqual(res["action"], "reload_status")

    def test_l2_acl_and_iptables_only(self):
        res = eval_js_router({
            "ss_acl_default_mode": "0",
            "ss_acl_mode_1": "2",
            "ss_acl_udp_1": "1",
            "ss_basic_dns_hijack": "1",
            "ss_basic_block_quic": "1",
        })
        self.assertEqual(res["action"], "reload_iptables")

    def test_webrtc_reject_uses_iptables_but_proxy_falls_back(self):
        res_reject = eval_js_router(
            {"ss_basic_block_webrtc": "1", "ss_basic_block_webrtc_ports": "all"},
            dom={"ss_basic_block_webrtc_action": {"value": "reject"}},
            db_ss={"ss_basic_block_webrtc_action": "reject"},
        )
        self.assertEqual(res_reject["action"], "reload_iptables")

        res_to_proxy = eval_js_router(
            {"ss_basic_block_webrtc_action": "proxy"},
            dom={"ss_basic_block_webrtc_action": {"value": "proxy"}},
            db_ss={"ss_basic_block_webrtc_action": "reject"},
        )
        self.assertEqual(res_to_proxy["action"], "start")

        res_from_proxy = eval_js_router(
            {"ss_basic_block_webrtc": "0"},
            dom={"ss_basic_block_webrtc_action": {"value": "reject"}},
            db_ss={"ss_basic_block_webrtc_action": "proxy"},
        )
        self.assertEqual(res_from_proxy["action"], "start")

        res_node = eval_js_router({"ss_basic_block_webrtc_node": "3"})
        self.assertEqual(res_node["action"], "start")

    def test_shunt_hot_and_ingress_mode_combo(self):
        res_shunt = eval_js_router(
            {"ss_basic_shunt_rules": "W10=", "ss_basic_shunt_rule_ts": "123456"},
            dom={"ss_basic_mode": {"value": "7"}},
            db_ss={"ss_basic_mode": "7"},
        )
        self.assertEqual(res_shunt["action"], "start_shunt_hot")
        self.assertTrue(res_shunt["shuntHot"])

        res_ingress_only = eval_js_router(
            {"ss_basic_shunt_ingress_mode": "1", "ss_basic_shunt_rule_ts": "123456"},
            dom={"ss_basic_mode": {"value": "7"}},
            db_ss={"ss_basic_mode": "7"},
        )
        self.assertEqual(res_ingress_only["action"], "reload_iptables")
        self.assertFalse(res_ingress_only["shuntHot"])

        res_combo = eval_js_router(
            {
                "ss_basic_shunt_rules": "W10=",
                "ss_basic_shunt_ingress_mode": "1",
                "ss_basic_rule_update_time": "5",
                "ss_basic_tablet": "1",
            },
            dom={"ss_basic_mode": {"value": "7"}},
            db_ss={"ss_basic_mode": "7"},
        )
        self.assertEqual(res_combo["action"], "reload_shunt_hot-iptables-cron")

    def test_l3_ipset_and_dnsmasq_only(self):
        res_ipset = eval_js_router({
            "ss_wan_white_ip": "MS4xLjEuMQ==",
            "ss_wan_black_ip": "OC44LjguOA==",
        })
        self.assertEqual(res_ipset["action"], "reload_ipset")

        res_dnsmasq = eval_js_router({"ss_dnsmasq": "Y2FjaGUtc2l6ZT0xMDAw"})
        self.assertEqual(res_dnsmasq["action"], "reload_dnsmasq")

        res_both = eval_js_router({
            "ss_wan_white_ip": "MS4xLjEuMQ==",
            "ss_dnsmasq": "Y2FjaGUtc2l6ZT0xMDAw",
        })
        self.assertEqual(res_both["action"], "reload_ipset-dnsmasq")

    def test_l3_l4_dns_keys_and_dnsmasq_dedup(self):
        res_dns = eval_js_router({
            "ss_wan_white_domain": "ZXhhbXBsZS5jb20=",
            "ss_basic_dns_plan": "2",
            "ss_basic_smrt_chn_dns": "smartdns_b64:e30=",
            "ss_basic_chng_china_dns_1_chk": "1",
            "ss_basic_chng_trust_dot_2_usr": "1.1.1.1:853",
            "ss_dnsmasq": "Y2FjaGUtc2l6ZT0xMDAw",
        })
        # dns 局部重载已包含 dnsmasq，自动去重
        self.assertEqual(res_dns["action"], "reload_dns")

    def test_unknown_key_forces_full_restart(self):
        res = eval_js_router({
            "ss_basic_tablet": "1",
            "ss_basic_proxy_ipv6": "1",
        })
        self.assertEqual(res["action"], "start")

    def test_generated_actions_pass_websocket_allowlist_regex(self):
        ws_text = WEBSOCKET_PATH.read_text(encoding="utf-8")
        m = re.search(r"grep -Eq '(\^\(\\\. \)\?ss_\[a-z0-9_\]\+\\\.sh[^']+)'", ws_text)
        self.assertIsNotNone(m)
        pattern = re.compile(m.group(1))
        for action in [
            "save_only",
            "start_shunt_hot",
            "reload_cron",
            "reload_status",
            "reload_iptables",
            "reload_ipset",
            "reload_dnsmasq",
            "reload_dns",
            "reload_shunt_hot-ipset-dnsmasq-dns-iptables-cron-status",
        ]:
            self.assertRegex(f"ss_config.sh {action}", pattern)


class BackendPartialReloadTests(unittest.TestCase):
    def run_ssconfig_reload(
        self,
        scopes: str,
        extra_env: dict | None = None,
        socks5_open: str = "xray",
        ignlist_ok: bool = True,
        load_iptables_ok: bool = True,
        ps_output: str = "",
    ) -> tuple[int, list[str]]:
        fns = "\n\n".join(
            extract_shell_function(SSCONFIG_PATH, fn)
            for fn in [
                "reload_cron_only",
                "reload_status_only",
                "reload_iptables_only",
                "reload_ipset_wb_only",
                "reload_dnsmasq_only",
                "dns_udp_relay_targets_match_runtime",
                "reload_dns_only",
                "reload_scopes",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "events.log"
            env_lines = [
                'ss_basic_status="1"',
                'ss_basic_mode="2"',
                'ss_basic_type="3"',
                'ss_basic_udpoff="1"',
                'ss_basic_udpall="0"',
                'ss_basic_block_webrtc="0"',
                'ss_basic_block_webrtc_action="reject"',
                'mangle="0"',
            ]
            if extra_env:
                for k, v in extra_env.items():
                    env_lines.append(f'{k}="{v}"')
            script = f"""
{chr(10).join(env_lines)}
echo_date() {{ printf 'LOG:%s\\n' "$*" >> "{log_file}"; }}
set_default() {{ eval "[ -n \\"\\${{$1}}\\" ] || $1=\\"$2\\""; }}
dbus() {{ printf 'DBUS:%s\\n' "$*" >> "{log_file}"; }}
fss_socks5_open_bin() {{ printf '%s\\n' "{socks5_open}"; }}
ipset() {{ {'return 0' if ignlist_ok else 'return 1'}; }}
fss_base_load_current_node_env() {{ printf 'CALL:fss_base_load_current_node_env\\n' >> "{log_file}"; }}
refresh_runtime_context() {{ printf 'CALL:refresh_runtime_context\\n' >> "{log_file}"; }}
add_wan_bypass_ips_to_ignlist() {{ printf 'CALL:add_wan_bypass_ips_to_ignlist\\n' >> "{log_file}"; }}
flush_iptables() {{ printf 'CALL:flush_iptables\\n' >> "{log_file}"; }}
load_iptables() {{ printf 'CALL:load_iptables\\n' >> "{log_file}"; return {'0' if load_iptables_ok else '1'}; }}
write_cron_job() {{ printf 'CALL:write_cron_job\\n' >> "{log_file}"; }}
remove_ss_reboot_job() {{ printf 'CALL:remove_ss_reboot_job\\n' >> "{log_file}"; }}
set_ss_reboot_job() {{ printf 'CALL:set_ss_reboot_job\\n' >> "{log_file}"; }}
stop_status() {{ printf 'CALL:stop_status\\n' >> "{log_file}"; }}
check_status() {{ printf 'CALL:check_status\\n' >> "{log_file}"; }}
ps() {{ printf '%s\\n' "{ps_output}"; }}
{fns}
reload_scopes "{scopes}"
"""
            proc = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
            lines = log_file.read_text(encoding="utf-8").splitlines() if log_file.exists() else []
            return proc.returncode, lines

    def test_reload_scopes_runs_in_deterministic_order(self):
        rc, lines = self.run_ssconfig_reload("status-cron-iptables")
        self.assertEqual(rc, 0)
        calls = [line for line in lines if line.startswith("CALL:")]
        self.assertEqual(
            calls,
            [
                "CALL:fss_base_load_current_node_env",
                "CALL:refresh_runtime_context",
                "CALL:add_wan_bypass_ips_to_ignlist",
                "CALL:flush_iptables",
                "CALL:load_iptables",
                "CALL:write_cron_job",
                "CALL:remove_ss_reboot_job",
                "CALL:set_ss_reboot_job",
                "CALL:stop_status",
                "CALL:check_status",
            ],
        )

    def test_reload_iptables_rejects_missing_proxy_or_ipset(self):
        rc, _ = self.run_ssconfig_reload("iptables", socks5_open="")
        self.assertNotEqual(rc, 0)

        rc, _ = self.run_ssconfig_reload("iptables", ignlist_ok=False)
        self.assertNotEqual(rc, 0)

    def test_reload_iptables_rejects_ssr_udp_mismatch(self):
        rc, _ = self.run_ssconfig_reload(
            "iptables",
            extra_env={"ss_basic_type": "1", "mangle": "1"},
            ps_output="1234 root rss-redir -c /koolshare/ss/ssr.json",
        )
        self.assertNotEqual(rc, 0)

        rc_ok, _ = self.run_ssconfig_reload(
            "iptables",
            extra_env={"ss_basic_type": "1", "mangle": "1"},
            ps_output="1234 root rss-redir -c /koolshare/ss/ssr.json -u",
        )
        self.assertEqual(rc_ok, 0)

    def test_reload_iptables_rejects_webrtc_proxy_mode(self):
        rc, _ = self.run_ssconfig_reload(
            "iptables",
            extra_env={
                "ss_basic_block_webrtc": "1",
                "ss_basic_block_webrtc_action": "proxy",
            },
        )
        self.assertNotEqual(rc, 0)

    def test_ss_config_reload_scopes_fallback_on_failure(self):
        fn = extract_shell_function(SS_CONFIG_PATH, "reload_fancyss_scopes")
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "dispatch.log"
            script = f"""
echo_date() {{ printf 'LOG:%s\\n' "$*" >> "{log_file}"; }}
dbus() {{
    if [ "$1" = "get" ] && [ "$2" = "ss_basic_shunt_hot_reload" ]; then
        echo "1"
    fi
}}
start_fancyss() {{ printf 'CALL:start_fancyss\\n' >> "{log_file}"; }}
sh() {{
    printf 'SH:%s\\n' "$*" >> "{log_file}"
    case "$*" in
        *"reload_scopes"*) return 1 ;;
    esac
    return 0
}}
{fn}
reload_fancyss_scopes "reload_iptables-cron"
"""
            proc = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            lines = log_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("SH:/koolshare/ss/ssconfig.sh reload_scopes iptables-cron", lines)
            self.assertIn("CALL:start_fancyss", lines)

    def test_ss_config_rejects_invalid_scope_and_falls_back(self):
        fn = extract_shell_function(SS_CONFIG_PATH, "reload_fancyss_scopes")
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "dispatch.log"
            script = f"""
echo_date() {{ printf 'LOG:%s\\n' "$*" >> "{log_file}"; }}
start_fancyss() {{ printf 'CALL:start_fancyss\\n' >> "{log_file}"; }}
{fn}
reload_fancyss_scopes "reload_unknown"
"""
            proc = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            lines = log_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("CALL:start_fancyss", lines)

    def test_reload_ipset_atomic_swap(self):
        fn = extract_shell_function(SSCONFIG_PATH, "reload_ipset_wb_only")
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "ipset.log"
            restore_file = Path(tmp) / "restore.txt"
            script = f"""
ss_basic_status="1"
ss_basic_mode="2"
ss_wan_white_ip="MS4xLjEuMQ=="
ss_wan_black_ip="OC44LjguOA=="
echo_date() {{ printf 'LOG:%s\\n' "$*" >> "{log_file}"; }}
base64_decode() {{ python3 -c "import sys,base64; d=sys.stdin.read().strip(); print(base64.b64decode(d).decode() if d else '')"; }}
dbus() {{
    if [ "$1" = "list" ]; then
        printf 'ssconf_basic_server_1=9.9.9.9\\n'
    fi
}}
ipset() {{
    if [ "$1" = "-!" ] && [ "$2" = "-R" ]; then
        cat > "{restore_file}"
        printf 'IPSET:restore\\n' >> "{log_file}"
        return 0
    fi
    printf 'IPSET:%s\\n' "$*" >> "{log_file}"
    return 0
}}
{fn}
reload_ipset_wb_only
"""
            proc = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            lines = log_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("IPSET:restore", lines)
            self.assertIn("IPSET:swap fss_bl_tmp black_list", lines)
            self.assertIn("IPSET:swap fss_wl_tmp white_list", lines)
            self.assertIn("IPSET:destroy fss_bl_tmp", lines)
            self.assertIn("IPSET:destroy fss_wl_tmp", lines)
            restore_txt = restore_file.read_text(encoding="utf-8")
            self.assertIn("add fss_wl_tmp 1.1.1.1", restore_txt)
            self.assertIn("add fss_wl_tmp 9.9.9.9", restore_txt)
            self.assertIn("add fss_bl_tmp 8.8.8.8", restore_txt)
            self.assertIn("add fss_bl_tmp 149.154.0.0/16", restore_txt)

    def test_reload_dnsmasq_only_and_dns_dedup(self):
        fns = "\n\n".join(
            extract_shell_function(SSCONFIG_PATH, fn)
            for fn in ["reload_dnsmasq_only", "reload_scopes"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "dnsmasq.log"
            script = f"""
ss_basic_status="1"
echo_date() {{ printf 'LOG:%s\\n' "$*" >> "{log_file}"; }}
create_dnsmasq_conf() {{ printf 'CALL:create_dnsmasq_conf\\n' >> "{log_file}"; }}
restart_dnsmasq() {{ printf 'CALL:restart_dnsmasq\\n' >> "{log_file}"; }}
reload_dns_only() {{ printf 'CALL:reload_dns_only\\n' >> "{log_file}"; return 0; }}
{fns}
reload_scopes "dnsmasq"
printf -- '===SPLIT===\\n' >> "{log_file}"
reload_scopes "dns-dnsmasq"
"""
            proc = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            content = log_file.read_text(encoding="utf-8")
            part1, part2 = content.split("===SPLIT===\n")
            self.assertIn("CALL:create_dnsmasq_conf", part1)
            self.assertIn("CALL:restart_dnsmasq", part1)
            self.assertIn("CALL:reload_dns_only", part2)
            self.assertNotIn("CALL:create_dnsmasq_conf", part2)

    def test_dns_udp_relay_targets_match_and_mismatch(self):
        if not shutil.which("jq"):
            self.skipTest("jq is not installed")
        fn = extract_shell_function(SSCONFIG_PATH, "dns_udp_relay_targets_match_runtime")
        with tempfile.TemporaryDirectory() as tmp:
            xray_conf = Path(tmp) / "xray.json"
            xray_conf.write_text(
                json.dumps({
                    "inbounds": [
                        {
                            "tag": "dns_udp_7915",
                            "protocol": "dokodemo-door",
                            "port": 7915,
                            "settings": {"address": "8.8.8.8", "port": 53},
                        }
                    ]
                }),
                encoding="utf-8",
            )
            base_script = f"""
ss_basic_mode="2"
ss_basic_type="3"
XRAY_RUNTIME_CONF="{xray_conf}"
run() {{ "$@"; }}
{fn}
"""
            # 1. Exact match -> returns 0
            match_script = base_script + """
iter_dns_udp_relay_targets() { printf '7915\0378.8.8.8\03753\037gfw\037\n'; }
dns_udp_relay_targets_match_runtime
"""
            proc_match = subprocess.run(["sh", "-c", match_script], capture_output=True, text=True)
            self.assertEqual(proc_match.returncode, 0)

            # 2. Target changed -> returns non-zero (forces full restart)
            mismatch_script = base_script + """
iter_dns_udp_relay_targets() { printf '7915\0371.1.1.1\03753\037gfw\037\n'; }
dns_udp_relay_targets_match_runtime
"""
            proc_mismatch = subprocess.run(["sh", "-c", mismatch_script], capture_output=True, text=True)
            self.assertNotEqual(proc_mismatch.returncode, 0)

            # 3. Added second relay -> returns non-zero
            added_script = base_script + """
iter_dns_udp_relay_targets() {
    printf '7915\0378.8.8.8\03753\037gfw\037\n'
    printf '7916\0371.1.1.1\03753\037gfw\037\n'
}
dns_udp_relay_targets_match_runtime
"""
            proc_added = subprocess.run(["sh", "-c", added_script], capture_output=True, text=True)
            self.assertNotEqual(proc_added.returncode, 0)


if __name__ == "__main__":
    unittest.main()
