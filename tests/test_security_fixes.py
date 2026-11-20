#!/usr/bin/env python3
import base64
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extract_shell_fn(rel_path: str, *names: str) -> str:
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    out = []
    for name in names:
        m = re.search(rf"^{re.escape(name)}\s*\(\)\s*\{{", text, re.M)
        if not m:
            raise AssertionError(f"Function {name} not found in {rel_path}")
        start = m.start()
        depth = 0
        pos = m.end() - 1
        while pos < len(text):
            ch = text[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out.append(text[start : pos + 1])
                    break
            pos += 1
    return "\n\n".join(out) + "\n"


def extract_js_fn(rel_path: str, *names: str) -> str:
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    out = []
    for name in names:
        m = re.search(rf"^function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", text, re.M)
        if not m:
            raise AssertionError(f"JS function {name} not found in {rel_path}")
        start = m.start()
        depth = 0
        pos = m.end() - 1
        while pos < len(text):
            ch = text[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out.append(text[start : pos + 1])
                    break
            pos += 1
    return "\n\n".join(out) + "\n"


class SecurityRegressionTests(unittest.TestCase):
    def test_html_escape_escapes_single_quotes_and_tags(self):
        js = extract_js_fn("fancyss/webs/Module_shadowsocks.asp", "htmlEscape", "escape_acl_html")
        js += """
const raw = `<img src=x onerror='alert("1")'>&`;
const e1 = htmlEscape(raw);
const e2 = escape_acl_html(raw);
if (e1.includes('<') || e1.includes('>') || e1.includes('"') || e1.includes("'")) throw Error("htmlEscape failed: " + e1);
if (e2.includes('<') || e2.includes('>') || e2.includes('"') || e2.includes("'")) throw Error("escape_acl_html failed: " + e2);
"""
        subprocess.run(["node", "-e", js], check=True)

    def test_node_table_and_options_escape_untrusted_fields(self):
        asp = (ROOT / "fancyss/webs/Module_shadowsocks.asp").read_text(encoding="utf-8")
        self.assertNotIn("option3.append('<option value=\"' + field + '\">' + c[\"name\"] + '</option>');", asp)
        self.assertNotIn("html += '<div class=\"nickname\">' + c[\"name\"] + '</div>';", asp)
        self.assertNotIn("html += '<div class=\"server\">' + c[\"server\"] + '</div>';", asp)

    def test_websocket_rejects_arbitrary_commands_and_non_lan(self):
        ws_text = (ROOT / "fancyss/ss/websocket").read_text(encoding="utf-8")
        self.assertNotIn("cmd $MSG", ws_text)
        self.assertNotIn('echo "$(env)"', ws_text)
        self.assertIn("is_lan_remote_addr", ws_text)

    def test_obfs_start_script_escapes_single_quotes_and_validates_ports(self):
        fns = extract_shell_fn("fancyss/ss/ssconfig.sh", "ss_shell_quote_sq", "write_ss_obfs_start_script")
        with tempfile.TemporaryDirectory() as tmp:
            script_out = os.path.join(tmp, "start-obfs.sh")
            marker = os.path.join(tmp, "pwned")
            sh_cmd = fns + f"""
write_ss_obfs_start_script "{script_out}" "1.2.3.4" "443" "1080" "tls" "a'; touch '{marker}'; #" ""
sh -n "{script_out}"
"""
            res = subprocess.run(["sh", "-c", sh_cmd], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse(os.path.exists(marker))
            # Non-numeric port must be rejected
            bad = subprocess.run(
                ["sh", "-c", fns + f'write_ss_obfs_start_script "{script_out}" "1.2.3.4" "443;id" "1080" "tls" "" ""'],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(bad.returncode, 0)

    def test_legacy_backup_capture_never_executes_shell_commands(self):
        fns = extract_shell_fn("fancyss/scripts/ss_node_common.sh", "fss_capture_legacy_backup_sh")
        with tempfile.TemporaryDirectory() as tmp:
            backup = os.path.join(tmp, "ssconf_backup.sh")
            out = os.path.join(tmp, "out.kv")
            marker = os.path.join(tmp, "executed")
            Path(backup).write_text(
                f'#!/bin/sh\ntouch "{marker}"\ndbus set ss_basic_enable="1"\ndbus set ssconf_basic_name_1="node$(touch {marker})"\n',
                encoding="utf-8",
            )
            res = subprocess.run(
                ["sh", "-c", fns + f'fss_capture_legacy_backup_sh "{backup}" "{out}"'],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse(os.path.exists(marker))
            captured = Path(out).read_text(encoding="utf-8")
            self.assertIn("ss_basic_enable=1", captured)

    def test_download_resv_log_blocks_path_traversal(self):
        fns = extract_shell_fn("fancyss/scripts/ss_conf.sh", "download_resv_log")
        script = fns + """
echo_date() { :; }
dbus() { echo "../../etc/passwd"; }
rm() { :; }
mkdir() { :; }
ln() { :; }
cp() { echo "CP_CALLED"; return 0; }
download_resv_log
"""
        res = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("CP_CALLED", res.stdout)

    def test_acl_index_rejects_non_numeric_before_eval(self):
        fns = extract_shell_fn("fancyss/scripts/ss_base.sh", "resolve_acl_mac", "is_acl_rule_complete")
        script = fns + """
is_acl_rule_complete "1;echo_INJECTED" && exit 10
resolve_acl_mac "1;echo_INJECTED" && exit 11
exit 0
"""
        res = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("INJECTED", res.stdout)

    def test_json_escape_helpers_neutralize_quotes_and_newlines(self):
        fns = extract_shell_fn("fancyss/ss/ssconfig.sh", "json_escape_str", "get_value_null", "get_value_empty")
        script = fns + """
val=$(get_value_null 'a","injected":true,"b')
printf '%s' "$val" | jq -e 'type == "string"' >/dev/null
"""
        res = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_cron_validators_reject_injection(self):
        fns = extract_shell_fn("fancyss/ss/ssconfig.sh", "is_cron_int_range", "is_cron_hour_csv")
        script = fns + """
is_cron_int_range "12" 0 23 || exit 1
is_cron_int_range "12 * * * * /bin/sh" 0 23 && exit 2
is_cron_hour_csv "3,8,12" || exit 3
is_cron_hour_csv "3,8;id" && exit 4
is_cron_hour_csv "25" && exit 5
exit 0
"""
        res = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_ci_workflow_has_no_raw_inputs_tools_interpolation(self):
        wf = (ROOT / ".github/workflows/build-zig-tools.yml").read_text(encoding="utf-8")
        self.assertNotIn("${{ inputs.tools ||", wf)
        self.assertIn("TOOLS_INPUT: ${{ inputs.tools }}", wf)
        self.assertIn("VALIDATED_TARGETS", wf)
        self.assertNotIn("actions/checkout@v4", wf)

    def test_update_and_download_scripts_verify_tls_by_default(self):
        for rel in (
            "fancyss/scripts/ss_update.sh",
            "fancyss/scripts/ss_xray.sh",
            "fancyss/scripts/ss_dns_test.sh",
        ):
            content = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("--no-check-certificate", content, rel)
            self.assertNotIn("curl-update -4sk", content, rel)
            self.assertNotIn("curl-update -4k", content, rel)
        update_sh = (ROOT / "fancyss/scripts/ss_update.sh").read_text(encoding="utf-8")
        self.assertIn("tar -ztf shadowsocks.tar.gz", update_sh)


if __name__ == "__main__":
    unittest.main()
