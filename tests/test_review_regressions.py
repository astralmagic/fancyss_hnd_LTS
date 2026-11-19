"""Regressions for the P0-P2 packaging review (real shell/JS entry points)."""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

from test_p2_regressions import ROOT, create_mock_dbus, run_shell, shell_function, shell_functions, write_executable


def js_functions(*names):
    source = (ROOT / "fancyss/webs/Module_shadowsocks.asp").read_text()
    return "\n".join(re.search(r"^function " + name + r"\(.*?^\}", source, re.M | re.S).group() for name in names)


class LocalImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "db"
        create_mock_dbus(self.root, self.db)
        self.prefix = f'source "{ROOT}/fancyss/scripts/ss_subscribe_profile_lib.sh"\n'
        self.prefix += shell_function("fancyss/scripts/ss_node_subscribe.sh", "subprof_create_local_import_profile") + "\n"
        self.profile_id = self.run_script('subprof_create_local_import_profile "Local"').stdout.strip()
        self.profile = self.read_profile()

    def run_script(self, script, ok=True):
        result = run_shell("bash", self.prefix + script, PATH=f"{self.root}:{os.environ['PATH']}")
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def read_profile(self):
        db = dict(line.split("=", 1) for line in self.db.read_text().splitlines())
        return json.loads(base64.b64decode(db["ss_subprof_" + self.profile_id]))

    def save(self, payload, ok=True):
        return self.run_script("subprof_write_profile_json " + shlex.quote(json.dumps(payload)), ok)

    def test_local_nodes_visible_but_cannot_sync_in_ui(self):
        self.assertTrue(self.profile["enabled"])
        self.assertFalse(self.profile["schedule"]["enabled"])
        script = js_functions("subscription_profile_is_enabled", "subscription_profile_can_sync", "get_subscription_profile_for_node", "schema2_node_is_effective_for_ui")
        script += f"\nvar p={json.dumps(self.profile)}, subscribeProfileMap={{[p.id]:p}}, subscribeProfileScopeMap={{}};"
        script += "\nvar n={_source:'subscribe',_profile_id:p.id}; console.log(JSON.stringify([schema2_node_is_effective_for_ui(n),subscription_profile_can_sync(p)])); p.enabled=false; console.log(schema2_node_is_effective_for_ui(n));"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ["[true,false]", "false"])

    def test_edit_enable_legacy_local_and_convert_to_online(self):
        self.profile["enabled"] = False
        self.save(self.profile)
        self.profile.update(enabled=True, name="Renamed")
        self.profile["schedule"]["enabled"] = True
        self.save(self.profile)
        saved = self.read_profile()
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["name"], "Renamed")
        self.assertFalse(saved["schedule"]["enabled"])
        # All three online consumers must omit local profiles even if enabled.
        result = self.run_script(f'''subprof_profile_exec_tsv {self.profile_id}
subprof_profile_schedule_tsv {self.profile_id}
subprof_count_profile_nodes_fast() {{ echo 1; }}
subprof_collect_enabled_profiles_tsv "{self.root}/rows"
cat "{self.root}/rows"
''')
        self.assertEqual(result.stdout, "")
        self.profile["url"] = "https://example.invalid/sub"
        self.save(self.profile)
        result = self.run_script(f"subprof_profile_exec_tsv {self.profile_id}\nsubprof_profile_schedule_tsv {self.profile_id}")
        self.assertIn(self.profile["url"], result.stdout)
        self.assertIn("true\ttrue\t", result.stdout)
        # An online profile cannot accidentally be converted by clearing its URL.
        self.assertNotEqual(self.save(dict(self.profile, url=""), ok=False).returncode, 0)

    def test_disabled_online_profiles_do_not_execute(self):
        self.save(dict(self.profile, url="https://example.invalid/sub", enabled=False))
        self.assertEqual(self.run_script(f"subprof_profile_exec_tsv {self.profile_id}").stdout, "")

    def test_local_profile_never_creates_cron_job(self):
        # Even a stale local profile with schedule.enabled=true must not run.
        self.profile["schedule"]["enabled"] = True
        self.run_script("subprof_dbus_set_json_by_key ss_subprof_" + self.profile_id + " " + shlex.quote(json.dumps(self.profile)))
        write_executable(self.root / "cru", '#!/bin/sh\necho "$*" >> "' + str(self.root / "cron.log") + '"\n')
        self.run_script(f'''subprof_clear_cron_jobs() {{ :; }}
SUBPROF_CRU_BIN="{self.root}/cru"
subprof_rebuild_cron_jobs
''')
        self.assertFalse((self.root / "cron.log").exists())

    def test_new_blank_profile_rejected(self):
        payload = dict(self.profile, id="abcdef123456")
        self.assertNotEqual(self.save(payload, ok=False).returncode, 0)

    def test_sync_all_with_only_local_profiles_never_clears_nodes(self):
        fns = shell_function("fancyss/scripts/ss_node_subscribe.sh", "start_node_subscribe")
        result = self.run_script(fns + '''
echo_date() { :; }
sub_refresh_node_state() { :; }
sub_reset_active_profile_context() { :; }
sub_get_online_urls() { echo LEGACY_FALLBACK; }
remove_sub_node() { echo DELETED_NODES; }
start_node_subscribe
''')
        self.assertEqual(result.stdout, "")

    def test_sync_all_keeps_local_sources_and_prunes_real_orphans(self):
        online = dict(self.profile, id="abcdef123456", url="https://example.invalid/sub")
        self.save(online)
        fixture = self.root / "fixture"
        fixture.mkdir()
        for tag, profile_id in [("import", self.profile_id), ("online", online["id"]), ("orphan", "ffffffffffff")]:
            (fixture / (tag + ".json")).write_text(json.dumps({"name":tag, "_source":"subscribe", "_profile_id":profile_id, "group":"test_"+tag}) + "\n")
        fns = shell_functions("fancyss/scripts/ss_node_subscribe.sh", "start_node_subscribe", "sub_prepare_enabled_profiles_file", "sub_preserve_unsynced_profile_sources", "sub_mark_active_source_tag", "remove_null")
        result = self.run_script(fns + f'''
DIR="{self.root}/work"
ACTIVE_SOURCE_TAGS="$DIR/active_tags"
LOCAL_SPLIT_META="$DIR/meta"
LOCAL_SPLIT_META_VALID=1
SUB_STORAGE_SCHEMA=2
SEQ_NU=3
echo_date() {{ :; }}
sub_refresh_node_state() {{ :; }}
sub_reset_active_profile_context() {{ :; }}
sub_reset_schema2_cache() {{ :; }}
sub_collect_active_link_hashes_from_profiles_file() {{ :; }}
sub_prune_subscribe_cache() {{ :; }}
skipdb2json() {{
  for tag in import online orphan; do
    cp "{fixture}/$tag.json" "$DIR/$tag.json"
    printf '%s\\t1\\t%s\\tTest\\n' "$DIR/$tag.json" "$tag" >> "$LOCAL_SPLIT_META"
  done
}}
nodes2files() {{ :; }}
nodes_stats() {{ :; }}
sub_apply_active_profile_context() {{ :; }}
get_online_rule_now() {{ sub_mark_active_source_tag online; }}
subprof_mark_state_success() {{ :; }}
pick_node_tool_command() {{ return 1; }}
sub_mark_removed_source_tag() {{ :; }}
sub_prune_source_identity() {{
  test -f "$DIR/import.json" && test -f "$DIR/online.json" && test ! -f "$DIR/orphan.json" || exit 93
  grep -Fxq import "$ACTIVE_SOURCE_TAGS" || exit 94
  echo PRUNE_VERIFIED
  exit 0
}}
start_node_subscribe
''')
        self.assertEqual(result.stdout, "PRUNE_VERIFIED\n")

    def test_editor_saves_local_without_sync_and_online_with_sync(self):
        script = js_functions("subscription_profile_is_enabled", "subscription_profile_can_sync", "save_subscription_profile_from_editor")
        script += f'''
var p={json.dumps(self.profile)}, payload={{...p, schedule_enabled:true}};
var subscribeProfileMap={{[p.id]:p}}, subscribeProfileEditorLayerIndex=null, saved=[], synced=[];
var $={{extend:Object.assign}}, layer={{msg:()=>{{}}}};
var get_subscription_profile_editor_payload=()=>payload;
var validate_subscription_custom_hours=()=>true;
var find_subscription_profile_by_url=()=>null;
var set_subscription_profile_editor_saving=()=>{{}};
var base64_encode_utf8=x=>x;
var call_subscription_profile_api=(action,data,done)=>{{saved.push(JSON.parse(data.ss_subscribe_profile_payload)); done(true);}};
var upsert_subscription_profile_state=x=>{{subscribeProfileMap[x.id]=x;}};
var refresh_table=()=>{{}}, render_subscription_manager=()=>{{}};
var sync_subscription_profiles=id=>synced.push(id);
var alert=msg=>{{throw Error(msg);}};
save_subscription_profile_from_editor(true);
if(saved.length!==1 || saved[0].schedule_enabled!==false || synced.length) throw Error('local save');
payload={{...payload,url:'https://example.invalid/sub'}};
save_subscription_profile_from_editor(true);
if(saved.length!==2 || synced.length!==1) throw Error('online conversion');
'''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


class MerlinboxIPv6Tests(unittest.TestCase):
    def run_case(self, ipv6="1", nat_fail="0", rule_fail="", present="1", node_type="9", repeat=False):
        fns = shell_functions("fancyss/ss/ssconfig.sh", "ensure_chain_with_cmd", "remove_anytls_merlinbox_compat_one", "remove_anytls_merlinbox_compat", "prepare_anytls_merlinbox_compat", "prepare_anytls_merlinbox_compat6", "start_ipv6_proxy_rules", "fallback_ipv6_proxy_to_ipv4", "flush_ip6tables", "disable_ipv6_proxy_runtime")
        script = fns + r'''
echo_date() { :; }
iptables() { return 1; }
ip() { return 0; }
dbus() { :; }
stop_dns_process() { :; }
start_dns_x() { :; }
ipv6_proxy_enabled() { [ "$ss_basic_proxy_ipv6" = 1 ]; }
jump=1
accept=1
nat_calls=0
ip6tables() {
  case "$*" in
    "-t mangle -C PREROUTING -i br+ -j MERLINKBOX_PROXY_V6") [ "$PRESENT" = 1 ] ;;
    "-t mangle -C MERLINKBOX_PROXY_V6 -j FSS_ANYTLS_MB6") [ "$jump" = 1 ] ;;
    "-t mangle -D MERLINKBOX_PROXY_V6 -j FSS_ANYTLS_MB6") jump=0 ;;
    "-t mangle -F FSS_ANYTLS_MB6") accept=0 ;;
    "-t mangle -A FSS_ANYTLS_MB6 -p tcp -j ACCEPT") [ "$RULE_FAIL" != A ] || return 1; accept=1 ;;
    "-t mangle -I MERLINKBOX_PROXY_V6 1 -j FSS_ANYTLS_MB6") [ "$nat_calls" -gt 0 ] || return 90; [ "$RULE_FAIL" != I ] || return 1; jump=$((jump+1)) ;;
    *) return 0 ;;
  esac
}
_start_ipv6_iptables() { nat_calls=$((nat_calls+1)); [ "$NAT_FAIL" = 0 ]; }
prepare_anytls_merlinbox_compat
[ "$jump" = 0 ] && [ "$accept" = 0 ] || exit 91
start_ipv6_proxy_rules
'''
        if repeat:
            script += "start_ipv6_proxy_rules\n"
        script += 'printf "%s,%s,%s,%s\\n" "$jump" "$accept" "$ss_basic_proxy_ipv6" "$nat_calls"\n'
        result = run_shell("bash", script, ss_basic_type=node_type, ss_basic_proxy_ipv6=ipv6, NAT_FAIL=nat_fail, RULE_FAIL=rule_fail, PRESENT=present)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_ipv6_disabled_cleans_old_bypass_without_installing(self):
        self.assertEqual(self.run_case(ipv6="0"), "0,0,0,0")

    def test_ipv6_success_installs_once_after_takeover(self):
        self.assertEqual(self.run_case(repeat=True), "1,1,1,2")

    def test_nat_failure_and_partial_bypass_failure_roll_back(self):
        for nat_fail, rule_fail in [("1", ""), ("0", "A"), ("0", "I")]:
            with self.subTest(nat_fail=nat_fail, rule_fail=rule_fail):
                self.assertEqual(self.run_case(nat_fail=nat_fail, rule_fail=rule_fail), "0,0,0,1")

    def test_other_protocol_and_absent_merlinbox_do_not_bypass(self):
        self.assertEqual(self.run_case(node_type="4"), "0,0,1,1")
        self.assertEqual(self.run_case(present="0"), "0,0,1,1")


class WebtestWarningTests(unittest.TestCase):
    def test_runtime_warning_survives_native_shell_and_cache_hit(self):
        fns = shell_functions("fancyss/scripts/ss_webtest.sh", "wt_warn_xray_nodes_before_test", "wt_prepare_xray_group_runtime", "wt_ensure_webtest_cache_ready", "wt_try_node_tool_webtest_cache_all", "wt_try_node_tool_webtest_cache")
        fns += "\n" + shell_function("fancyss/scripts/ss_webtest_gen.sh", "wt_warn_xray_allow_insecure")
        nodes = {
            "1": {"type":"5", "trojan_ai":"1", "trojan_uuid":"secret-do-not-log"},
            "2": {"type":"5", "trojan_ai":"1", "trojan_pcs":"aabb"},
            "3": {"type":"4", "xray_network_security":"none", "xray_network_security_ai":"1"},
            "4": {"type":"8", "hy2_ai":"0"},
            "5": {"type":"3", "v2ray_network_security":"tls", "v2ray_network_security_ai":"true"},
            "6": {"type":"4", "xray_network_security":"tls", "xray_network_security_ai":"1", "xray_use_json":"1"},
            "7": {"type":"8", "hy2_ai":"1"},  # Not selected.
        }
        for mode in ["native", "shell", "cache"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp)
                (t / "nodes.json").write_text(json.dumps(nodes))
                (t / "ids").write_text("1\n2\n3\n4\n5\n6\n")
                write_executable(t / "node-tool", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TMP2/native.log"\n[ "$MODE" = native ]\n')
                script = fns + r'''
echo_date() { printf '%s\n' "$*"; }
wt_node_get_plain() { jq -r --arg id "$2" --arg f "$1" '.[$id][$f] // ""' "$TMP2/nodes.json"; }
wt_pick_node_tool() { echo "$TMP2/node-tool"; }
wt_cache_log() { :; }
wt_log_node_tool_webtest_summary() { :; }
fss_detect_storage_schema() { echo 2; }
wt_build_nodes_index() { :; }
wt_collect_xray_like_ids_file() { cp "$TMP2/ids" "$1"; }
wt_webtest_cache_is_globally_fresh() { [ "$MODE" = cache ]; }
wt_webtest_cache_lock_acquire() { :; }
wt_webtest_cache_lock_release() { :; }
wt_rebuild_webtest_cache_from_ids() { echo shell > "$TMP2/shell.log"; }
wt_materialize_cached_nodes() { echo materialized > "$TMP2/materialized.log"; return 1; }
wt_mark_failed_from_nodes_file() { :; }
wt_prepare_xray_group_runtime "$TMP2/ids" test >/dev/null 2>&1
test -f "$TMP2/materialized.log"
'''
                result = run_shell("bash", script, TMP2=tmp, MODE=mode, WT_DIAGNOSTIC_LOG=str(t / "warnings.log"))
                self.assertEqual(result.returncode, 0, result.stderr)
                log = (t / "warnings.log").read_text()
                self.assertEqual(len(log.splitlines()), 2)
                self.assertIn("节点#1(trojan)", log)
                self.assertIn("节点#5(v2ray)", log)
                self.assertNotIn("secret", log)
                self.assertEqual((t / "shell.log").exists(), mode == "shell")
                self.assertIn("warm-cache --webtest --effective", (t / "native.log").read_text())


if __name__ == "__main__":
    unittest.main()
