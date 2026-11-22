import base64
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
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


def run_bash(script, **env):
    return subprocess.run(
        ["bash", "-c", script],
        env=dict(os.environ, **env),
        capture_output=True,
        text=True,
    )


class Hysteria2Tests(unittest.TestCase):
    def run_validation(self, up, down, congestion=""):
        script = shell_function("fancyss/ss/ssconfig.sh", "validate_hy2_speed_settings") + r'''
number_test() { printf '%s' "$1" | grep -Eq '^[0-9]+$' && echo 0 || echo 1; }
echo_date() { printf '%s\n' "$*"; }
close_in_five() { exit 42; }
validate_hy2_speed_settings
'''
        return run_bash(
            script,
            ss_basic_hy2_up=up,
            ss_basic_hy2_dl=down,
            ss_basic_hy2_cg=congestion,
        )

    def test_empty_speeds_use_bbr(self):
        result = self.run_validation("", "")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bbr", result.stdout)

    def test_only_one_speed_is_rejected(self):
        for up, down in (("100", ""), ("", "100")):
            with self.subTest(up=up, down=down):
                self.assertEqual(self.run_validation(up, down).returncode, 42)

    def test_non_numeric_speed_is_rejected(self):
        for up, down in (("100mbps", "100"), ("100", "10/20")):
            with self.subTest(up=up, down=down):
                self.assertEqual(self.run_validation(up, down).returncode, 42)

    def test_numeric_speeds_keep_congestion_behavior(self):
        result = self.run_validation("100", "200", "brutal")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("brutal", result.stdout)


class HostsAndSpaceTests(unittest.TestCase):
    def test_hosts_trailing_dots_aliases_and_normal_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            hosts = Path(tmp) / "hosts"
            hosts.write_text(
                "192.0.2.10 client-host.\n"
                "192.0.2.11 first. second. plain\n"
                "192.0.2.12 router.lan printer\n"
            )
            sed_wrapper = r'''
sed() {
  if [ "$1" = "-i" ] && sed --version >/dev/null 2>&1; then
    command sed "$@"
  elif [ "$1" = "-i" ]; then
    shift
    command sed -i '' "$@"
  else
    command sed "$@"
  fi
}
'''
            script = sed_wrapper + shell_function("fancyss/ss/ssconfig.sh", "prepare_chng_hosts")
            script += f'\nprepare_chng_hosts "{tmp}"\n'
            result = run_bash(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (Path(tmp) / "chng_hosts").read_text(),
                "192.0.2.10 client-host\n"
                "192.0.2.11 first second plain\n"
                "192.0.2.12 router.lan printer\n",
            )
            config = (ROOT / "fancyss/ss/ssconfig.sh").read_text()
            self.assertIn("hosts /tmp/etc/chng_hosts", config)

    def test_exact_mount_space_query_handles_nested_and_long_devices(self):
        sample = (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/a-very-long-device-name-that-used-to-wrap 100000 40000 60000 40% /jffs\n"
            "/dev/a-very-long-device-name-that-used-to-wrap 100000 40000 60000 40% /jffs/nested/bind\n"
            "/dev/data 200000 50000 150000 25% /data\n"
            "/dev/data 200000 50000 150000 25% /data/nested\n"
        )
        script = r'''
df() { cat "$DF_SAMPLE"; }
jffs=$(df -Pk | awk '$NF == "/jffs" {print $4; exit}')
data=$(df -Pk | awk '$NF == "/data" {print $4; exit}')
printf '%s %s\n' "$jffs" "$data"
'''
        with tempfile.NamedTemporaryFile("w", delete=False) as file:
            file.write(sample)
            sample_file = file.name
        self.addCleanup(Path(sample_file).unlink)
        result = run_bash(script, DF_SAMPLE=sample_file)
        self.assertEqual(result.stdout.strip(), "60000 150000")
        for path in ("fancyss/install.sh", "fancyss/scripts/ss_dns_test.sh"):
            self.assertNotIn("df | grep -w", (ROOT / path).read_text())
            self.assertNotRegex((ROOT / path).read_text(), r"df \| awk")


class PortOwnershipTests(unittest.TestCase):
    def test_proc_fallback_and_netstat_fast_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "proc/net").mkdir(parents=True)
            (root / "proc/123/fd").mkdir(parents=True)
            (root / "proc/456/fd").mkdir(parents=True)
            (root / "bin").mkdir()
            (root / "proc/net/tcp").write_text(
                "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
                "0: 0100007F:5BA0 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 4242 1 0 100 0 0 10 0\n"
            )
            (root / "proc/net/tcp6").write_text("")
            (root / "proc/123/fd/3").symlink_to("socket:[4242]")
            (root / "bin/pidof").write_text(
                "#!/bin/sh\n[ \"$1\" = xray ] && echo 123\n[ \"$1\" = other ] && echo 456\nexit 0\n"
            )
            (root / "bin/netstat").write_text("#!/bin/sh\nexit 0\n")
            for path in (root / "bin").iterdir():
                path.chmod(0o755)
            names = (
                "fss_listening_socket_inodes",
                "fss_port_listened_by_bins",
                "fss_port_owner_pids",
                "fss_socks5_open_bin",
            )
            preamble = shell_functions("fancyss/scripts/ss_base.sh", *names)
            script = preamble + r'''
fss_port_listened_by_bins 23456 xray || exit 10
! fss_port_listened_by_bins 23456 other || exit 11
! fss_port_listened_by_bins 23456 missing || exit 12
mv "$FIXTURE/proc/123/fd/3" "$FIXTURE/proc/456/fd/3"
cat > "$FIXTURE/bin/netstat" <<'EOF'
#!/bin/sh
echo 'tcp 0 0 127.0.0.1:23456 0.0.0.0:* LISTEN 456/other'
EOF
! fss_port_listened_by_bins 23456 xray || exit 13
fss_port_listened_by_bins 23456 other || exit 14
cat > "$FIXTURE/bin/netstat" <<'EOF'
#!/bin/sh
echo 'tcp 0 0 127.0.0.1:23456 0.0.0.0:* LISTEN 123/xray'
EOF
fss_port_listened_by_bins 23456 xray || exit 15
'''
            env = dict(os.environ)
            env["PATH"] = f"{root / 'bin'}:{env['PATH']}"
            env.update(FIXTURE=tmp, FSS_PROC_ROOT=str(root / "proc"))
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_udp_proc_fallback_and_pid_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "proc/net").mkdir(parents=True)
            (root / "proc/123/fd").mkdir(parents=True)
            (root / "proc/456/fd").mkdir(parents=True)
            (root / "bin").mkdir()
            (root / "proc/net/tcp").write_text(
                "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
                "0: 0100007F:5BA0 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 4242 1 0 100 0 0 10 0\n"
            )
            (root / "proc/net/tcp6").write_text("")
            (root / "proc/net/udp").write_text(
                "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode ref pointer drops\n"
                "0: 00000000:1EE9 00000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 5555 2 0 0\n"
                "1: 00000000:1EE9 01010101:0035 01 00000000:00000000 00:00000000 00000000 0 0 6666 2 0 0\n"
            )
            (root / "proc/net/udp6").write_text(
                "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode ref pointer drops\n"
                "0: 00000000000000000000000000000000:0035 00000000000000000000000000000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 7777 2 0 0\n"
            )
            (root / "proc/123/fd/3").symlink_to("socket:[5555]")
            (root / "proc/123/fd/4").symlink_to("socket:[7777]")
            (root / "proc/123/fd/5").symlink_to("socket:[4242]")
            (root / "proc/456/fd/3").symlink_to("socket:[6666]")
            (root / "bin/pidof").write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = smartdns ] && echo 123\n"
                "[ \"$1\" = xray ] && echo 123\n"
                "[ \"$1\" = other ] && echo 456\n"
                "exit 0\n"
            )
            # Firmware netstat without PID/Program name (column ends with '-')
            (root / "bin/netstat").write_text(
                "#!/bin/sh\n"
                "echo 'udp        0      0 :::7913                 :::*                                -'\n"
            )
            for path in (root / "bin").iterdir():
                path.chmod(0o755)
            names = (
                "fss_listening_socket_inodes",
                "fss_port_listened_by_bins",
                "fss_port_owner_pids",
                "fss_socks5_open_bin",
            )
            preamble = shell_functions("fancyss/scripts/ss_base.sh", *names)
            script = preamble + r'''
[ "$(fss_listening_socket_inodes 7913)" = "5555" ] || exit 20
[ "$(fss_listening_socket_inodes 53)" = "7777" ] || exit 21
fss_port_listened_by_bins 7913 smartdns || exit 22
fss_port_listened_by_bins 53 smartdns || exit 23
! fss_port_listened_by_bins 7913 other || exit 24
[ "$(fss_port_owner_pids 7913)" = "123" ] || exit 25
[ "$(fss_socks5_open_bin)" = "xray" ] || exit 26

cat > "$FIXTURE/bin/netstat" <<'EOF'
#!/bin/sh
echo 'udp        0      0 0.0.0.0:7913            0.0.0.0:*                           123/smartdns'
echo 'udp        0      0 :::7913                 :::*                                123/smartdns'
echo 'tcp        0      0 127.0.0.1:23456         0.0.0.0:*               LISTEN      789/anytls-zig'
EOF
[ "$(fss_port_owner_pids 7913)" = "123" ] || exit 27
[ "$(fss_socks5_open_bin)" = "anytls-zig" ] || exit 28

cat > "$FIXTURE/bin/netstat" <<'EOF'
#!/bin/sh
exit 0
EOF
rm -f "$FIXTURE/proc/123/fd/5"
! fss_socks5_open_bin || exit 29
'''
            env = dict(os.environ)
            env["PATH"] = f"{root / 'bin'}:{env['PATH']}"
            env.update(FIXTURE=tmp, FSS_PROC_ROOT=str(root / "proc"))
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"rc={result.returncode}: {result.stderr}")


class SubscriptionStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dbus = self.root / "dbus"
        self.dbus.mkdir()
        names = (
            "subprof_log", "subprof_jq_bin", "subprof_json_is_valid", "subprof_state_key", "subprof_b64_compact",
            "subprof_b64_encode_compact", "subprof_b64_decode_loose",
            "subprof_dbus_set_json_by_key", "subprof_dbus_get_json_by_key",
            "subprof_state_json_or_default", "subprof_write_state_or_log",
            "subprof_mark_state_success", "subprof_mark_state_failure",
        )
        self.functions = 'SUB_PROFILE_DBUS_PREFIX="ss_subprof_"\n' + shell_functions("fancyss/scripts/ss_subscribe_profile_lib.sh", *names)
        self.functions += r'''
dbus() {
  case "$1" in
    set)
      [ "${DBUS_FAIL:-0}" != "1" ] || return 1
      pair="$2"; key="${pair%%=*}"; value="${pair#*=}"
      printf '%s' "$value" > "$DBUS_DIR/$key"
      ;;
    get)
      [ -f "$DBUS_DIR/$2" ] && cat "$DBUS_DIR/$2"
      ;;
  esac
}
'''
        self.env = dict(os.environ, DBUS_DIR=str(self.dbus))

    def state(self):
        path = self.dbus / "ss_subprof_p1_state"
        if not path.exists():
            return None
        import base64
        return json.loads(base64.b64decode(path.read_text()).decode())

    def mark(self, action, error=""):
        if action == "success":
            call = 'subprof_mark_state_success p1 hash group curl path auto preset'
        else:
            call = f'subprof_mark_state_failure p1 "{error}" hash group curl path auto preset'
        result = subprocess.run(
            ["bash", "-c", self.functions + "\n" + call],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_first_success_existing_failure_corruption_and_recovery(self):
        self.mark("success")
        state = self.state()
        self.assertGreater(state["last_ok_ts"], 0)
        self.assertEqual(state["last_error"], "")

        state["custom"] = "preserved"
        import base64
        (self.dbus / "ss_subprof_p1_state").write_text(base64.b64encode(json.dumps(state).encode()).decode())
        self.mark("failure", "temporary failure")
        failed = self.state()
        self.assertEqual(failed["last_error"], "temporary failure")
        self.assertEqual(failed["custom"], "preserved")

        (self.dbus / "ss_subprof_p1_state").write_text("bm90LWpzb24=")
        self.mark("success")
        recovered = self.state()
        self.assertEqual(recovered["last_error"], "")
        self.assertGreater(recovered["last_ok_ts"], 0)
        self.assertNotIn("custom", recovered)

    def test_cron_rebuild_is_idempotent(self):
        cru = self.root / "cru"
        state = self.root / "cron"
        cru.write_text(
            "#!/bin/sh\ncase \"$1\" in\n"
            "l) [ ! -f \"$CRU_STATE\" ] || cat \"$CRU_STATE\" ;;\n"
            "a) printf '%s\\t%s\\n' \"$2\" \"$3\" >> \"$CRU_STATE\" ;;\n"
            "d) if [ -f \"$CRU_STATE\" ]; then awk -F '\\t' -v id=\"$2\" '$1 != id' \"$CRU_STATE\" > \"$CRU_STATE.new\" && mv \"$CRU_STATE.new\" \"$CRU_STATE\"; fi ;;\n"
            "esac\n"
        )
        cru.chmod(0o755)
        names = (
            "subprof_log", "subprof_cron_job_name", "subprof_cron_value_csv", "subprof_cron_week_csv",
            "subprof_cron_minute", "subprof_cron_hour", "subprof_cron_delay_for_index",
            "subprof_clear_cron_jobs", "subprof_rebuild_cron_jobs",
        )
        script = shell_functions("fancyss/scripts/ss_subscribe_profile_lib.sh", *names) + r'''
subprof_list_profile_ids() { printf 'p1\n'; }
subprof_profile_schedule_tsv() { printf 'true\ttrue\t1\t1\t7\t3\t5\t1\t2\t\n'; }
subprof_rebuild_cron_jobs
subprof_rebuild_cron_jobs
'''
        env = dict(os.environ, SUBPROF_CRU_BIN=str(cru), CRU_STATE=str(state))
        result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = state.read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertIn("ssnodeprof_p1", rows[0])
        self.assertIn("ss_node_subscribe.sh 3 p1", rows[0])

    def test_error_logging_works_without_echo_date(self):
        r1 = subprocess.run(
            ["bash", "-c", self.functions + '\nsubprof_write_state_or_log ss_subprof_p1_state ""\n'],
            env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(r1.returncode, 1)
        self.assertIn("订阅状态序列化失败", r1.stderr)
        self.assertNotIn("not found", r1.stderr)

        r2 = subprocess.run(
            ["bash", "-c", self.functions + '\nsubprof_write_state_or_log ss_subprof_p1_state "{}"\n'],
            env=dict(self.env, DBUS_FAIL="1"), capture_output=True, text=True,
        )
        self.assertEqual(r2.returncode, 1)
        self.assertIn("订阅状态写入失败", r2.stderr)
        self.assertNotIn("not found", r2.stderr)

        cru_fail = self.root / "cru_fail"
        cru_fail.write_text("#!/bin/sh\n[ \"$1\" = a ] && exit 1\nexit 0\n")
        cru_fail.chmod(0o755)
        names = (
            "subprof_log", "subprof_cron_job_name", "subprof_cron_value_csv", "subprof_cron_week_csv",
            "subprof_cron_minute", "subprof_cron_hour", "subprof_cron_delay_for_index",
            "subprof_clear_cron_jobs", "subprof_rebuild_cron_jobs",
        )
        script = shell_functions("fancyss/scripts/ss_subscribe_profile_lib.sh", *names) + r'''
subprof_list_profile_ids() { printf 'p1\n'; }
subprof_profile_schedule_tsv() { printf 'true\ttrue\t1\t1\t7\t3\t5\t1\t2\t\n'; }
subprof_rebuild_cron_jobs
'''
        r3 = subprocess.run(
            ["bash", "-c", script],
            env=dict(os.environ, SUBPROF_CRU_BIN=str(cru_fail)),
            capture_output=True, text=True,
        )
        self.assertEqual(r3.returncode, 1)
        self.assertIn("订阅定时任务注册失败", r3.stderr)
        self.assertNotIn("not found", r3.stderr)


class RuleUpdateTests(unittest.TestCase):
    KEYS = "gfwlist chnroute chnlist chnroute6 rotlist white_list black_list block_list apple_china google_china cdn_test udplist adslist".split()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote"
        self.local = self.root / "local"
        self.bin = self.root / "bin"
        self.work_tmp = self.root / "tmp"
        for directory in (self.remote, self.local, self.bin, self.work_tmp):
            directory.mkdir()
        (self.root / "base.sh").write_text(
            "dbus() { return 0; }\n"
            "nvram() { return 0; }\n"
            "cru() { return 0; }\n"
            "http_response() { return 0; }\n"
        )
        (self.bin / "wget").write_text(r'''#!/bin/sh
out=""; prev=""
for arg in "$@"; do [ "$prev" = "-qO" ] && out="$arg"; prev="$arg"; done
for arg in "$@"; do url="$arg"; done
name="${url##*/}"
printf '%s\n' "$name" >> "$WGET_LOG"
[ "${FAIL_NAME:-}" != "$name" ] || exit 1
cp "$REMOTE_DIR/$name" "$out"
''')
        (self.bin / "wget").chmod(0o755)
        for key in self.KEYS:
            name = self.filename(key)
            (self.local / name).write_text(f"old-{key}\n")
            (self.remote / name).write_text(f"new-{key}\n")
        self.write_manifest(self.local, "old-date")
        self.write_manifest(self.remote, "new-date")
        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.bin}:{self.env['PATH']}"
        self.env.update(
            REMOTE_DIR=str(self.remote), WGET_LOG=str(self.root / "wget.log"),
            FSS_BASE_FILE=str(self.root / "base.sh"), FSS_RULE_FILE=str(self.local / "rules.json.js"),
            FSS_RULE_SAVE_DIR=str(self.local), FSS_RULE_URL_MAIN="https://controlled.invalid",
            FSS_RULE_LOG_FILE=str(self.root / "log"), FSS_SKIP_RESTART="1",
            FSS_RULE_LOCK_DIR=str(self.root / "rule.lock"), FSS_RULE_TMP_ROOT=str(self.work_tmp),
        )

    def filename(self, key):
        return key + (".gz" if key in ("gfwlist", "chnlist", "adslist") else ".txt")

    def write_manifest(self, directory, date):
        value = {}
        for key in self.KEYS:
            name = self.filename(key)
            value[key] = {
                "name": name, "date": date,
                "md5": hashlib.md5((directory / name).read_bytes()).hexdigest(), "count": "1",
            }
        (directory / "rules.json.js").write_text(json.dumps(value))

    def run_update(self, *args, **extra):
        if not args:
            args = ("force",)
        return subprocess.run(
            ["sh", str(ROOT / "fancyss/scripts/ss_rule_update.sh"), *args],
            env=dict(self.env, **extra), capture_output=True, text=True,
        )

    def test_batch_success_and_adslist_exclusion(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        for key in self.KEYS:
            if key == "adslist":
                continue
            self.assertEqual((self.local / self.filename(key)).read_text(), f"new-{key}\n")
        self.assertNotIn("adslist.gz", (self.root / "wget.log").read_text().splitlines())
        self.assertEqual(json.loads((self.local / "rules.json.js").read_text())["block_list"]["date"], "new-date")

    def test_checksum_or_download_failure_preserves_every_file(self):
        for failure in ("checksum", "download"):
            with self.subTest(failure=failure):
                setup_res = self.run_update()
                self.assertEqual(setup_res.returncode, 0, setup_res.stderr + setup_res.stdout)
                self.write_manifest(self.remote, f"next-{failure}")
                for key in self.KEYS:
                    (self.remote / self.filename(key)).write_text(f"{failure}-{key}\n")
                self.write_manifest(self.remote, f"next-{failure}")
                before = {p.name: p.read_bytes() for p in self.local.iterdir()}
                (self.root / "wget.log").unlink(missing_ok=True)
                if failure == "checksum":
                    (self.remote / "gfwlist.gz").write_text("corrupt\n")
                    extra = {}
                else:
                    extra = {"FAIL_NAME": "gfwlist.gz"}
                result = self.run_update(**extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual({p.name: p.read_bytes() for p in self.local.iterdir()}, before)
                downloaded = (self.root / "wget.log").read_text().splitlines()
                self.assertEqual(downloaded, ["rules.json.js", "gfwlist.gz"])
                (self.remote / "gfwlist.gz").write_text("new-gfwlist\n")
                for key in self.KEYS:
                    (self.remote / self.filename(key)).write_text(f"new-{key}\n")
                self.write_manifest(self.remote, "new-date")

    def test_replace_failure_rolls_back_already_replaced_files(self):
        setup_res = self.run_update()
        self.assertEqual(setup_res.returncode, 0, setup_res.stderr + setup_res.stdout)
        self.write_manifest(self.remote, "newer-date")
        for key in self.KEYS:
            (self.remote / self.filename(key)).write_text(f"newer-{key}\n")
        self.write_manifest(self.remote, "newer-date")
        before = {p.name: p.read_bytes() for p in self.local.iterdir()}
        (self.bin / "mv").write_text(r'''#!/bin/sh
for arg in "$@"; do target="$arg"; done
[ "${target##*/}" != "${FAIL_MV_NAME:-}" ] || exit 1
/bin/mv "$@"
''')
        (self.bin / "mv").chmod(0o755)
        result = self.run_update(FAIL_MV_NAME="chnroute.txt")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual({p.name: p.read_bytes() for p in self.local.iterdir()}, before)

    def test_web_action_exit_code_and_single_sentinel(self):
        res_ok = self.run_update("123", "2")
        self.assertEqual(res_ok.returncode, 0, res_ok.stderr + res_ok.stdout)
        log_ok = (self.root / "log").read_text().splitlines()
        self.assertEqual(log_ok.count("XU6J03M6"), 1)

        self.write_manifest(self.remote, "fail-date")
        for key in self.KEYS:
            (self.remote / self.filename(key)).write_text(f"fail-{key}\n")
        self.write_manifest(self.remote, "fail-date")
        res_fail = self.run_update("124", "2", FAIL_NAME="gfwlist.gz")
        self.assertNotEqual(res_fail.returncode, 0)
        log_fail = (self.root / "log").read_text().splitlines()
        self.assertEqual(log_fail.count("XU6J03M6"), 1)

    def test_active_lock_prevents_concurrent_update(self):
        lock_dir = self.root / "rule.lock"
        lock_dir.mkdir()
        pid_file = lock_dir / "pid"
        pid_file.write_text(f"{os.getpid()}\n")
        before = {p.name: p.read_bytes() for p in self.local.iterdir()}
        res = self.run_update()
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual({p.name: p.read_bytes() for p in self.local.iterdir()}, before)
        self.assertTrue(pid_file.is_file())
        self.assertEqual(pid_file.read_text().strip(), str(os.getpid()))

    def test_path_with_spaces_succeeds(self):
        space_dir = self.root / "dir with space"
        space_dir.mkdir()
        res = self.run_update(PATH=f"{space_dir}:{self.env['PATH']}")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_busybox_mktemp_and_missing_mktemp_fallback(self):
        mktemp_stub = self.bin / "mktemp"
        try:
            # 1) BusyBox mktemp that rejects path in template and only supports -d -p DIR TEMPLATE
            mktemp_stub.write_text(r'''#!/bin/sh
if [ "$1" = "-d" ] && [ "$2" = "-p" ] && [ -n "$3" ] && [ -n "$4" ]; then
    dir="$3/${4%XXXXXX}busybox"
    mkdir -p "$dir" && printf '%s\n' "$dir"
    exit 0
fi
exit 1
''')
            mktemp_stub.chmod(0o755)
            res = self.run_update()
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            self.assertEqual(list(self.work_tmp.iterdir()), [])

            # 2) mktemp completely unusable -> falls back to mkdir loop
            mktemp_stub.write_text("#!/bin/sh\nexit 127\n")
            mktemp_stub.chmod(0o755)
            res_fallback = self.run_update()
            self.assertEqual(res_fallback.returncode, 0, res_fallback.stderr + res_fallback.stdout)
            self.assertEqual(list(self.work_tmp.iterdir()), [])
        finally:
            mktemp_stub.unlink(missing_ok=True)

    def test_same_date_corrupted_local_file_is_repaired(self):
        res_init = self.run_update("update")
        self.assertEqual(res_init.returncode, 0, res_init.stderr + res_init.stdout)
        # Corrupt local gfwlist.gz while keeping manifest date identical to remote
        (self.local / "gfwlist.gz").write_text("corrupted-local-content\n")
        (self.root / "wget.log").unlink(missing_ok=True)
        res_repair = self.run_update("update")
        self.assertEqual(res_repair.returncode, 0, res_repair.stderr + res_repair.stdout)
        self.assertEqual((self.local / "gfwlist.gz").read_text(), "new-gfwlist\n")
        downloaded = (self.root / "wget.log").read_text().splitlines()
        self.assertEqual(downloaded, ["rules.json.js", "gfwlist.gz"])

    def test_gfwlist_merges_four_sources_and_preserves_output_on_failure(self):
        script = ROOT / "rules_ng/update_rules.sh"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            for name in ("source", "work", "out"):
                (path / name).mkdir()
            inputs = path / "source"
            official = "[AutoProxy 0.2.9]\n||Shared.Example\n||first.example\n@@||except.example\n"
            (inputs / "official").write_bytes(base64.b64encode(official.encode()))
            (inputs / "loukky").write_text("[AutoProxy 0.2.9]\n||second.example\n||shared.example\n")
            (inputs / "loyal").write_text(".SHARED.Example\r\nthird.example\n# ignored\n")
            (inputs / "pexcn").write_text("fourth.example\nshared.example\n192.0.2.1\nv2ex.com\n")
            manifest = path / "out/rules.json.js"
            manifest.write_text("{}")
            generate = r'''source "$SCRIPT"
CURR_PATH="$TMPROOT/out"
RULE_FILE="$CURR_PATH/rules.json.js"
curl_download() { cp "$TMPROOT/source/${2##*/}" "$2"; }
update_gfwlist "$TMPROOT/work"
'''
            env = dict(SCRIPT=str(script), TMPROOT=tmp)
            result = run_bash(generate, **env)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = path / "out/gfwlist.gz"
            original = output.read_bytes()
            original_manifest = manifest.read_bytes()
            self.assertEqual(gzip.decompress(original).decode().splitlines(), [
                "first.example", "fourth.example", "second.example", "shared.example", "third.example",
            ])
            self.assertEqual(json.loads(original_manifest)["gfwlist"]["count"], "5")
            for name, bad in (("pexcn", "# empty"), ("loukky", "unusable data")):
                with self.subTest(name=name):
                    saved = (inputs / name).read_bytes()
                    (inputs / name).write_text(bad)
                    self.assertNotEqual(run_bash(generate, **env).returncode, 0)
                    self.assertEqual(output.read_bytes(), original)
                    self.assertEqual(manifest.read_bytes(), original_manifest)
                    (inputs / name).write_bytes(saved)

    def test_update_rules_script_uses_expected_upstreams(self):
        script = (ROOT / "rules_ng/update_rules.sh").read_text()
        for obsolete in (
            "17mon/china_ip_list",
            "firehol/blocklist-ipsets",
        ):
            self.assertNotIn(obsolete, script)
        for active in (
            "pexcn/daily/gh-pages/gfwlist/gfwlist.txt",
            "Loukky/gfwlist-by-loukky/master/gfwlist.txt",
            "gfwlist/gfwlist/master/gfwlist.txt",
            "Loyalsoldier/v2ray-rules-dat/release/gfw.txt",
            "gaoyifan/china-operator-ip/ip-lists/china.txt",
            "Loyalsoldier/geoip/release/text/cn.txt",
            "misakaio/chnroutes2/master/chnroutes.txt",
            "felixonmars/dnsmasq-china-list",
        ):
            self.assertIn(active, script)


class ProcStatusVersionTests(unittest.TestCase):
    def test_format_anytls_version_line_keeps_16_char_column(self):
        names = ("get_zig_tool_version", "print_bin_version_line", "format_anytls_version_line")
        preamble = shell_functions("fancyss/scripts/ss_proc_status.sh", *names)
        with tempfile.TemporaryDirectory() as tmp:
            bin_path = Path(tmp) / "anytls-zig"
            for raw, expected_ver, expected_note in (
                ("0.1.0", "0.1.0", "版本标识后端"),
                ("anytls-go 0.1.0 (fancyss CLI compatibility; sing-anytls v0.0.13)", "anytls-go/0.1.0", "sing-anytls v0.0.13"),
            ):
                with self.subTest(raw=raw):
                    bin_path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{raw}'\n")
                    bin_path.chmod(0o755)
                    res = run_bash(preamble + f'\nformat_anytls_version_line "{bin_path}"\n')
                    self.assertEqual(res.returncode, 0, res.stderr)
                    line = res.stdout.rstrip("\n")
                    self.assertEqual(line[:16].strip(), "anytls-zig")
                    self.assertEqual(line[17:33].strip(), expected_ver)
                    self.assertLessEqual(len(expected_ver), 16)
                    self.assertIn(expected_note, line[34:])


class AssetTests(unittest.TestCase):
    def test_traditional_manifest_matches_every_asset(self):
        manifest = json.loads((ROOT / "rules_ng/rules.json.js").read_text())
        self.assertEqual(set(manifest), set(RuleUpdateTests.KEYS))
        for key, item in manifest.items():
            with self.subTest(key=key):
                path = ROOT / "rules_ng" / item["name"]
                self.assertTrue(path.is_file())
                self.assertEqual(hashlib.md5(path.read_bytes()).hexdigest(), item["md5"])
                if path.suffix == ".gz":
                    count = len(gzip.decompress(path.read_bytes()).splitlines())
                else:
                    count = len(path.read_text().splitlines())
                self.assertEqual(int(item["count"]), count)
                if key == "chnroute":
                    networks = [ipaddress.ip_network(line) for line in path.read_text().splitlines()]
                    self.assertEqual(int(item["count_ip"]), sum(n.num_addresses for n in networks))
                if key == "chnlist":
                    domains = gzip.decompress(path.read_bytes()).decode().splitlines()
                    self.assertTrue(all(re.fullmatch(r"[a-z0-9._-]+", domain) for domain in domains))

    def test_geodata_manifest_and_package_mirror_are_consistent(self):
        counts = json.loads((ROOT / "rules_ng2/meta/rule_counts.json").read_text())
        manifest_text = (ROOT / "fancyss/res/shunt_manifest.json.js").read_text()
        presets = json.loads(manifest_text.split("=", 1)[1].strip().rstrip(";"))
        for preset in presets:
            with self.subTest(preset=preset["id"]):
                summary = counts["preset"][preset["id"]]
                self.assertEqual(preset["count"], summary["total"])
                self.assertEqual(preset["site_count"], summary["site_count"])
                self.assertEqual(preset["ip_count"], summary["ip_count"])
        for name in ("geosite.dat", "geoip.dat"):
            source = ROOT / "rules_ng2/dat" / name
            package = ROOT / "fancyss/ss/rules_ng2/dat" / name
            self.assertGreater(source.stat().st_size, 0)
            self.assertEqual(source.read_bytes(), package.read_bytes())

    def test_geodata_fetch_failure_preserves_existing_asset(self):
        names = (
            "normalize_site_domain_list_custom", "normalize_site_666os",
            "normalize_site_tokens", "normalize_ip_plain", "emit_source", "build_asset_family",
        )
        functions = shell_functions("scripts/update_geodata_assets.sh", *names)
        for mode in ("failure", "empty"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "site").mkdir()
                asset = root / "site/demo.txt"
                asset.write_text("domain:keep.example\n")
                assets = root / "assets.json"
                assets.write_text(json.dumps({"site": [{
                    "id": "demo", "mode": "generated",
                    "sources": [{"type": "remote_666os_site", "url": "https://invalid.example"}],
                }]}))
                if mode == "failure":
                    fetch = "fetch_url() { return 1; }"
                else:
                    fetch = "fetch_url() { printf 'not a valid domain\\n'; }"
                script = functions + "\n" + fetch + f'''
TMP_DIR="{root}"
RULES_ROOT="{root}"
ASSETS_JSON="{assets}"
DO_FETCH=1
build_asset_family site
'''
                result = run_bash(script)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(asset.read_text(), "domain:keep.example\n")

    def test_google_play_cn_routes_and_common_domains(self):
        google_cn = (ROOT / "rules_ng2/site/google-cn.txt").read_text()
        for domain in ("c.android.clients.google.com", "redirector.gvt1.com", "gvt1-cn.com"):
            self.assertIn(domain, google_cn)
        for name in ("google", "cn", "gfw"):
            self.assertGreater((ROOT / "rules_ng2/site" / f"{name}.txt").stat().st_size, 0)


class BuildTests(unittest.TestCase):
    def test_hnd_lite_packed_once_and_smartdns_is_managed(self):
        source = (ROOT / "build.sh").read_text()
        self.assertEqual(len(re.findall(r"^\s*pack hnd lite release\s*$", source, re.M)), 1)
        self.assertIn("binaries/smartdns", source)
        self.assertNotIn("fancyss/bin-ipq64", source)
        makefile = (ROOT / "binaries/Makefile").read_text()
        self.assertIn("v2ray-plugin/make_latest.sh", makefile)
        self.assertIn("xray/make_latest.sh", makefile)
        for target in ("update_anytls_zig", "update_smartdns", "update_chinadns_ng"):
            self.assertRegex(makefile, re.compile(rf"^{target}:", re.M))


if __name__ == "__main__":
    unittest.main()
