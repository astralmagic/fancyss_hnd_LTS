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
ip_prefix_hex=0xc0a80a00
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
            self.assertTrue(any("--mark 0xc0a80a00/0xffffff00" in l for l in v4_out_rules))

            idx_black = next(i for i, l in enumerate(v4_rules) if "black_list" in l)
            idx_gfw = next(i for i, l in enumerate(v4_rules) if "gfwlist" in l)
            idx_chnlist = next(i for i, l in enumerate(v4_rules) if "chnlist" in l)
            idx_chnroute = next(i for i, l in enumerate(v4_rules) if "chnroute" in l)
            self.assertLess(idx_black, idx_chnlist)
            self.assertLess(idx_gfw, idx_chnroute)
            self.assertTrue(any("-p udp -j REJECT" in l for l in v4_rules), "must block all overseas UDP, not just 443")
            self.assertTrue(any("-p udp -j REJECT" in l for l in v6_rules), "must block all overseas IPv6 UDP")

        # 2b) PR #43 #1 probe: global mode or global ACL (mode=5) must NOT RETURN on chnlist/chnroute.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "global_acl.log"
            res_global = run_bash(
                fn_code
                + f"""
ss_basic_mode=2
acl_nu="1"
ss_acl_mode_1=5
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
            self.assertFalse(any("chnlist" in l for l in g_rules), "global ACL guard must not RETURN on chnlist")
            self.assertFalse(any("chnroute" in l for l in g_rules), "global ACL guard must not RETURN on chnroute")
            self.assertTrue(any("white_list" in l for l in g_rules))

        # 2c) PR #43 #3 probe: verify_proxy_ingress_rules fails when nat/PREROUTING jump is absent.
        res_verify_fail = run_bash(
            fn_code
            + """
iptables() { return 1; }
verify_proxy_ingress_rules
"""
        )
        self.assertNotEqual(res_verify_fail.returncode, 0)
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
        # PR #43 #4, #5, #8 probes:
        # - atomically swaps ignlist/ignlist6 using single static source (incl. 192.18.0.0/15 & public DNS)
        # - preserves dynamic members in black_list/white_list (NO full swap/flush), prunes only removed static IPs
        # - does NOT double-decode ss_wan_black_ip / ss_wan_white_ip after add_white_black
        # - routes IPv6 ISP_DNS to white_list6 instead of IPv4 white_list
        # - never flushes dynamic gfwlist/chnlist/router sets
        # - propagates ipset -R failure
        import base64
        fn_code = functions(
            SSCONFIG_PATH,
            "fss_static_ignlist_v4",
            "fss_static_ignlist_v6",
            "fss_decode_static_ip_list",
            "fss_collect_current_static_ip_lists",
            "reconcile_fail_closed_ipsets",
            "add_current_server_ip_to_ignlist",
        )
        black_b64 = base64.b64encode(b"198.51.100.20\n2001:db8::20\n").decode()
        white_b64 = base64.b64encode(b"198.51.100.30\n2001:db8::30\n").decode()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captured = root / "ipset_restore.txt"
            snap_b4 = root / "snap_b.v4"
            snap_b6 = root / "snap_b.v6"
            snap_w4 = root / "snap_w.v4"
            snap_w6 = root / "snap_w.v6"
            # Seed previous static snapshots containing removed static entries
            snap_b4.write_text("198.51.100.99\n198.51.100.20\n")
            snap_w4.write_text("198.51.100.88\n198.51.100.30\n")

            res_ok = run_bash(
                fn_code
                + f"""
ss_basic_mode=7
ISP_DNS1="202.96.134.133"
ISP_DNS2="2400:3200::1"
ss_wan_black_ip="{black_b64}"
ss_wan_white_ip="{white_b64}"
CURRENT_NODE_SERVER_RESOLVED_IP="203.0.113.10"
FSS_STATIC_BLACK_SNAP_V4="{snap_b4}"
FSS_STATIC_BLACK_SNAP_V6="{snap_b6}"
FSS_STATIC_WHITE_SNAP_V4="{snap_w4}"
FSS_STATIC_WHITE_SNAP_V6="{snap_w6}"
base64_decode() {{ python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read()).decode())'; }}
dbus() {{ :; }}
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
            # PR #43 #4: black_list & white_list must NOT be flushed/swapped (preserves ChinaDNS dynamic IPs)
            self.assertNotIn("swap black_list", body)
            self.assertNotIn("swap white_list", body)
            self.assertNotIn("flush black_list", body)
            self.assertNotIn("flush white_list", body)
            # Removed static entries must be incrementally deleted, current static entries preserved
            self.assertIn("del black_list 198.51.100.99 -exist", body)
            self.assertIn("del white_list 198.51.100.88 -exist", body)
            self.assertIn("add black_list 198.51.100.20 -exist", body)
            self.assertIn("add black_list6 2001:db8::20 -exist", body)
            self.assertIn("add white_list 198.51.100.30 -exist", body)
            self.assertIn("add white_list6 2001:db8::30 -exist", body)
            # PR #43 #8: IPv6 ISP_DNS2 goes to white_list6, NEVER IPv4 white_list
            self.assertIn("add white_list 202.96.134.133 -exist", body)
            self.assertIn("add white_list6 2400:3200::1 -exist", body)
            self.assertNotIn("add white_list 2400:3200::1", body)
            # Static ignlist single-source completeness
            for required_entry in ("192.18.0.0/15", "223.5.5.5", "114.114.114.114", "1.2.4.8", "180.76.76.76", "119.29.29.29"):
                self.assertIn(f"add ignlist_fc_tmp {required_entry}", body)
            for dynamic_set in ("gfwlist", "gfwlist6", "chnlist", "chnlist6", "router", "router6"):
                self.assertNotIn(f"flush {dynamic_set}", body)

            # PR #43 #5: verify add_white_black does not clobber ss_wan_black_ip / ss_wan_white_ip in place
            awb_src = extract_shell_function(SSCONFIG_PATH, "add_white_black")
            self.assertNotIn("ss_wan_black_ip=$(echo", awb_src)
            self.assertNotIn("ss_wan_white_ip=$(echo", awb_src)

            # Failure propagation probe: ipset -R fails -> reconcile_fail_closed_ipsets MUST return non-zero.
            res_fail = run_bash(
                fn_code
                + f"""
FSS_STATIC_BLACK_SNAP_V4="{snap_b4}"
FSS_STATIC_BLACK_SNAP_V6="{snap_b6}"
FSS_STATIC_WHITE_SNAP_V4="{snap_w4}"
FSS_STATIC_WHITE_SNAP_V6="{snap_w6}"
dbus() {{ :; }}
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
        # Review #1, #4, #5, #6 probes:
        # - verifies production hot_runtime_managed_rule_tags is called (no fake function name)
        # - must NOT call fss_shunt_cleanup_runtime (which stops other nodes' helpers)
        # - must NOT delete fss_webrtc or fss_rule_*
        # - must rollback and return non-zero if removing old fss_default fails
        # - must call ssconfig.sh reload_dns_process when need_dns_reload is set and fail if it fails
        self.assertIn("hot_runtime_managed_rule_tags()", HOT_RELOAD_SCRIPT.read_text())
        self.assertNotIn("hot_list_managed_rule_tags", HOT_RELOAD_SCRIPT.read_text())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rm_log = root / "removed.log"
            rt_conf = root / "xray.json"
            rt_conf.write_text('{"inbounds":[{"tag":"socks-in"},{"tag":"dns_udp_1055"}]}\n')
            hot_code = functions(HOT_RELOAD_SCRIPT, "hot_switch_default_node")
            base_script = f"""
HOT_STATE_DIR='{root}/hot_state'
HOT_LOCK_FILE='{root}/hot.lock'
HOT_NEED_DNS_RELOAD_FILE='{root}/need_dns'
XRAY_RUNTIME_CONF='{rt_conf}'
ss_basic_furl='http://www.google.com/generate_204'
ss_basic_curl='http://connectivitycheck.platform.hicloud.com/generate_204'
run() {{ "$@"; }}
hot_ensure_state_dir() {{ mkdir -p "${{HOT_STATE_DIR}}"; }}
hot_log() {{ echo "LOG:$*"; }}
hot_require_ready() {{ return 0; }}
hot_node_needs_helper() {{ return 1; }}
fss_shunt_cleanup_runtime() {{ echo "CLEANUP_RUNTIME_FORBIDDEN"; }}
hot_build_next_state() {{ echo "BUILD_NEXT_FORBIDDEN"; }}
hot_ensure_single_target_loaded() {{ echo "ensure_single:$1"; return 0; }}
hot_runtime_managed_rule_tags() {{ printf 'fss_status_probe\\nfss_webrtc\\nfss_rule_claude\\nfss_default\\n'; }}
hot_runtime_inbound_rule_tags() {{ printf 'fss_in_socks\\nfss_in_dns_udp\\n'; }}
hot_add_inbound_rule() {{ echo "ADD_IN:$1:$2:$3"; return 0; }}
fss_shunt_host_from_url() {{
    case "$1" in
    *google*) echo "www.google.com" ;;
    *hicloud*) echo "connectivitycheck.platform.hicloud.com" ;;
    esac
}}
hot_add_rule() {{ echo "ADD:$1:$2:$5"; return 0; }}
dbus() {{ echo "DBUS:$*"; }}
killall() {{ echo "KILL_FORBIDDEN"; }}
{hot_code}
"""
            # 1) Happy path: updates socks-in + dns_udp + status_probe + default, preserves webrtc & custom rules
            res = run_bash(
                base_script
                + f"""
hot_remove_rule() {{ echo "REMOVE:$1" >> '{rm_log}'; return 0; }}
hot_switch_default_node 34
"""
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            out = res.stdout
            removed = rm_log.read_text() if rm_log.exists() else ""
            self.assertIn("ensure_single:34", out)
            self.assertNotIn("CLEANUP_RUNTIME_FORBIDDEN", out)
            self.assertNotIn("BUILD_NEXT_FORBIDDEN", out)
            self.assertNotIn("KILL_FORBIDDEN", out)
            self.assertIn('ADD_IN:fss_in_socks_s_', out)
            self.assertIn('ADD_IN:fss_in_dns_udp_s_', out)
            self.assertIn("ADD:fss_default_s_", out)
            self.assertIn("ADD:fss_status_probe_s_", out)
            self.assertIn("REMOVE:fss_in_socks", removed)
            self.assertIn("REMOVE:fss_in_dns_udp", removed)
            self.assertIn("REMOVE:fss_default", removed)
            self.assertIn("REMOVE:fss_status_probe", removed)
            self.assertNotIn("REMOVE:fss_webrtc", removed)
            self.assertNotIn("REMOVE:fss_rule_claude", removed)

            # 1b) PR #43 #6 probe: missing fss_in_socks tag (legacy static binding) MUST fail & fallback.
            res_no_socks_tag = run_bash(
                base_script
                + """
hot_runtime_inbound_rule_tags() { printf ''; }
hot_remove_rule() { return 0; }
hot_switch_default_node 34
"""
            )
            self.assertNotEqual(res_no_socks_tag.returncode, 0)
            self.assertNotIn("ADD:fss_default_s_", res_no_socks_tag.stdout)

            # 2) Fault injection: deleting old fss_default fails -> must rollback ALL new rules and return non-zero.
            rm_log.write_text("")
            res_rm_fail = run_bash(
                base_script
                + f"""
hot_remove_rule() {{
    echo "REMOVE:$1" >> '{rm_log}'
    [ "$1" = "fss_default" ] && return 1
    return 0
}}
hot_switch_default_node 34
"""
            )
            self.assertNotEqual(res_rm_fail.returncode, 0)
            rm_fail_lines = rm_log.read_text()
            self.assertIn("REMOVE:fss_in_socks_s_", rm_fail_lines)
            self.assertIn("REMOVE:fss_in_dns_udp_s_", rm_fail_lines)
            self.assertIn("REMOVE:fss_default_s_", rm_fail_lines)
            self.assertIn("REMOVE:fss_status_probe_s_", rm_fail_lines)

            # 3) Fault injection: need_dns_reload set and reload_dns_process fails -> must return non-zero.
            fake_ssconfig = root / "ssconfig.sh"
            fake_ssconfig.write_text("#!/bin/sh\nexit 1\n")
            fake_ssconfig.chmod(0o755)
            sh_log = root / "sh_calls.log"
            dns_script = base_script.replace("/koolshare/ss/ssconfig.sh", str(fake_ssconfig))
            res_dns_fail = run_bash(
                dns_script
                + f"""
hot_ensure_single_target_loaded() {{ touch "${{HOT_NEED_DNS_RELOAD_FILE}}"; return 0; }}
hot_remove_rule() {{ return 0; }}
sh() {{ echo "SH:$*" >> '{sh_log}'; return 1; }}
hot_switch_default_node 34
"""
            )
            self.assertNotEqual(res_dns_fail.returncode, 0)
            self.assertIn("reload_dns_process", sh_log.read_text())

            # 4) Review #1 probe: REAL hot_runtime_managed_rule_tags with failing API -> must fail BEFORE adding rule.
            real_enum_code = functions(
                HOT_RELOAD_SCRIPT,
                "hot_list_all_runtime_rule_tags",
                "hot_runtime_managed_rule_tags",
                "hot_runtime_inbound_rule_tags",
                "hot_switch_default_node",
            )
            res_api_fail = run_bash(
                f"""
HOT_STATE_DIR='{root}/hot_state'
HOT_LOCK_FILE='{root}/hot.lock'
HOT_NEED_DNS_RELOAD_FILE='{root}/need_dns'
XAPI_TOOL_BIN='xapi-tool'
XRAY_API_SERVER='127.0.0.1:10085'
hot_ensure_state_dir() {{ mkdir -p "${{HOT_STATE_DIR}}"; }}
hot_log() {{ echo "LOG:$*"; }}
hot_require_ready() {{ return 0; }}
hot_node_needs_helper() {{ return 1; }}
hot_ensure_single_target_loaded() {{ return 0; }}
run() {{ return 1; }}
hot_add_rule() {{ echo "ADD_FORBIDDEN:$1"; return 0; }}
hot_add_inbound_rule() {{ echo "ADD_IN_FORBIDDEN:$1"; return 0; }}
{real_enum_code}
hot_switch_default_node 34
"""
            )
            self.assertNotEqual(res_api_fail.returncode, 0)
            self.assertNotIn("ADD_FORBIDDEN", res_api_fail.stdout)
            self.assertNotIn("ADD_IN_FORBIDDEN", res_api_fail.stdout)

            # 5) Review #3 probe: hot_bypass_new_outbound_server adds node IP to ignlist & white_list + records snapshot.
            bypass_code = functions(HOT_RELOAD_SCRIPT, "hot_bypass_new_outbound_server")
            ob_file = root / "ob.json"
            ob_file.write_text('{"settings":{"vnext":[{"address":"203.0.113.55"}]}}')
            ipset_log = root / "bypass_ipset.log"
            white_snap = root / "snap_white.v4"
            res_bypass = run_bash(
                f"""
FSS_STATIC_WHITE_SNAP_V4='{white_snap}'
run() {{ "$@"; }}
ipset() {{ echo "ipset:$*" >> '{ipset_log}'; return 0; }}
{bypass_code}
hot_bypass_new_outbound_server '{ob_file}'
"""
            )
            self.assertEqual(res_bypass.returncode, 0, res_bypass.stderr)
            bypass_ops = ipset_log.read_text()
            self.assertIn("ipset:-! add ignlist 203.0.113.55", bypass_ops)
            self.assertIn("ipset:-! add white_list 203.0.113.55", bypass_ops)
            self.assertIn("203.0.113.55", white_snap.read_text())

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
