"""Regression tests for Issue #41 (shunt proxy domain merge) and Issue #42 (fail-closed restart & failover)."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_partial_reload import ROOT, SSCONFIG_PATH, extract_shell_function


BASE_SCRIPT = ROOT / "fancyss/scripts/ss_base.sh"
SHUNT_SCRIPT = ROOT / "fancyss/scripts/ss_node_shunt.sh"
HOT_RELOAD_SCRIPT = ROOT / "fancyss/scripts/ss_shunt_hot_reload.sh"
STATUS_MAIN_SCRIPT = ROOT / "fancyss/scripts/ss_status_main.sh"


def functions(path, *names):
    return "\n".join(extract_shell_function(path, name) for name in names)


def run_bash(script):
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=15)


class ShuntProxyDomainMergeTests(unittest.TestCase):
    """Issue #41: multi-rule proxy domain merge, case-insensitive DIRECT/REJECT, and cap-trimmed rebuild."""

    def test_prepare_runtime_accumulates_all_proxy_rules_and_excludes_mixed_case_special_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "shunt"
            rule_dir = runtime_dir / "rules"
            proxy_out = root / "ss_shunt_proxy.txt"
            active_out = runtime_dir / "active_rules.tsv"
            target_out = runtime_dir / "target_nodes.txt"

            # Exceeds FSS_SHUNT_MAX_RULES=4 and FSS_SHUNT_MAX_TARGETS=2 to trigger rebuild_runtime_lists=1
            # with lowercase direct/reject targets.
            rules = [
                {"id": "r_claude", "enabled": "1", "source": "builtin", "preset": "claude", "custom_b64": "", "target_node_id": "24", "remark": "Claude"},
                {"id": "r_openai", "enabled": "1", "source": "builtin", "preset": "openai", "custom_b64": "", "target_node_id": "24", "remark": "OpenAI"},
                {"id": "r_gemini", "enabled": "1", "source": "builtin", "preset": "gemini", "custom_b64": "", "target_node_id": "34", "remark": "Gemini"},
                {"id": "r_steam", "enabled": "1", "source": "builtin", "preset": "steam", "custom_b64": "", "target_node_id": "direct", "remark": "Steam"},
                {"id": "r_ads", "enabled": "1", "source": "builtin", "preset": "ads", "custom_b64": "", "target_node_id": "Reject", "remark": "Ads"},
                {"id": "r_extra", "enabled": "1", "source": "builtin", "preset": "extra", "custom_b64": "", "target_node_id": "99", "remark": "Extra"},
            ]
            rules_json = json.dumps(rules)

            fn_code = functions(
                SHUNT_SCRIPT,
                "fss_shunt_prepare_runtime",
                "fss_shunt_rebuild_proxy_domains",
            )
            script = f"""
set -e
FSS_SHUNT_RUNTIME_DIR='{runtime_dir}'
FSS_SHUNT_RUNTIME_RULE_DIR='{rule_dir}'
FSS_SHUNT_RUNTIME_PROXY_FILE='{proxy_out}'
FSS_SHUNT_RUNTIME_ACTIVE_FILE='{active_out}'
FSS_SHUNT_RUNTIME_TARGET_FILE='{target_out}'
FSS_SHUNT_MAX_RULES=5
FSS_SHUNT_MAX_TARGETS=2
FSS_SHUNT_RUNTIME_READY=""
FSS_SHUNT_RUNTIME_READY_KEY=""

fss_shunt_mode_selected() {{ return 0; }}
fss_shunt_rules_enabled() {{ return 0; }}
fss_shunt_runtime_key() {{ echo "k1"; }}
fss_shunt_runtime_is_fresh() {{ return 1; }}
fss_shunt_cleanup_runtime() {{ rm -rf "{runtime_dir}" "{proxy_out}"; }}
fss_shunt_rules_json() {{ cat <<'EOF'
{rules_json}
EOF
}}
fss_shunt_write_rules_mirror() {{ :; }}
fss_pick_jq_bin() {{ command -v jq; }}
fss_mktemp_dir() {{ mktemp -d "{root}/tmp.XXXXXX"; }}
fss_shunt_target_is_special() {{
    case "$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')" in
    DIRECT|REJECT) return 0 ;;
    *) return 1 ;;
    esac
}}
fss_shunt_target_is_proxy_node() {{ return 0; }}
fss_shunt_write_runtime_meta() {{ :; }}
fss_shunt_materialize_rule_domains() {{
    local preset="$2" domain_file="$4" proxy_file="$5"
    : > "${{domain_file}}"
    : > "${{proxy_file}}"
    case "${{preset}}" in
    claude)
        printf 'domain:claude.ai\\nfull:challenges.cloudflare.com\\n' > "${{domain_file}}"
        printf 'claude.ai\\nchallenges.cloudflare.com\\n' > "${{proxy_file}}"
        echo 2
        ;;
    openai)
        printf 'domain:openai.com\\n' > "${{domain_file}}"
        printf 'openai.com\\n' > "${{proxy_file}}"
        echo 1
        ;;
    gemini)
        printf 'domain:gemini.google.com\\n' > "${{domain_file}}"
        printf 'gemini.google.com\\n' > "${{proxy_file}}"
        echo 1
        ;;
    steam)
        printf 'domain:steampowered.com\\n' > "${{domain_file}}"
        printf 'steampowered.com\\n' > "${{proxy_file}}"
        echo 1
        ;;
    ads)
        printf 'domain:ads.example.com\\n' > "${{domain_file}}"
        printf 'ads.example.com\\n' > "${{proxy_file}}"
        echo 1
        ;;
    extra)
        printf 'domain:extra.example.com\\n' > "${{domain_file}}"
        printf 'extra.example.com\\n' > "${{proxy_file}}"
        echo 1
        ;;
    esac
}}

{fn_code}

fss_shunt_prepare_runtime
"""
            res = run_bash(script)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(proxy_out.exists(), "proxy domain file should exist")
            domains = set(proxy_out.read_text().splitlines())
            self.assertEqual(
                domains,
                {"claude.ai", "challenges.cloudflare.com", "openai.com", "gemini.google.com"},
            )
            self.assertNotIn("steampowered.com", domains)
            self.assertNotIn("ads.example.com", domains)

            # Direct probe on fss_shunt_rebuild_proxy_domains with raw lowercase direct/reject lines.
            raw_active = root / "raw_active.tsv"
            raw_active.write_text(
                f"r_claude|24|{rule_dir}/r_claude.domains|builtin|claude|Claude\n"
                f"r_steam|direct|{rule_dir}/r_steam.domains|builtin|steam|Steam\n"
                f"r_ads|reject|{rule_dir}/r_ads.domains|builtin|ads|Ads\n"
            )
            rebuilt = root / "rebuilt_proxy.txt"
            res2 = run_bash(
                fn_code
                + f'\nfss_shunt_rebuild_proxy_domains "{raw_active}" "{rebuilt}"\n'
            )
            self.assertEqual(res2.returncode, 0, res2.stderr)
            self.assertEqual(
                set(rebuilt.read_text().splitlines()),
                {"claude.ai", "challenges.cloudflare.com"},
            )


class FailClosedRestartTests(unittest.TestCase):
    """Issue #42 Phase 1 & 2: fail-closed restart, guard chain, real close_in_five, resolved IP, helper exclusion."""

    def test_apply_ss_restart_uses_guard_chain_resolved_ip_and_delays_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_log = Path(tmp) / "events.log"
            fn_code = functions(
                SSCONFIG_PATH,
                "apply_ss",
                "add_current_server_ip_to_ignlist",
                "clear_current_node_server_ip",
                "record_current_node_server_ip",
            )
            script = f"""
set -e
ss_basic_status=1
ss_basic_mode=7
ss_basic_type=4
ss_basic_server_ip="stale.should.be.cleared"
ss_basic_shunt_hot_reload=0

log_event() {{ echo "$1" >> '{event_log}'; }}
echo_date() {{ :; }}
refresh_runtime_context() {{ :; }}
shunt_hot_restart_eligible() {{ return 1; }}
ss_pre_stop() {{ log_event ss_pre_stop; }}
stop_status() {{ log_event stop_status; }}
kill_process() {{ log_event kill_process; }}
remove_ss_trigger_job() {{ :; }}
remove_ss_reboot_job() {{ :; }}
restore_conf() {{ log_event RESTORE_CONF_FORBIDDEN; }}
restart_dnsmasq() {{ log_event restart_dnsmasq; }}
install_fail_closed_guard() {{ log_event install_fail_closed_guard; return 0; }}
reconcile_fail_closed_ipsets() {{ log_event reconcile_fail_closed_ipsets; return 0; }}
verify_proxy_ingress_rules() {{ log_event verify_proxy_ingress_rules; return 0; }}
remove_fail_closed_guard() {{ log_event remove_fail_closed_guard; return 0; }}
flush_iptables() {{ log_event flush_iptables; }}
flush_ipset() {{ log_event FLUSH_IPSET_FORBIDDEN; }}
kill_cron_job() {{ :; }}
prepare_system() {{ log_event prepare_system; }}
resolv_server_ip() {{
    log_event resolv_server_ip
    clear_current_node_server_ip
}}
refresh_current_node_server_ip_runtime() {{
    log_event refresh_current_node_server_ip_runtime
    record_current_node_server_ip "203.0.113.10"
}}
ipset() {{ log_event "ipset:$*"; }}
load_module() {{ :; }}
creat_ipset() {{ log_event "creat_ipset:fc=${{FSS_FAIL_CLOSED_RESTART}}"; }}
create_dnsmasq_conf() {{ log_event create_dnsmasq_conf; }}
add_white_black() {{ log_event add_white_black; }}
creat_shunt_json() {{ log_event creat_shunt_json; }}
should_bootstrap_dns_before_proxy() {{ return 0; }}
start_xray() {{ log_event start_xray; }}
start_dns_x() {{ log_event start_dns_x; }}
get_proxy_server_ip() {{ log_event get_proxy_server_ip; }}
load_iptables() {{ log_event load_iptables; }}
auto_start() {{ :; }}
write_cron_job() {{ :; }}
set_ss_reboot_job() {{ :; }}
write_numbers() {{ :; }}
finish_start() {{ :; }}
ss_post_start() {{ :; }}
check_status() {{ :; }}
dbus() {{ :; }}

{fn_code}

apply_ss
"""
            res = run_bash(script)
            self.assertEqual(res.returncode, 0, res.stderr)
            events = event_log.read_text().splitlines()

            self.assertNotIn("RESTORE_CONF_FORBIDDEN", events)
            self.assertNotIn("FLUSH_IPSET_FORBIDDEN", events)
            self.assertIn("creat_ipset:fc=1", events)
            # Review #3 probe: domain node resolved during runtime bootstrap must be added to ignlist before start_xray.
            self.assertIn("ipset:-! add ignlist 203.0.113.10", events)
            self.assertLess(events.index("ipset:-! add ignlist 203.0.113.10"), events.index("start_xray"))

            idx_xray = events.index("start_xray")
            idx_guard_on = events.index("install_fail_closed_guard")
            idx_reconcile = events.index("reconcile_fail_closed_ipsets")
            idx_flush_ipt = events.index("flush_iptables")
            idx_load_ipt = events.index("load_iptables")
            idx_verify = events.index("verify_proxy_ingress_rules")
            idx_guard_off = events.index("remove_fail_closed_guard")

            self.assertGreater(idx_guard_on, idx_xray)
            self.assertEqual(idx_guard_on + 1, idx_reconcile)
            self.assertEqual(idx_reconcile + 1, idx_flush_ipt)
            self.assertEqual(idx_flush_ipt + 1, idx_load_ipt)
            self.assertEqual(idx_load_ipt + 1, idx_verify)
            self.assertEqual(idx_verify + 1, idx_guard_off)

    def test_client_acl_write_failure_stops_firewall_reload(self):
        code = functions(SSCONFIG_PATH, "load_iptables", "_start_iptables", "lan_access_control",
                         "apply_acl_udp_rule", "apply_quic_block", "apply_acl_quic_filter_rule",
                         "start_ipv6_proxy_rules", "get_shunt_ingress_mode")
        script = """
FSS_FAIL_CLOSED_RESTART=1
ss_basic_mode=7
ss_acl_ip_1=192.0.2.3
ss_acl_mode_1=5
mangle=0
prepare_anytls_merlinbox_compat() { :; }
ifconfig() { echo br0; }
ensure_chain() { :; }
init_shunt_chain_v4() { :; }
echo_date() { :; }
get_acl_rule_indexes() { echo 1; }
get_acl_ip_mark() { echo 1; }
get_acl_source_rule4() { echo "-s $ss_acl_ip_1"; }
get_acl_udp_flag() { echo 1; }
get_acl_quic_flag() { echo 1; }
get_jump_mode() { echo g; }
get_action_chain() { [ "$1" = 5 ] && echo SHADOWSOCKS_GLO || echo RETURN; }
get_mode_name() { echo "$1"; }
resolve_acl_ports() { echo all; }
resolve_acl_default_ports_raw() { echo all; }
resolve_acl_default_mode() { echo 0; }
factor() { :; }
dbus() { :; }
acl_proxy_supports_udp() { return 0; }
iptables() {
    case "$*" in
        "-t nat -L PREROUTING"*) echo ready ;;
        *"-A SHADOWSOCKS -s 192.0.2.3 -p tcp"*) [ "$fault" != tcp ] ;;
        *"-A SHADOWSOCKS -s 192.0.2.3 -p udp --dport 443"*) [ "$fault" != udp ] ;;
    esac
}
append_if_not_exists() {
    case "$*" in
        "filter -A SHADOWSOCKS -s 192.0.2.3 "*) [ "$fault" != filter ] ;;
    esac
}
insert_if_not_exists() { :; }
apply_webrtc_block4() { :; }
apply_webrtc_block6() { :; }
dns_hijack_control() { :; }
ipv6_proxy_enabled() { return 0; }
_start_ipv6_iptables() { [ "$fault" != v6 ]; }
prepare_anytls_merlinbox_compat6() { :; }
fallback_ipv6_proxy_to_ipv4() { echo DOWNGRADE; }
close_in_five() { echo "HALT:$1"; }
"""
        for fault in ("tcp", "udp", "filter", "v6", "none"):
            with self.subTest(fault=fault):
                res = run_bash(code + script + f"\nfault={fault}\nload_iptables")
                self.assertEqual(res.stderr, "")
                if fault == "none":
                    self.assertEqual(res.returncode, 0, res.stdout)
                else:
                    self.assertNotEqual(res.returncode, 0)
                    self.assertIn("HALT:flag", res.stdout)
                    self.assertNotIn("DOWNGRADE", res.stdout)

    def test_guard_install_failure_aborts_before_flush_and_covers_ipv6_full_udp_and_black_priority(self):
        # Review #1, #2, #3, #5 probes (incl. PR #43 #1 global ACL, #2 OUTPUT guard, #3 ingress verification)
        fn_code = functions(
            SSCONFIG_PATH,
            "fail_closed_guard_has_global_mode",
            "install_fail_closed_guard_chain_hook",
            "install_fail_closed_guard_family",
            "remove_fail_closed_guard_chain_hook",
            "remove_fail_closed_guard_family",
            "install_fail_closed_guard",
            "remove_fail_closed_guard",
            "verify_proxy_ingress_rules",
        )
        # 1) All iptables calls fail -> install_fail_closed_guard MUST return non-zero.
        res_fail = run_bash(
            fn_code
            + """
iptables() { return 1; }
ip6tables() { return 1; }
install_fail_closed_guard
"""
        )
        self.assertNotEqual(res_fail.returncode, 0)

        # 2) Verify rule ordering & coverage: black_list/gfwlist before chnlist/chnroute, full UDP, IPv6 guard, OUTPUT guard.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "ipt.log"
            res_ok = run_bash(
                fn_code
                + f"""
ss_basic_mode=2
ss_basic_proxy_ipv6=1
ip_prefix_hex="0xc0a80a00/0xffffff00"
dbus() {{ :; }}
iptables() {{
    echo "v4:$*" >> '{log}'
    case "$3" in
    -C|-L) return 1 ;;
    esac
    return 0
}}
ip6tables() {{
    echo "v6:$*" >> '{log}'
    case "$3" in
    -C|-L) return 1 ;;
    esac
    return 0
}}
install_fail_closed_guard
"""
            )
            self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
            lines = log.read_text().splitlines()
            v4_rules = [l for l in lines if l.startswith("v4:-t filter -A FSS_FC_GUARD_TMP ")]
            v6_rules = [l for l in lines if l.startswith("v6:-t filter -A FSS_FC_GUARD6_TMP ")]
            v4_out_rules = [l for l in lines if l.startswith("v4:-t filter -A FSS_FC_GUARD_OUT_TMP ")]
            v6_out_rules = [l for l in lines if l.startswith("v6:-t filter -A FSS_FC_GUARD6_OUT_TMP ")]
            self.assertTrue(v6_rules, "IPv6 guard chain FSS_FC_GUARD6_TMP must be installed")
            self.assertTrue(v4_out_rules, "IPv4 OUTPUT guard chain FSS_FC_GUARD_OUT_TMP must be installed")
            self.assertTrue(v6_out_rules, "IPv6 OUTPUT guard chain FSS_FC_GUARD6_OUT_TMP must be installed")
            self.assertIn("v4:-t filter -I OUTPUT 1 -j FSS_FC_GUARD_OUT_TMP", lines)
            self.assertTrue(any("--match-set router dst -j REJECT" in l for l in v4_out_rules))
            # P1 #1 probe: production ip_prefix_hex already has /0xffffff00; must NOT double-append mask!
            self.assertTrue(any("--mark 0xc0a80a00/0xffffff00 -j REJECT" in l for l in v4_out_rules))
            self.assertFalse(any("/0xffffff00/0xffffff00" in l for l in v4_out_rules))

            idx_black = next(i for i, l in enumerate(v4_rules) if "black_list" in l)
            idx_gfw = next(i for i, l in enumerate(v4_rules) if "gfwlist" in l)
            idx_chnlist = next(i for i, l in enumerate(v4_rules) if "chnlist" in l)
            idx_chnroute = next(i for i, l in enumerate(v4_rules) if "chnroute" in l)
            self.assertLess(idx_black, idx_chnlist)
            self.assertLess(idx_gfw, idx_chnroute)
            self.assertTrue(any("-p udp -j REJECT" in l for l in v4_rules), "must block all overseas UDP, not just 443")
            self.assertTrue(any("-p udp -j REJECT" in l for l in v6_rules), "must block all overseas IPv6 UDP")

        # 2b) P1 #2 probe: mode 7 with ss_basic_shunt_ingress_mode=5 (no explicit global ACL) & global ACL must NOT RETURN on chnlist/chnroute.
        for label, extra_env in (
            ("global_acl", 'ss_basic_mode=2\nacl_nu="1"\nss_acl_mode_1=5\n'),
            ("shunt_global_ingress", 'ss_basic_mode=7\nss_basic_shunt_ingress_mode=5\n'),
        ):
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory() as tmp:
                    log = Path(tmp) / f"{label}.log"
                    res_global = run_bash(
                        fn_code
                        + f"""
{extra_env}
ss_basic_proxy_ipv6=0
dbus() {{ :; }}
iptables() {{
    echo "$*" >> '{log}'
    case "$3" in
    -C|-L) return 1 ;;
    esac
    return 0
}}
ip6tables() {{ return 1; }}
install_fail_closed_guard
"""
                    )
                    self.assertEqual(res_global.returncode, 0, res_global.stderr)
                    g_rules = [l for l in log.read_text().splitlines() if l.startswith("-t filter -A FSS_FC_GUARD_TMP ")]
                    self.assertFalse(any("chnlist" in l for l in g_rules), f"{label} guard must not RETURN on chnlist")
                    self.assertFalse(any("chnroute" in l for l in g_rules), f"{label} guard must not RETURN on chnroute")
                    self.assertTrue(any("white_static" in l for l in g_rules))
                    self.assertTrue(any("white_list" in l for l in g_rules))

        # 2c) P1 #3 probe: PREROUTING jump exists, but SHADOWSOCKS_GLO lacks final REDIRECT -> verify MUST fail!
        res_verify_missing_redir = run_bash(
            fn_code
            + "\n"
            + functions(SSCONFIG_PATH, "get_action_chain")
            + """
ss_basic_mode=5
ss_basic_proxy_ipv6=0
dbus() { :; }
iptables() {
    case "$*" in
    "-t nat -C PREROUTING -p tcp -j SHADOWSOCKS") return 0 ;;
    "-t nat -S SHADOWSOCKS_GLO")
        printf '%s\n' '-N SHADOWSOCKS_GLO' '-A SHADOWSOCKS_GLO -p tcp -m set --match-set white_list dst -j RETURN'
        return 0
        ;;
    *) return 1 ;;
    esac
}
verify_proxy_ingress_rules
"""
        )
        self.assertNotEqual(res_verify_missing_redir.returncode, 0)
        self.assertIn(
            'append_if_not_exists nat -A SHADOWSOCKS_GLO -p tcp -j REDIRECT --to-ports 3333 || return 1',
            SSCONFIG_PATH.read_text(),
        )
        self.assertIn(
            'iptables -t nat -I PREROUTING "${INSET_NU}" -p tcp -j SHADOWSOCKS || return 1',
            SSCONFIG_PATH.read_text(),
        )

        # 3) Review #3 probe: retry install fails mid-way -> MUST NOT flush or delete previous live FSS_FC_GUARD.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "retry.log"
            res_retry = run_bash(
                fn_code
                + f"""
ss_basic_mode=2
dbus() {{ :; }}
iptables() {{
    echo "$*" >> '{log}'
    case "$*" in
    "-t filter -L FSS_FC_GUARD") return 0 ;;
    "-t filter -L FSS_FC_GUARD_TMP") return 1 ;;
    "-t filter -N FSS_FC_GUARD_TMP") return 0 ;;
    "-t filter -A FSS_FC_GUARD_TMP"*"gfwlist"*) return 1 ;;
    *) return 0 ;;
    esac
}}
ip6tables() {{ return 1; }}
install_fail_closed_guard
"""
            )
            self.assertNotEqual(res_retry.returncode, 0)
            retry_ops = log.read_text().splitlines()
            self.assertNotIn("-t filter -F FSS_FC_GUARD", retry_ops)
            self.assertNotIn("-t filter -D FORWARD -j FSS_FC_GUARD", retry_ops)
            self.assertNotIn("-t filter -X FSS_FC_GUARD", retry_ops)
            self.assertIn("-t filter -X FSS_FC_GUARD_TMP", retry_ops)

        # 4) Review #5 probe: remove_fail_closed_guard MUST return non-zero if chain/jump remains.
        res_rem_fail = run_bash(
            fn_code
            + """
iptables() {
    case "$*" in
    "-t filter -C FORWARD -j FSS_FC_GUARD") return 0 ;;
    "-t filter -D FORWARD -j FSS_FC_GUARD") return 1 ;;
    "-t filter -L FSS_FC_GUARD") return 0 ;;
    *) return 1 ;;
    esac
}
ip6tables() { return 1; }
remove_fail_closed_guard
"""
        )
        self.assertNotEqual(res_rem_fail.returncode, 0)

    def test_reconcile_fail_closed_ipsets_preserves_dynamic_sets_and_propagates_failure(self):
        # P1 #4 probe: static sets (ignlist*, black_static*, white_static*) are atomically swapped so removing a
        # manual black IP NEVER deletes an overlapping dynamic black_list DNS entry; and deleting a whitelist
        # domain triggers atomic reset of dynamic white_list* while preserving white_static*.
        import base64
        fn_code = functions(
            SSCONFIG_PATH,
            "fss_static_ignlist_v4",
            "fss_static_ignlist_v6",
            "fss_decode_static_ip_list",
            "fss_collect_current_static_ip_lists",
            "fss_white_domain_sig",
            "reconcile_fail_closed_ipsets",
            "add_current_server_ip_to_ignlist",
        )
        fn_code += "\n" + functions(ROOT / "fancyss/scripts/ss_node_common.sh",
                                     "fss_list_node_server_ips", "fss_list_node_server_hosts",
                                     "fss_list_node_server_hosts_v2_fast", "fss_pick_jq_bin")
        black_b64 = base64.b64encode(b"198.51.100.20\n2001:db8::20\n").decode()
        white_b64 = base64.b64encode(b"198.51.100.30\n2001:db8::30\n").decode()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captured = root / "ipset_restore.txt"
            white_dom = root / "white_list.txt"
            white_sig = root / "white_domain.sig"
            white_dom.write_text("apple.com\nexample-old.com\n")
            white_sig.write_text("old_sig:999\n")

            nodes = root / "nodes"
            entries = [{"type": "4", "server": "203.0.113.55"}, {"type": "9", "anytls_server": "2001:db8::55"},
                       {"type": "5", "server": "node.example.org"},
                       {"type": "4", "xray_use_json": "1", "xray_json": json.dumps({"outbounds": [
                           {"protocol": "vless", "settings": {"address": "203.0.113.66", "port": 443}}]})}]
            nodes.write_text("".join(f"fss_node_{i}={base64.b64encode(json.dumps(node).encode()).decode()}\n"
                                     for i, node in enumerate(entries, 1)))
            fn_code += f"""
FSS_STATIC_WHITE_SNAP_V4='{root}/legacy4'
FSS_STATIC_WHITE_SNAP_V6='{root}/legacy6'
fss_detect_storage_schema() {{ echo 2; }}
dbus() {{
    case "$1:$2" in
        get:fss_node_order) echo "$TEST_NODE_ORDER" ;;
        list:fss_node_) cat '{nodes}' ;;
    esac
}}
"""
            # Case 1: whitelist domain removed -> dynamic white_list* is reset via swap,
            # while dynamic black_list* is untouched (only black_static* is swapped!).
            white_dom.write_text("apple.com\n")
            res_ok = run_bash(
                fn_code
                + f"""
ss_basic_mode=7
ISP_DNS1="202.96.134.133"
ISP_DNS2="2400:3200::1"
ss_wan_black_ip="{black_b64}"
ss_wan_white_ip="{white_b64}"
CURRENT_NODE_SERVER_RESOLVED_IP="203.0.113.10"
FSS_WHITE_DOMAIN_FILE="{white_dom}"
FSS_WHITE_DOMAIN_SIG_FILE="{white_sig}"
base64_decode() {{ python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read()).decode())'; }}
TEST_NODE_ORDER=1,2,3,4
add_wan_bypass_ips_to_ignlist() {{ return 0; }}
ipset() {{
    if [ "$1" = "-R" ]; then
        cat > '{captured}'
        return 0
    fi
    return 0
}}
reconcile_fail_closed_ipsets
"""
            )
            self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
            body = captured.read_text()
            self.assertIn("swap ignlist ignlist_fc_tmp", body)
            self.assertIn("swap ignlist6 ignlist6_fc_tmp", body)
            self.assertIn("swap black_static black_static_fc_tmp", body)
            self.assertIn("swap black_static6 black_static6_fc_tmp", body)
            self.assertIn("swap white_static white_static_fc_tmp", body)
            self.assertIn("swap white_static6 white_static6_fc_tmp", body)
            # Dynamic black_list must NEVER be deleted/swapped/flushed when static black IPs change!
            self.assertNotIn("del black_list", body)
            self.assertNotIn("swap black_list", body)
            self.assertNotIn("flush black_list", body)
            # Whitelist domain changed -> dynamic white_list* IS reset via swap to clear stale domain IPs!
            self.assertIn("swap white_list white_list_dyn_tmp", body)
            self.assertIn("swap white_list6 white_list6_dyn_tmp", body)
            # Static items go to black_static* / white_static* with IPv4/IPv6 separation
            self.assertIn("add black_static_fc_tmp 198.51.100.20", body)
            self.assertIn("add black_static6_fc_tmp 2001:db8::20", body)
            self.assertIn("add white_static_fc_tmp 198.51.100.30", body)
            self.assertIn("add white_static6_fc_tmp 2001:db8::30", body)
            self.assertIn("add white_static_fc_tmp 202.96.134.133", body)
            self.assertIn("add white_static6_fc_tmp 2400:3200::1", body)
            self.assertNotIn("add white_static_fc_tmp 2400:3200::1", body)
            for required_entry in ("192.18.0.0/15", "223.5.5.5", "114.114.114.114", "1.2.4.8", "180.76.76.76", "119.29.29.29"):
                self.assertIn(f"add ignlist_fc_tmp {required_entry}", body)
            for dynamic_set in ("gfwlist", "gfwlist6", "chnlist", "chnlist6", "router", "router6"):
                self.assertNotIn(f"flush {dynamic_set}", body)

            self.assertIn("add white_static_fc_tmp 203.0.113.55", body)
            self.assertIn("add white_static6_fc_tmp 2001:db8::55", body)
            self.assertIn("add white_static_fc_tmp 203.0.113.66", body)
            self.assertNotIn("node.example.org", body)

            # Case 2: whitelist domains unchanged -> dynamic white_list* is preserved (no swap).
            captured.write_text("")
            same_script = fn_code + f"""
ss_basic_mode=7
FSS_WHITE_DOMAIN_FILE="{white_dom}"
FSS_WHITE_DOMAIN_SIG_FILE="{white_sig}"
TEST_NODE_ORDER=2,3
add_wan_bypass_ips_to_ignlist() {{ return 0; }}
ipset() {{
    if [ "$1" = "-R" ]; then
        cat > '{captured}'
        return 0
    fi
    return 0
}}
reconcile_fail_closed_ipsets
"""
            res_same = run_bash(same_script)
            self.assertEqual(res_same.returncode, 0, res_same.stderr)
            self.assertNotIn("swap white_list ", captured.read_text())

            self.assertNotIn("203.0.113.55", captured.read_text())
            self.assertIn("add white_static6_fc_tmp 2001:db8::55", captured.read_text())

            # Older hot switches polluted the dynamic set even with an unchanged domain signature.
            legacy = root / "legacy4"
            legacy.write_text("203.0.113.55\n")
            failed = run_bash(same_script.replace("cat >", "return 1; cat >"))
            self.assertNotEqual(failed.returncode, 0)
            self.assertTrue(legacy.exists())
            migrated = run_bash(same_script)
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            self.assertIn("swap white_list white_list_dyn_tmp", captured.read_text())
            self.assertFalse(legacy.exists())
            self.assertEqual(run_bash(same_script).returncode, 0)
            self.assertNotIn("swap white_list ", captured.read_text())

            # Failure propagation probe: ipset -R fails -> reconcile_fail_closed_ipsets MUST return non-zero.
            res_fail = run_bash(
                fn_code
                + f"""
FSS_WHITE_DOMAIN_FILE="{white_dom}"
FSS_WHITE_DOMAIN_SIG_FILE="{white_sig}"
TEST_NODE_ORDER=2,3
ipset() {{ return 1; }}
reconcile_fail_closed_ipsets
"""
            )
            self.assertNotEqual(res_fail.returncode, 0)

    def test_disable_ss_aborts_flush_ipset_if_guard_removal_fails(self):
        # Review #5 probe: disable_ss must NOT flush ipsets when guard remains stuck in FORWARD.
        fn_code = functions(SSCONFIG_PATH, "disable_ss")
        script = f"""
echo_date() {{ echo "$*"; }}
ss_pre_stop() {{ :; }}
set_skin() {{ :; }}
dbus() {{ :; }}
stop_status() {{ :; }}
stop_ws() {{ :; }}
kill_process() {{ :; }}
remove_ss_trigger_job() {{ :; }}
remove_ss_reboot_job() {{ :; }}
restore_conf() {{ :; }}
restart_dnsmasq() {{ :; }}
remove_fail_closed_guard() {{ echo "REMOVE_GUARD_TRY"; return 1; }}
sleep() {{ :; }}
flush_iptables() {{ echo "FLUSH_IPTABLES_FORBIDDEN"; }}
flush_ipset() {{ echo "FLUSH_IPSET_FORBIDDEN"; }}
kill_cron_job() {{ :; }}

{fn_code}

disable_ss
"""
        res = run_bash(script)
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(res.stdout.count("REMOVE_GUARD_TRY"), 3)
        self.assertNotIn("FLUSH_IPSET_FORBIDDEN", res.stdout)

    def test_reload_dns_process_entry_sets_fail_closed_guards_so_close_in_five_never_disables(self):
        # Review #2 probe: reload_dns_process must set FSS_PARTIAL_RELOAD=1 & FSS_FAIL_CLOSED_RESTART=1.
        src = SSCONFIG_PATH.read_text()
        idx = src.index("reload_dns_process)")
        snippet = src[idx:idx + 350]
        self.assertIn("FSS_PARTIAL_RELOAD=1", snippet)
        self.assertIn("FSS_FAIL_CLOSED_RESTART=1", snippet)

        fn_code = functions(BASE_SCRIPT, "close_in_five")
        res = run_bash(
            fn_code
            + """
echo_date() { :; }
unset_lock() { :; }
disable_ss() { echo "DISABLE_SS_FORBIDDEN"; }
(
    FSS_PARTIAL_RELOAD=1
    FSS_FAIL_CLOSED_RESTART=1
    export FSS_PARTIAL_RELOAD FSS_FAIL_CLOSED_RESTART
    close_in_five flag
)
rc=$?
echo "RC=$rc"
"""
        )
        self.assertIn("RC=1", res.stdout)
        self.assertNotIn("DISABLE_SS_FORBIDDEN", res.stdout)

    def test_creat_ipset_preserves_all_live_sets_and_swaps_chnroute_in_fail_closed_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chn4 = root / "chnroute.txt"
            chn6 = root / "chnroute6.txt"
            chn4.write_text("1.0.1.0/24\n")
            chn6.write_text("2400::/32\n")
            fn_code = functions(SSCONFIG_PATH, "creat_ipset").replace(
                "/koolshare/ss/rules/chnroute.txt", str(chn4)
            ).replace(
                "/koolshare/ss/rules/chnroute6.txt", str(chn6)
            )
            script = f"""
set -e
FSS_FAIL_CLOSED_RESTART=1
ss_basic_mode=1
echo_date() {{ :; }}
ipset() {{ cat; }}

{fn_code}

creat_ipset
"""
            res = run_bash(script)
            self.assertEqual(res.returncode, 0, res.stderr)
            out = res.stdout
            # Issue #1 probe: none of the live sets may be flushed during fail-closed restart.
            for live_set in (
                "ignlist", "ignlist6", "white_list", "white_list6",
                "black_list", "black_list6", "chnlist", "chnlist6",
                "gfwlist", "gfwlist6", "router", "router6",
            ):
                self.assertNotIn(f"flush {live_set}\n", out)
            self.assertIn("swap chnroute chnroute_tmp", out)
            self.assertIn("destroy chnroute_tmp", out)
            self.assertIn("swap chnroute6 chnroute6_tmp", out)
            self.assertIn("destroy chnroute6_tmp", out)

    def test_real_close_in_five_never_calls_disable_ss_during_fail_closed_restart(self):
        # Issue #3 probe: use real close_in_five from ss_base.sh (no mock) with FSS_PARTIAL_RELOAD=0.
        fn_code = (
            functions(BASE_SCRIPT, "close_in_five")
            + "\n"
            + functions(SSCONFIG_PATH, "load_iptables")
        )
        script = f"""
FSS_FAIL_CLOSED_RESTART=1
FSS_PARTIAL_RELOAD=0
echo_date() {{ echo "$*"; }}
iptables() {{ echo "rule"; }}
_start_iptables() {{ return 1; }}
disable_ss() {{ echo "DISABLE_SS_CALLED"; }}
flush_iptables() {{ echo "FLUSH_IPTABLES_CALLED"; }}
flush_ipset() {{ echo "FLUSH_IPSET_CALLED"; }}
unset_lock() {{ echo "LOCK_RELEASED"; }}
sleep() {{ :; }}

{fn_code}

load_iptables
"""
        res = run_bash(script)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertNotIn("DISABLE_SS_CALLED", res.stdout)
        self.assertNotIn("FLUSH_IPTABLES_CALLED", res.stdout)
        self.assertNotIn("FLUSH_IPSET_CALLED", res.stdout)
        self.assertIn("LOCK_RELEASED", res.stdout)
        self.assertIn("Fail-Closed", res.stdout)

    def test_reload_proxy_only_recovers_backup_when_real_close_in_five_exits_and_excludes_obfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xray_conf = root / "xray.json"
            event_log = root / "reload_events.log"
            xray_conf.write_text('{"old": true}\n')
            # PR #43 #9 fix: replace /tmp/ FIRST, then /koolshare/ss/xray.json, so TMPDIR=/tmp never double-rewrites!
            fn_code = (
                functions(BASE_SCRIPT, "close_in_five")
                + "\n"
                + functions(
                    SSCONFIG_PATH,
                    "init_protocol_config_paths",
                    "reload_proxy_only",
                    "add_current_server_ip_to_ignlist",
                    "clear_current_node_server_ip",
                    "record_current_node_server_ip",
                ).replace("/tmp/", str(root) + "/").replace("/koolshare/ss/xray.json", str(xray_conf))
            )

            base_mocks = f"""
ss_basic_status=1
ss_basic_mode=7
ss_basic_type=4
ss_basic_ss_obfs=""
ss_basic_server="node.example.com"
ss_basic_shunt_hot_reload=0
echo_date() {{ :; }}
unset_lock() {{ :; }}
iptables() {{ return 0; }}
fss_base_load_current_node_env() {{ :; }}
refresh_runtime_context() {{ :; }}
resolv_server_ip() {{
    clear_current_node_server_ip
    record_current_node_server_ip "35.213.105.171"
}}
is_domain() {{ return 0; }}
ipset() {{ echo "ipset:$*" >> '{event_log}'; }}
kill_proxy_only() {{ echo "kill_proxy_only" >> '{event_log}'; }}
kill_process() {{ echo "KILL_PROCESS_FORBIDDEN" >> '{event_log}'; }}
flush_iptables() {{ echo "FLUSH_IPTABLES_FORBIDDEN" >> '{event_log}'; }}
flush_ipset() {{ echo "FLUSH_IPSET_FORBIDDEN" >> '{event_log}'; }}
restart_dnsmasq() {{ echo "RESTART_DNSMASQ_FORBIDDEN" >> '{event_log}'; }}
start_xray() {{ echo "start_xray" >> '{event_log}'; return 0; }}
get_proxy_server_ip() {{ :; }}
write_numbers() {{ :; }}
dbus() {{ :; }}
{fn_code}
"""
            # 1) Happy path: uses CURRENT_NODE_SERVER_RESOLVED_IP and leaves iptables/dns untouched.
            res_ok = run_bash(
                base_mocks
                + f'\ncreat_shunt_json() {{ echo \'{{"new": true}}\' > "{xray_conf}"; return 0; }}\nreload_proxy_only\n'
            )
            self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
            ok_events = event_log.read_text()
            self.assertIn("ipset:-! add ignlist 35.213.105.171", ok_events)
            self.assertIn("kill_proxy_only", ok_events)
            self.assertIn("start_xray", ok_events)
            self.assertNotIn("KILL_PROCESS_FORBIDDEN", ok_events)
            self.assertNotIn("FLUSH_IPTABLES_FORBIDDEN", ok_events)
            self.assertNotIn("RESTART_DNSMASQ_FORBIDDEN", ok_events)

            # 2) Issue #5 probe: generator calls REAL close_in_five (exit 1); subshell must restore backup.
            event_log.write_text("")
            xray_conf.write_text('{"old": true}\n')
            res_fail = run_bash(
                base_mocks
                + f'\ncreat_shunt_json() {{ echo "broken" > "{xray_conf}"; close_in_five; }}\nreload_proxy_only\n'
            )
            self.assertNotEqual(res_fail.returncode, 0)
            self.assertNotIn("kill_proxy_only", event_log.read_text())
            self.assertEqual(xray_conf.read_text(), '{"old": true}\n')

            # 3) Issue #6 probe: SS TLS obfs node must bail out of lightweight reload before killing anything.
            event_log.write_text("")
            res_obfs = run_bash(
                base_mocks
                + "\nss_basic_mode=2\nss_basic_type=0\nss_basic_ss_obfs=tls\nreload_proxy_only\n"
            )
            self.assertNotEqual(res_obfs.returncode, 0)
            self.assertNotIn("kill_proxy_only", event_log.read_text())

            # 4) PR #43 #7 probe: standalone non-shunt VLESS reload initializes VLESS_CONFIG_FILE/TEMP without prepare_system.
            event_log.write_text("")
            res_vless = run_bash(
                base_mocks
                + f"""
ss_basic_mode=2
ss_basic_type=4
unset VLESS_CONFIG_FILE VLESS_CONFIG_TEMP
creat_vless_json() {{
    echo "VLESS_PATHS:${{VLESS_CONFIG_FILE}}|${{VLESS_CONFIG_TEMP}}" >> '{event_log}'
    [ -n "${{VLESS_CONFIG_FILE}}" ] && [ -n "${{VLESS_CONFIG_TEMP}}" ]
}}
reload_proxy_only
"""
            )
            self.assertEqual(res_vless.returncode, 0, res_vless.stderr)
            self.assertIn(f"VLESS_PATHS:{xray_conf}|{root}/xray_tmp.json", event_log.read_text())


class SafeFailoverPolicyTests(unittest.TestCase):
    """Issue #42 Phase 3: flock lock check, separator survival, deduped probe recovery, mode 7 hot switch."""

    def _status_harness(self, root):
        fn_code = functions(
            STATUS_MAIN_SCRIPT,
            "failover_now_epoch",
            "failover_state_read",
            "failover_state_write",
            "failover_cooldown_seconds",
            "failover_is_ssconfig_locked",
            "failover_record_probe_health",
            "failover_should_throttle",
            "failover_mark_separator",
            "failover_exec_recovery",
            "failover_action",
            "failover_check_1",
        )
        return f"""
LOGFILE_F='{root}/ssf_status.txt'
FAILOVER_STATE_FILE='{root}/failover_state'
FAILOVER_LOCK_FILE='{root}/koolss.lock'
FAILOVER_SS_LOG_FILE='{root}/ss_log.txt'
FAILOVER_MAX_STREAK=3
FAILOVER_RECOVERY_RESET=3
LOGTIME1='16:00:00'
ss_failover_s1=3
ss_failover_s2_1=15
ss_failover_s2_2=4
ss_failover_s3_1=15
ss_failover_s3_2=1000
LOGM() {{ echo "LOGM:$*"; }}
echo_date() {{ :; }}
fss_get_current_node_id() {{ echo 24; }}
get_node_name_by_id() {{ echo "node-$1"; }}
fss_set_current_node_id() {{ echo "SET_NODE:$1"; }}
fss_set_failover_node_id() {{ echo "SET_BACKUP:$1"; }}
fss_get_failover_node_id() {{ echo 34; }}
fss_get_next_node_id_in_order() {{ echo 24; }}
dbus() {{
    if [ "$1" = "get" ] && [ "$2" = "ss_basic_mode" ]; then
        echo "${{TEST_BASIC_MODE:-2}}"
        return 0
    fi
    echo "DBUS:$*"
}}
run() {{ echo "RUN:$*"; }}
{fn_code}
"""

    def test_default_lock_file_matches_ssconfig_and_stale_file_does_not_block(self):
        # Issue #8 probe: lock file must default to /var/lock/koolss.lock.
        self.assertIn("/var/lock/koolss.lock", STATUS_MAIN_SCRIPT.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale_lock = root / "koolss.lock"
            stale_lock.write_text("stale")
            harness = self._status_harness(root)
            script = harness + """
ps() { echo "1 root sh"; }
if failover_is_ssconfig_locked; then
    echo "LOCKED"
else
    echo "UNLOCKED"
fi
"""
            res = run_bash(script)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), "UNLOCKED")

    def test_separator_blocks_immediate_retrigger_in_real_failover_check_1(self):
        # Issue #9 probe: real failover_check_1 must see separator written by failover_mark_separator.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_f = root / "ssf_status.txt"
            log_f.write_text("16:00:01 timeout\n16:00:02 timeout\n16:00:03 timeout\n")
            harness = self._status_harness(root)
            script = harness + """
failover_action() { echo "RETRIGGERED_FORBIDDEN"; }
failover_mark_separator "restart"
failover_check_1
echo "DONE"
"""
            res = run_bash(script)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("RETRIGGERED_FORBIDDEN", res.stdout)
            self.assertIn("DONE", res.stdout)

    def test_cached_ok_line_not_double_counted_and_three_distinct_probes_reset_streak(self):
        # Issue #10 probe: repeated health checks on the same cached OK line must NOT increment OK_STREAK.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = self._status_harness(root)
            script = harness + """
ss_failover_s4_1=1
sh() { echo "SH:$*"; return 0; }

failover_now_epoch() { echo 1000; }
failover_action 2
failover_now_epoch() { echo 1030; }
failover_action 2
failover_now_epoch() { echo 1061; }
failover_action 2
failover_now_epoch() { echo 1242; }
failover_action 2
failover_now_epoch() { echo 2000; }
failover_action 2

# Single OK line polled 3 times + separator written -> OK_STREAK must stay 1, streak must stay 3.
printf '16:10:01 204 OK\\n' >> "${LOGFILE_F}"
failover_record_probe_health
failover_mark_separator "restart"
failover_record_probe_health
failover_record_probe_health
echo "AFTER_SAME_LINE:$(cat "${FAILOVER_STATE_FILE}")"

# Two more distinct OK lines -> OK_STREAK reaches 3 -> resets streak to 0.
printf '16:10:07 204 OK\\n' >> "${LOGFILE_F}"
failover_record_probe_health
printf '16:10:13 204 OK\\n' >> "${LOGFILE_F}"
failover_record_probe_health
echo "AFTER_3_DISTINCT:$(cat "${FAILOVER_STATE_FILE}")"
"""
            res = run_bash(script)
            self.assertEqual(res.returncode, 0, res.stderr)
            out = res.stdout
            ss_log = (root / "ss_log.txt").read_text()
            self.assertEqual(ss_log.count("SH:/koolshare/ss/ssconfig.sh reload_proxy_only"), 3)
            self.assertIn("处于退避冷却期", out)
            self.assertIn("已达连续上限（3 次），停止循环重启并保持 Fail-Closed 拦截状态", out)
            self.assertNotIn("ssconfig.sh -- stop", out)
            self.assertIn("AFTER_SAME_LINE:1242 3 1 ", out)
            self.assertIn("重置故障转移退避计数", out)
            self.assertIn("AFTER_3_DISTINCT:0 0 0 ", out)

    def test_single_node_failure_holds_fail_closed_instead_of_stopping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = self._status_harness(root)
            script = harness + """
ss_failover_s4_1=2
ss_failover_s4_2=2
fss_get_node_count() { echo 1; }
failover_now_epoch() { echo 1000; }
failover_action 2
"""
            res = run_bash(script)
            self.assertEqual(res.returncode, 0, res.stderr)
            out = res.stdout
            self.assertIn("保持 Fail-Closed 拦截状态并告警", out)
            self.assertNotIn("DBUS:set ss_basic_enable=0", out)
            self.assertNotIn("ssconfig.sh -- stop", out)

    def test_mode7_switch_prefers_hot_switch_default_node_without_restarting_xray(self):
        # Issue #7 probe: mode 7 node switch calls switch_default_node first, keeping xray alive.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_hot = root / "ss_shunt_hot_reload.sh"
            fake_hot.write_text("#!/bin/sh\nexit 0\n")
            fake_hot.chmod(0o755)
            harness = self._status_harness(root).replace(
                "/koolshare/scripts/ss_shunt_hot_reload.sh", str(fake_hot)
            )
            script = harness + f"""
TEST_BASIC_MODE=7
ss_failover_s4_1=2
ss_failover_s4_2=1
sh() {{ echo "SH:$*"; return 0; }}
failover_now_epoch() {{ echo 1000; }}
failover_action 2
"""
            res = run_bash(script)
            self.assertEqual(res.returncode, 0, res.stderr)
            ss_log = (root / "ss_log.txt").read_text()
            self.assertIn(f"SH:{fake_hot} switch_default_node 34", ss_log)
            self.assertNotIn("reload_proxy_only", ss_log)
            self.assertNotIn("ssconfig.sh -- restart", res.stdout)

    def test_hot_switch_default_node_preserves_webrtc_custom_rules_and_other_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rt_conf = root / "xray.json"
            live_conf = root / "live.json"
            calls = root / "calls"
            initial_conf = {
                "outbounds": [{"tag": tag} for tag in ("direct", "proxy24", "proxy34", "proxy99", "api")],
                "inbounds": [{"tag": "socks-in"}, {"tag": "dns_udp_1055"}, {"tag": "tproxy-in"}],
                "routing": {
                    "rules": [
                        {"type": "field", "ruleTag": "fss_in_socks", "inboundTag": ["socks-in"], "outboundTag": "proxy24"},
                        {"type": "field", "inboundTag": ["api-in"], "outboundTag": "api"},
                        {"type": "field", "ruleTag": "fss_in_dns_udp", "inboundTag": ["dns_udp_1055"], "outboundTag": "proxy24"},
                        {"type": "field", "ruleTag": "fss_status_probe", "domain": ["www.google.com"], "outboundTag": "proxy24"},
                        {"type": "field", "ruleTag": "fss_webrtc", "port": "3478", "outboundTag": "proxy24"},
                        # Overlapping custom rules matching probe domain & DNS upstream IP!
                        {"type": "field", "ruleTag": "fss_rule_overlap_probe", "domain": ["www.google.com"], "outboundTag": "direct"},
                        {"type": "field", "ruleTag": "fss_rule_overlap_dns", "ip": ["8.8.8.8"], "outboundTag": "proxy99"},
                        {"type": "field", "ruleTag": "fss_rule_claude", "domain": ["claude.ai"], "outboundTag": "proxy24"},
                        {"type": "field", "ruleTag": "fss_default", "network": "tcp,udp", "outboundTag": "proxy24"},
                    ]
                },
            }
            hot_code = functions(HOT_RELOAD_SCRIPT, "hot_routes_match_runtime", "hot_replace_default_routes", "hot_switch_default_node")
            base_script = f"""
HOT_NEED_DNS_RELOAD_FILE='{root}/need_dns'
XRAY_RUNTIME_CONF='{rt_conf}'
XRAY_API_SERVER='127.0.0.1:10085'
XAPI_TOOL_BIN=list_rules
XRAY_BIN=xray
hot_ensure_state_dir() {{ :; }}
hot_log() {{ echo "LOG:$*"; }}
hot_require_ready() {{ :; }}
hot_node_needs_helper() {{ return 1; }}
hot_ensure_single_target_loaded() {{
    [ "$fault" != dns ] || touch "$HOT_NEED_DNS_RELOAD_FILE"
}}
fss_shunt_store_default_node() {{ echo "DEFAULT:$1"; }}
dbus() {{ :; }}
run() {{ "$@"; }}
sh() {{ echo "SH:$*"; return 1; }}
mv() {{
    case "$fault:$2" in disk:*/next.json) return 1 ;; esac
    command mv "$@"
}}
list_rules() {{
    [ "$1" = routing-list-rule ] && [ "$fault" != list ] || return 1
    jq '{{rules: [.routing.rules[] | {{rule_tag:(.ruleTag // ""), tag:.outboundTag}}]}}' '{live_conf}'
}}
xray() {{
    [ "$#" = 4 ] && [ "$1 $2 $3" = "api adrules --server=$XRAY_API_SERVER" ] || return 1
    jq -r '.routing.rules[-1].outboundTag' "$4" >> '{calls}'
    if [ "$(wc -l < '{calls}')" -eq 1 ]; then
        [ "$fault" != rpc ] || return 1
        [ "$fault" != ignored ] || return 0
        cp "$4" '{live_conf}' && [ "$fault" != timeout ]
    else
        cp "$4" '{live_conf}'
    fi
}}
{hot_code}
"""
            def switch(fault="", node=34):
                return subprocess.run(["sh", "-c", base_script + f"\nfault={fault}\nhot_switch_default_node {node}"],
                                      text=True, capture_output=True, timeout=15)

            def reset():
                rt_conf.write_text(json.dumps(initial_conf))
                live_conf.write_bytes(rt_conf.read_bytes())
                calls.write_text("")

            reset()
            res = switch()
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("DEFAULT:34", res.stdout)
            self.assertEqual(calls.read_text().splitlines(), ["proxy34"])
            self.assertEqual(rt_conf.read_bytes(), live_conf.read_bytes())
            live_rules = json.loads(live_conf.read_text())["routing"]["rules"]

            def xray_first_match(inbound="", domain="", ip="", port=""):
                for r in live_rules:
                    if "inboundTag" in r and inbound not in r["inboundTag"]:
                        continue
                    if "domain" in r and domain not in r["domain"]:
                        continue
                    if "ip" in r and ip not in r["ip"]:
                        continue
                    if "port" in r and str(port) != str(r["port"]):
                        continue
                    return r["outboundTag"]
                return None

            # Overlapping rules exist for www.google.com (direct) and 8.8.8.8 (proxy99):
            # socks-in & dns_udp_1055 MUST STILL match proxy34 FIRST!
            self.assertEqual(xray_first_match(inbound="socks-in", domain="www.google.com"), "proxy34")
            self.assertEqual(xray_first_match(inbound="dns_udp_1055", ip="8.8.8.8"), "proxy34")
            # Custom rules & WebRTC on tproxy-in remain preserved with their original outbounds!
            self.assertEqual(xray_first_match(inbound="tproxy-in", domain="claude.ai"), "proxy24")
            self.assertEqual(xray_first_match(inbound="tproxy-in", port="3478"), "proxy24")
            self.assertEqual(xray_first_match(inbound="tproxy-in", domain="other.example"), "proxy34")

            self.assertEqual([r for r in live_rules if r.get("ruleTag", "").startswith("fss_rule_")],
                             [r for r in initial_conf["routing"]["rules"] if r.get("ruleTag", "").startswith("fss_rule_")])
            self.assertEqual(xray_first_match(inbound="api-in"), "api")
            self.assertEqual(switch(node=24).returncode, 0)
            self.assertEqual(calls.read_text().splitlines(), ["proxy34", "proxy24"])

            for fault, expected_calls in (("disk", []), ("list", []), ("rpc", ["proxy34", "proxy24"]),
                                          ("timeout", ["proxy34", "proxy24"]), ("ignored", ["proxy34", "proxy24"])):
                with self.subTest(fault=fault):
                    reset()
                    result = switch(fault)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertNotIn("DEFAULT:", result.stdout)
                    self.assertEqual(calls.read_text().splitlines(), expected_calls)
                    for path in (rt_conf, live_conf):
                        self.assertEqual(json.loads(path.read_text()), initial_conf)

            for legacy in (False, True):
                with self.subTest(legacy=legacy):
                    reset()
                    changed = json.loads(live_conf.read_text())
                    changed["routing"]["rules"][0].pop("ruleTag")
                    live_conf.write_text(json.dumps(changed))
                    if legacy:
                        rt_conf.write_bytes(live_conf.read_bytes())
                    self.assertNotEqual(switch().returncode, 0)
                    self.assertEqual(calls.read_text(), "")

            reset()
            res = switch("dns")
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("SH:/koolshare/ss/ssconfig.sh reload_dns_process", res.stdout)
            self.assertNotIn("DEFAULT:34", res.stdout)

    def test_numeric_outbound_uses_only_static_sets(self):
        code = functions(HOT_RELOAD_SCRIPT, "hot_bypass_new_outbound_server")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ob = root / "outbound.json"
            for addr, suffix in (("203.0.113.55", ""), ("2001:db8::55", "6")):
                with self.subTest(addr=addr):
                    ob.write_text(json.dumps({"settings": {"vnext": [{"address": addr}]}}))
                    script = code + f"""
FSS_STATIC_WHITE_SNAP_V4='{root}/legacy4'
FSS_STATIC_WHITE_SNAP_V6='{root}/legacy6'
run() {{ "$@"; }}
: > '{root}/ops'
ipset() {{ echo "$*" >> '{root}/ops'; [ "$3" != "$fail_set" ]; }}
hot_bypass_new_outbound_server '{ob}'
"""
                    res = run_bash(script)
                    self.assertEqual(res.returncode, 0, res.stderr)
                    self.assertEqual((root / "ops").read_text().splitlines(), [f"-! add ignlist{suffix} {addr}",
                                                               f"-! add white_static{suffix} {addr}"])
                    self.assertFalse((root / "legacy4").exists())
                    self.assertFalse((root / "legacy6").exists())
                    self.assertNotEqual(run_bash(f"fail_set=white_static{suffix}\n" + script).returncode, 0)

    def test_mode7_fallback_reloads_saved_default(self):
        store = functions(SHUNT_SCRIPT, "fss_shunt_store_default_node", "fss_shunt_get_configured_default_target")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "hot"
            hot.touch(mode=0o755)
            harness = self._status_harness(root).replace("/koolshare/scripts/ss_shunt_hot_reload.sh", str(hot))
            for light_ok, store_ok in ((True, True), (False, True), (False, False)):
                with self.subTest(light_ok=light_ok, store_ok=store_ok):
                    (root / "default").write_text("24")
                    (root / "identity").write_text("id-24")
                    res = run_bash(harness + store + f"""
FSS_SHUNT_DEFAULT_NODE_DBUS_KEY=default
FSS_SHUNT_DEFAULT_NODE_IDENTITY_DBUS_KEY=identity
fss_shunt_target_is_proxy_node() {{ [ "$1" = 34 ]; }}
fss_shunt_target_is_direct() {{ return 1; }}
fss_shunt_target_is_reject() {{ return 1; }}
fss_get_node_identity_by_id() {{ echo "id-$1"; }}
dbus() {{
    case "$1:$2" in
        get:ss_basic_mode) echo 7 ;;
        set:default=34) [ "{int(store_ok)}" = 1 ] && echo 34 > '{root}/default' ;;
        get:*) cat '{root}'/"$2" ;;
        set:*) echo "${{2#*=}}" > '{root}'/"${{2%%=*}}" ;;
    esac
}}
sh() {{
    [ "$1" != '{hot}' ] || return 1
    unset ss_basic_shunt_default_node
    echo "LIGHT:$(fss_shunt_get_configured_default_target)" >> '{root}/trace'
    return {0 if light_ok else 1}
}}
run() {{
    unset ss_basic_shunt_default_node
    echo "FULL:$(fss_shunt_get_configured_default_target)" >> '{root}/trace'
}}
: > '{root}/trace'
failover_exec_recovery switch 34
""")
                    if not store_ok:
                        self.assertNotEqual(res.returncode, 0)
                        self.assertEqual((root / "trace").read_text(), "")
                        self.assertEqual((root / "identity").read_text().strip(), "id-24")
                        self.assertEqual((root / "default").read_text().strip(), "24")
                        continue
                    self.assertEqual(res.returncode, 0, res.stderr)
                    self.assertEqual((root / "identity").read_text().strip(), "id-34")
                    self.assertEqual((root / "trace").read_text().splitlines(),
                                     ["LIGHT:34"] if light_ok else ["LIGHT:34", "FULL:34"])

    def test_reload_proxy_only_failure_falls_back_to_fail_closed_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = self._status_harness(root)
            script = harness + """
ss_failover_s4_1=1
sh() { echo "SH_FAIL:$*"; return 1; }
failover_now_epoch() { echo 1000; }
failover_action 1
"""
            res = run_bash(script)
            self.assertEqual(res.returncode, 0, res.stderr)
            ss_log = (root / "ss_log.txt").read_text()
            self.assertIn("SH_FAIL:/koolshare/ss/ssconfig.sh reload_proxy_only", ss_log)
            self.assertIn("RUN:start-stop-daemon -S -q -b -x /koolshare/ss/ssconfig.sh -- restart", res.stdout)


if __name__ == "__main__":
    unittest.main()
