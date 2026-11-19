# P2 / HND v8 停更后严重稳定性问题拆分与排查记录（Issue #15）

本记录对应 Issue #15（关联上游 `hq450/fancyss` `#33899`、`#33904`、`#33936`、`#33943`），将混合的用户故障现象按独立根因拆分为四个维度，并汇总已在 `candidate/p2` 落地的修复措施与实机取证步骤。

---

## 1. 上游 `statx` 兼容与 `ss_node_common.sh` 一致性（`#33899`）

### 根因
1. **旧内核 `statx` 系统调用崩溃**：`binaries/geotool`、`binaries/xapi-tool`、`binaries/anytls-zig`、`binaries/websocketd`、`binaries/status-tool`、`binaries/webtest-tool` 等 Zig 工具在引入 `binaries/zig-linux-compat.sh`（Linux `4.1.0` / `armv5te 2.6.36` 基线）之前编译，仅 `sub-tool` 在 P0 重建；旧路由器 4.1 内核执行包含 `statx` 的二进制会返回 `ENOSYS` 或导致 Zig 0.15 `std.fs` 报错/崩溃。
2. **`fss_resolve_node_field_name` 与 `jq` 回退缺陷（Bug 2）**：
   - `fss_export_current_node_env` 的 `jq` 回退分支（在 `node-tool` 不可用时触发）存在布尔值处理错误（`false // empty` 丢弃 `false`，`true | tostring` 输出 `"true"` 而非 `"1"`）、0 字段调用漏导出、未通过 `fss_run_node_tool` 隔离环境，以及未处理按节点 `type`（`5` Trojan、`6` Naïve、`8` Hysteria2、`9` AnyTLS）区分的通用字段映射。
   - `fss_node_json_simple_value` 对空 JSON 字符串 `""` 会返回字面量 `""` 两字符，且对含 `\"`/`\\` 转义字符的字段会截断损坏。
   - `fss_node_json_server_host_port`、`fss_get_node_server_host_port`、`fss_node_json_snapshot_tsv` 及 `sub_get_node_server_plain`/`sub_get_node_port_plain` 缺失 AnyTLS（`type=9`）和 Naïve（`type=6`）回退。

### 已落地修复
- 扩展 [build-zig-tools.yml](../../.github/workflows/build-zig-tools.yml) 与 [Makefile](../../binaries/Makefile)，使用 [zig-linux-compat.sh](../../binaries/zig-linux-compat.sh)（Linux `4.1.0` / `2.6.36`）在 CI 中全量重建并做 `seccomp ENOSYS` + `qemu-user -strace` 无 `statx`/`copy_file_range` 校验后同步全部 8 个 Zig 工具。
- 在 [ss_node_common.sh](../../fancyss/scripts/ss_node_common.sh) 修复 `fss_resolve_node_field_name`、`fss_node_json_simple_value`、`fss_get_node_field_plain`、`fss_export_current_node_env`、`fss_node_json_server_host_port`、`fss_get_node_server_host_port`、`fss_node_json_snapshot_tsv` 和 `fss_set_node_field_plain`。

---

## 2. Xray `allowInsecure` 移除后的静默断网（`#33943`）

### 根因
- 自带 Xray `v26.11.9-676eaf6` 已彻底移除 `allowInsecure` 字段。`xray -test` 仍返回配置合法且进程监听 `23456`/`3333`，但使用自签或域名不匹配证书且未填 `pinnedPeerCertSha256` 的 VMess/VLESS/Trojan/Hysteria2 节点会静默握手失败，导致全部外网请求超时（并诱发 failover 反复重启）。
- Web UI 在勾选 `AllowInsecure` 时，通过 `x_ai_off` / `trojan_ai_off` / `hy2_ai_off` 隐藏了 `pinnedPeerCertSha256` 和 `verifyPeerCertByName` 输入框，阻碍用户迁移证书指纹；VMess 编辑页也缺少弃用提示。

### 已落地修复
- 在 [ssconfig.sh](../../fancyss/ss/ssconfig.sh)（`warn_xray_allow_insecure_removed`）与 [ss_webtest_gen.sh](../../fancyss/scripts/ss_webtest_gen.sh)（`wt_warn_xray_allow_insecure`）中对勾选 `ai=1` 且缺少 `pcs` 的节点以及自定义 JSON 内的 `"allowInsecure": true` 输出显式日志警告。
- 更新 [Module_shadowsocks.asp](../../fancyss/webs/Module_shadowsocks.asp) 的 `ALLOW_INSECURE_NOTICE` 为现行有效提示，补充 VMess `v_ai_on` 提示，并在所有 `pcs` / `vcn` 行移除 `*_ai_off` 隐藏条件。

---

## 3. AC86U 等小 `/jffs` 机型使用 `full` 包时 `ksdb/log` 膨胀（`#33904`）

### 根因
1. **`full` vs `lite` 空间差异**：`full` 包额外包含 5 个 UPX 压缩二进制（`naive`、`tuic-client`、`anytls-zig`、`haveged`、`ipt2socks`），展开后多占约 `5.36 MiB`；UPX 产物在 UBIFS 上几乎不可再压缩，而 [install.sh](../../fancyss/install.sh) 原 `/data` 卸载列表 `_BINS` 遗漏了 `tuic-client`。
2. **`skipd` `ksdb/log` 高频无效写入**：每次 `source ss_base.sh` 均无条件调用 `get_acl_rule_indexes` -> `resolve_acl_mac`，即使 `ss_acl_mac_*` 未变化也重复执行 `dbus set` / `dbus remove`。HTTP 轮询（`ss_status.sh`）、状态检查（`ss_proc_status.sh`）、`dnsmasq.postconf` 及 `refresh_runtime_context` 会持续向 `ksdb/log` 追加事务记录，最终耗尽小 `/jffs` 空间。
3. **`FSS_BASE_SKIP_DBUS_EXPORT=1` 与 ACL 误删交互 bug**：`ss_shunt_stats.sh` 等只读脚本以 `FSS_BASE_SKIP_DBUS_EXPORT=1` 引入 `ss_base.sh` 时，未导出 `ss_acl_*` 变量却未跳过顶层 `get_acl_rule_indexes`，导致 `is_acl_rule_complete` 误判并将用户 ACL 规则通过 `cleanup_acl_rule` 删除（每次产生多条 `dbus remove` 日志）。

### 已落地修复
- 在 [ss_base.sh](../../fancyss/scripts/ss_base.sh) 中使 `resolve_acl_mac` 写入幂等化，新增 `FSS_BASE_READONLY_ACL=1`（只读脚本跳过 ACL MAC 写库与清理），并用 `FSS_BASE_SKIP_DBUS_EXPORT != 1` 包裹顶层 ACL 初始化代码；同步为 [ss_status.sh](../../fancyss/scripts/ss_status.sh)、[ss_proc_status.sh](../../fancyss/scripts/ss_proc_status.sh) 与 [dnsmasq.postconf](../../fancyss/ss/rules/dnsmasq.postconf) 启用 `FSS_BASE_READONLY_ACL=1`。
- 在 [ssconfig.sh](../../fancyss/ss/ssconfig.sh) 中使 `refresh_runtime_context` 写入 `ss_basic_version_local` 幂等化。
- 在 [install.sh](../../fancyss/install.sh) 的 `_BINS` `/data` 分流列表中加入 `tuic-client`。

---

## 4. BE86U（3006.102 / Linux 4.19）`full` 包开机重启风险排查（`#33936`）

### 高概率触发面分析（与 `lite` 包的差异）
1. **仅 `full` 包含的 `/koolshare/bin/haveged`**：在存在 `haveged` 二进制而固件使用内核硬件 RNG（未运行 `jitterentropy-rngd`）的 Linux 4.19+ 机型（如 BE86U）上，[ssconfig.sh](../../fancyss/ss/ssconfig.sh) 会启动传统静态 UPX 版 `haveged -w 1024`，且 `kill_process` 原先误用 `killall -9 ${haveged_pid}`（传数字 PID 给 `killall` 无法杀死旧进程）。
2. **`mangle == 0` 仍加载 `xt_TPROXY` 与下发 `fwmark 0x07`**：即使关闭 UDP 代理或使用 AnyTLS/Naïve（`mangle=0`），`_start_iptables` / `_start_ipv6_iptables` 与分流链原先仍无条件加载 `xt_TPROXY`/`xt_socket`、写入 `-j TPROXY` 规则（触发内核 `request_module`）以及插入 `mangle OUTPUT --dport 53 -j MARK --set-mark 0x7` + `table 310 local 0.0.0.0/0 dev lo` 策略路由，在博通 Runner / FlowCache 硬件加速内核上属于高风险向量。

### 已落地缓解与后续实机复现取证清单
- 已落地修复：在 [ssconfig.sh](../../fancyss/ss/ssconfig.sh) 中限制仅 `LINUX_VER` 为 `26`/`41` 时启动自带 `haveged` 并改用 `kill -9 ${haveged_pid}` 清理；在 `_start_iptables`、`_start_ipv6_iptables`、`init_shunt_chain_v4`/`v6` 中仅当 `mangle=1` 时加载 `xt_TPROXY`、创建 `table 310` 路由及下发 `mangle OUTPUT` fwmark / `TPROXY` 规则。
- **仍需实机验证/取证**（若用户再次反馈 BE86U 或 AC86U 复现）：
  1. 重启前记录内核与插件版本：`uname -a && cat /koolshare/ss/version && dbus get ss_basic_pkg_type`
  2. 捕获崩溃/串口或 pstore 日志：检查 `/sys/fs/pstore/`、`/jffs/syslog.log*`、`dmesg` 及前一次重启的 `/tmp/upload/ss_wan_log.txt`、`lsmod | grep -E 'xt_TPROXY|xt_socket'`
  3. 定位引发崩溃的具体步骤：在开启/关闭 `haveged` 与关闭 `mangle` UDP TPROXY 的条件下分段启动 `/koolshare/ss/ssconfig.sh start` 确认精确内核 Oops 调用栈
  4. 检查 `/jffs` 与 `skipd` 状态：`df -h /jffs /data && ls -lh /jffs/.koolshare/ksdb/ /tmp/upload/`，确认是否为 `ksdb/log` 增量写满或 `/jffs` 空间不足所致。
