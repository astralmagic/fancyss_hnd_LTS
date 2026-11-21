import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


def run_webrtc_case(
    env_overrides: dict,
    ip6_supported: bool = True,
    udp_supported: bool = True,
    ipv6_proxy: bool = False,
    mangle_val: str = "1",
) -> tuple[list[str], list[str], str]:
    ssconfig = ROOT / "fancyss/ss/ssconfig.sh"
    fns = "\n\n".join(
        extract_shell_function(ssconfig, fn)
        for fn in [
            "ensure_chain",
            "ensure_chain6",
            "ensure_chain_with_cmd",
            "append_if_not_exists",
            "append_if_not_exists6",
            "append_if_not_exists_with_cmd",
            "get_webrtc_block_ports_mode",
            "get_webrtc_block_action",
            "apply_webrtc_block4",
            "apply_webrtc_block6",
        ]
    )
    ip6_check = "return 0" if ip6_supported else "return 1"
    udp_check = "return 0" if udp_supported else "return 1"
    v6_proxy_check = "return 0" if ipv6_proxy else "return 1"
    with tempfile.TemporaryDirectory() as tmp:
        log_file = Path(tmp) / "calls.log"
        script = f"""
{fns}
mangle="{mangle_val}"
proxy_core_supports_udp() {{ {udp_check}; }}
ipv6_proxy_enabled() {{ {v6_proxy_check}; }}
get_tproxy_port6() {{ echo "3334"; }}
echo_date() {{ printf 'LOG:%s\\n' "$*" >> "{log_file}"; }}
iptables() {{
    case "$*" in
        *" -C "*|*" -L "*) return 1 ;;
    esac
    printf 'V4:%s\\n' "$*" >> "{log_file}"
    return 0
}}
ip6tables() {{
    case "$*" in
        "-t filter -L FORWARD"|"-t mangle -L PREROUTING") {ip6_check} ;;
        *" -C "*|*" -L "*) return 1 ;;
    esac
    printf 'V6:%s\\n' "$*" >> "{log_file}"
    return 0
}}
apply_webrtc_block4
apply_webrtc_block6
"""
        env = os.environ.copy()
        env.update(env_overrides)
        subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=True)
        content = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    v4, v6, logs = [], [], []
    for line in content.splitlines():
        if line.startswith("V4:"):
            v4.append(line[3:])
        elif line.startswith("V6:"):
            v6.append(line[3:])
        elif line.startswith("LOG:"):
            logs.append(line[4:])
    return v4, v6, "\n".join(logs)


class WebRTCBlockTests(unittest.TestCase):
    def test_disabled_installs_nothing(self):
        v4, v6, logs = run_webrtc_case(
            {
                "ss_basic_block_webrtc": "0",
                "ss_basic_block_webrtc_action": "reject",
                "ss_basic_block_webrtc_ports": "common",
                "ss_basic_block_webrtc_ipv6": "1",
            }
        )
        self.assertEqual(v4, [])
        self.assertEqual(v6, [])
        self.assertEqual(logs, "")

    def test_common_ports_reject_ipv4_and_ipv6(self):
        v4, v6, _ = run_webrtc_case(
            {
                "ss_basic_block_webrtc": "1",
                "ss_basic_block_webrtc_action": "reject",
                "ss_basic_block_webrtc_ports": "common",
                "ss_basic_block_webrtc_ipv6": "1",
            }
        )
        self.assertIn("-t filter -N SHADOWSOCKS_WEBRTC", v4)
        self.assertIn("-t filter -A SHADOWSOCKS_WEBRTC -p udp -m set --match-set ignlist dst -j RETURN", v4)
        self.assertIn(
            "-t filter -A SHADOWSOCKS_WEBRTC -p udp -m multiport --dport 3478:3481,5349,19302:19309 -j REJECT --reject-with icmp-port-unreachable",
            v4,
        )
        self.assertIn("-t filter -I FORWARD 1 -i br+ -p udp -j SHADOWSOCKS_WEBRTC", v4)

        self.assertIn("-t filter -N SHADOWSOCKS6_WEBRTC", v6)
        self.assertIn("-t filter -A SHADOWSOCKS6_WEBRTC -p udp -m set --match-set ignlist6 dst -j RETURN", v6)
        self.assertIn("-t filter -A SHADOWSOCKS6_WEBRTC -p udp -d fc00::/7 -j RETURN", v6)
        self.assertIn("-t filter -A SHADOWSOCKS6_WEBRTC -p udp -d ff00::/8 -j RETURN", v6)
        self.assertIn(
            "-t filter -A SHADOWSOCKS6_WEBRTC -p udp -m multiport --dport 3478:3481,5349,19302:19309 -j REJECT --reject-with icmp6-port-unreachable",
            v6,
        )
        self.assertIn("-t filter -I FORWARD 1 -i br+ -p udp -j SHADOWSOCKS6_WEBRTC", v6)

    def test_all_ports_reject_exempts_infra(self):
        v4, v6, _ = run_webrtc_case(
            {
                "ss_basic_block_webrtc": "1",
                "ss_basic_block_webrtc_action": "reject",
                "ss_basic_block_webrtc_ports": "all",
                "ss_basic_block_webrtc_ipv6": "1",
            }
        )
        self.assertIn("-t filter -A SHADOWSOCKS_WEBRTC -p udp -m multiport --dport 53,67:68,123 -j RETURN", v4)
        self.assertIn("-t filter -A SHADOWSOCKS_WEBRTC -p udp -j REJECT --reject-with icmp-port-unreachable", v4)

        self.assertIn("-t filter -A SHADOWSOCKS6_WEBRTC -p udp -m multiport --dport 53,123,546:547 -j RETURN", v6)
        self.assertIn("-t filter -A SHADOWSOCKS6_WEBRTC -p udp -j REJECT --reject-with icmp6-port-unreachable", v6)

    def test_proxy_action_common_ports_ipv4_and_ipv6(self):
        v4, v6, _ = run_webrtc_case(
            {
                "ss_basic_block_webrtc": "1",
                "ss_basic_block_webrtc_action": "proxy",
                "ss_basic_block_webrtc_ports": "common",
                "ss_basic_block_webrtc_ipv6": "1",
            },
            ipv6_proxy=True,
        )
        self.assertIn("-t mangle -N SHADOWSOCKS_WEBRTC", v4)
        self.assertIn(
            "-t mangle -A SHADOWSOCKS_WEBRTC -p udp -m multiport --dport 3478:3481,5349,19302:19309 -j TPROXY --on-port 3333 --tproxy-mark 0x07",
            v4,
        )
        self.assertIn("-t mangle -I PREROUTING 1 -i br+ -p udp -j SHADOWSOCKS_WEBRTC", v4)

        self.assertIn("-t mangle -N SHADOWSOCKS6_WEBRTC", v6)
        self.assertIn(
            "-t mangle -A SHADOWSOCKS6_WEBRTC -p udp -m multiport --dport 3478:3481,5349,19302:19309 -j TPROXY --on-port 3334 --tproxy-mark 0x07",
            v6,
        )
        self.assertIn("-t mangle -I PREROUTING 1 -i br+ -p udp -j SHADOWSOCKS6_WEBRTC", v6)

    def test_proxy_action_all_ports_exempts_infra(self):
        v4, v6, _ = run_webrtc_case(
            {
                "ss_basic_block_webrtc": "1",
                "ss_basic_block_webrtc_action": "proxy",
                "ss_basic_block_webrtc_ports": "all",
                "ss_basic_block_webrtc_ipv6": "1",
            },
            ipv6_proxy=True,
        )
        self.assertIn("-t mangle -A SHADOWSOCKS_WEBRTC -p udp -m multiport --dport 53,67:68,123 -j RETURN", v4)
        self.assertIn("-t mangle -A SHADOWSOCKS_WEBRTC -p udp -j TPROXY --on-port 3333 --tproxy-mark 0x07", v4)

        self.assertIn("-t mangle -A SHADOWSOCKS6_WEBRTC -p udp -m multiport --dport 53,123,546:547 -j RETURN", v6)
        self.assertIn("-t mangle -A SHADOWSOCKS6_WEBRTC -p udp -j TPROXY --on-port 3334 --tproxy-mark 0x07", v6)

    def test_proxy_action_falls_back_to_reject_when_udp_unsupported(self):
        v4, v6, logs = run_webrtc_case(
            {
                "ss_basic_block_webrtc": "1",
                "ss_basic_block_webrtc_action": "proxy",
                "ss_basic_block_webrtc_ports": "common",
                "ss_basic_block_webrtc_ipv6": "1",
            },
            udp_supported=False,
            ipv6_proxy=True,
        )
        self.assertIn("-t filter -I FORWARD 1 -i br+ -p udp -j SHADOWSOCKS_WEBRTC", v4)
        self.assertIn("-t filter -I FORWARD 1 -i br+ -p udp -j SHADOWSOCKS6_WEBRTC", v6)
        self.assertIn("已自动回退为直接拦截（reject）", logs)

    def test_proxy_action_ipv6_falls_back_to_reject_when_ipv6_proxy_disabled(self):
        v4, v6, logs = run_webrtc_case(
            {
                "ss_basic_block_webrtc": "1",
                "ss_basic_block_webrtc_action": "proxy",
                "ss_basic_block_webrtc_ports": "common",
                "ss_basic_block_webrtc_ipv6": "1",
            },
            ipv6_proxy=False,
        )
        self.assertIn("-t mangle -I PREROUTING 1 -i br+ -p udp -j SHADOWSOCKS_WEBRTC", v4)
        self.assertIn("-t filter -I FORWARD 1 -i br+ -p udp -j SHADOWSOCKS6_WEBRTC", v6)
        self.assertIn("未开启IPv6代理或当前核心不支持UDP", logs)

    def test_ipv6_option_disabled_skips_ip6tables(self):
        v4, v6, _ = run_webrtc_case(
            {
                "ss_basic_block_webrtc": "1",
                "ss_basic_block_webrtc_action": "reject",
                "ss_basic_block_webrtc_ports": "common",
                "ss_basic_block_webrtc_ipv6": "0",
            }
        )
        self.assertTrue(len(v4) > 0)
        self.assertEqual(v6, [])

    def test_ip6tables_unsupported_skips_gracefully(self):
        v4, v6, logs = run_webrtc_case(
            {
                "ss_basic_block_webrtc": "1",
                "ss_basic_block_webrtc_action": "reject",
                "ss_basic_block_webrtc_ports": "common",
                "ss_basic_block_webrtc_ipv6": "1",
            },
            ip6_supported=False,
        )
        self.assertTrue(len(v4) > 0)
        self.assertEqual(v6, [])
        self.assertIn("跳过IPv6 WebRTC防泄漏规则", logs)

    def test_shunt_webrtc_route_and_outbound_emission(self):
        shunt_sh = ROOT / "fancyss/scripts/ss_node_shunt.sh"
        fns = "\n\n".join(
            extract_shell_function(shunt_sh, fn)
            for fn in [
                "fss_shunt_get_webrtc_proxy_target",
                "fss_shunt_emit_webrtc_route_rule",
                "fss_shunt_emit_outbounds_json",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "outbounds"
            out_dir.mkdir()
            (out_dir / "1_outbounds.json").write_text('{"tag":"proxy1"}', encoding="utf-8")
            (out_dir / "12_outbounds.json").write_text('{"tag":"proxy12"}', encoding="utf-8")
            target_file = Path(tmp) / "targets.txt"
            target_file.write_text("", encoding="utf-8")

            rule_file = Path(tmp) / "rule.json"
            outs_file = Path(tmp) / "outs.json"
            script = f"""
{fns}
FSS_SHUNT_RUNTIME_OUTBOUND_DIR="{out_dir}"
FSS_SHUNT_RUNTIME_TARGET_FILE="{target_file}"
fss_shunt_node_supported() {{ [ "$1" = "1" ] || [ "$1" = "12" ]; }}
fss_shunt_get_effective_default_target() {{ echo "1"; }}
fss_shunt_target_is_special() {{ return 1; }}
fss_shunt_log() {{ :; }}
fss_shunt_emit_webrtc_route_rule 1 > "{rule_file}"
printf '[%s]' "$(fss_shunt_emit_outbounds_json 1)" > "{outs_file}"
"""
            # 1. Specific node 12 + common ports
            env = os.environ.copy()
            env.update(
                {
                    "ss_basic_block_webrtc": "1",
                    "ss_basic_block_webrtc_action": "proxy",
                    "ss_basic_block_webrtc_node": "12",
                    "ss_basic_block_webrtc_ports": "common",
                }
            )
            subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=True)
            rule_obj = json.loads(rule_file.read_text(encoding="utf-8"))
            self.assertEqual(rule_obj["ruleTag"], "fss_webrtc")
            self.assertEqual(rule_obj["outboundTag"], "proxy12")
            self.assertEqual(rule_obj["port"], "3478-3481,5349,19302-19309")
            out_tags = [o["tag"] for o in json.loads(outs_file.read_text(encoding="utf-8"))]
            self.assertIn("proxy12", out_tags)

            # 2. Empty node (follow current) + all ports
            env["ss_basic_block_webrtc_node"] = ""
            env["ss_basic_block_webrtc_ports"] = "all"
            subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=True)
            rule_obj = json.loads(rule_file.read_text(encoding="utf-8"))
            self.assertEqual(rule_obj["outboundTag"], "proxy1")
            self.assertEqual(rule_obj["network"], "udp")
            self.assertNotIn("port", rule_obj)

            # 3. Action = reject -> no rule
            env["ss_basic_block_webrtc_action"] = "reject"
            subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(rule_file.read_text(encoding="utf-8").strip(), "")

    def test_ui_and_hint_wired(self):
        asp = (ROOT / "fancyss/webs/Module_shadowsocks.asp").read_text(encoding="utf-8")
        menu = (ROOT / "fancyss/res/ss-menu.js").read_text(encoding="utf-8")
        for token in [
            '"ss_basic_block_webrtc_action"',
            '"ss_basic_block_webrtc_node"',
            '"ss_basic_block_webrtc_ports"',
            '"ss_basic_block_webrtc"',
            '"ss_basic_block_webrtc_ipv6"',
            "ss_basic_block_webrtc_action_row",
            "ss_basic_block_webrtc_node_row",
            "ss_basic_block_webrtc_ports_row",
            "ss_basic_block_webrtc_ipv6_row",
            "option_webrtc_action",
            "option_webrtc_node",
            "option_webrtc_ports",
            "refresh_webrtc_node_options",
        ]:
            self.assertIn(token, asp)
        self.assertIn("itemNum == 157", menu)
        self.assertIn("通过节点转发", menu)


if __name__ == "__main__":
    unittest.main()
