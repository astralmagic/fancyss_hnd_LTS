#!/bin/sh

# fancyss script for asuswrt/merlin based router with software center

source /koolshare/scripts/ss_base.sh

LOGFILE_F=/tmp/upload/ssf_status.txt
LOGFILE_C=/tmp/upload/ssc_status.txt
LOGSTREAM_F=/tmp/upload/ssf_status.stream
LOGSTREAM_C=/tmp/upload/ssc_status.stream
STATUS_FRONT_CACHE=/tmp/upload/ss_status_front.txt
STATUS_BACK_CACHE=/tmp/upload/ss_status.txt
STATUS_STATE_FILE=/tmp/upload/ss_status_daemon.json
FAILOVER_STATE_FILE="${FAILOVER_STATE_FILE:-/tmp/fss_failover_state}"
FAILOVER_LOCK_FILE="${FAILOVER_LOCK_FILE:-/var/lock/koolss.lock}"
FAILOVER_SS_LOG_FILE="${FAILOVER_SS_LOG_FILE:-/tmp/upload/ss_log.txt}"
FAILOVER_MAX_STREAK="${FAILOVER_MAX_STREAK:-3}"
FAILOVER_RECOVERY_RESET="${FAILOVER_RECOVERY_RESET:-10}"
#LOGTIME1=📅$(TZ=UTC-8 date -R "+%m-%d/%H:%M:%S")
LOGTIME1=⌚$(TZ=UTC-8 date -R "+%H:%M:%S")
CURRENT=$(fss_get_current_node_id)
CHK_INTER=$(dbus get ss_basic_interval)
COUNT=1
LAST_CACHE=""
rm -rf /tmp/upload/test.txt

get_node_name_by_id() {
	fss_get_node_field_plain "$1" name
}

pick_fastest_webtest_node() {
	local current_id="$1"
	local result_file="${2:-/tmp/upload/webtest_bakcup.txt}"
	local latency=""
	local node_id=""

	[ -s "${result_file}" ] || return 1
	awk -F '>' -v current="${current_id}" '
		$1 ~ /^[0-9]+$/ && $1 != current {
			if (!seen[$1]) {
				order[++count] = $1
				seen[$1] = 1
			}
			latency[$1] = $2
		}
		END {
			for (i = 1; i <= count; i++) {
				id = order[i]
				if (latency[id] ~ /^[0-9]+$/) {
					printf "%d\t%s\n", latency[id], id
				}
			}
		}
	' "${result_file}" 2>/dev/null | sort -n -k1,1 -k2,2n | while read -r latency node_id
	do
		[ -n "${node_id}" ] || continue
		if fss_node_id_exists "${node_id}" && { [ "${ss_basic_mode}" != "7" ] || fss_shunt_target_is_proxy_node "${node_id}"; }; then
			printf '%s\n' "${node_id}"
			return 0
		fi
	done
}

append_status_log_line() {
	local logfile="$1"
	local streamfile="$2"
	local line="$3"
	printf '%s\n' "${line}" >> "${logfile}"
	printf '%s\n' "${line}" >> "${streamfile}"
}

clean_f_log() {
	[ $(wc -l "$LOGFILE_F" | awk '{print $1}') -le "$LOG_MAX" ] && return
	local logdata=$(tail -n 500 "$LOGFILE_F")
	echo "$logdata" > $LOGFILE_F 2> /dev/null
	unset logdata
}

clean_c_log() {
	[ $(wc -l "$LOGFILE_C" | awk '{print $1}') -le "$LOG_MAX" ] && return
	local logdata=$(tail -n 500 "$LOGFILE_C")
	echo "$logdata" > $LOGFILE_C 2> /dev/null
	unset logdata
}

LOGM() {
	echo $1
	logger $1
}

pick_jq_bin() {
	if command -v jq >/dev/null 2>&1; then
		command -v jq
		return 0
	fi
	if [ -x "/koolshare/bin/jq" ]; then
		echo "/koolshare/bin/jq"
		return 0
	fi
	return 1
}

extract_status_ms() {
	printf '%s' "$1" | grep -Eo '[0-9]+ ms' | tail -n1 | awk '{print $1}'
}

status_line_ok() {
	printf '%s' "$1" | grep -q "✓"
}

status_cache_foreign_line() {
	printf '%s' "$1" | awk -F '@@' '{print $1}'
}

status_cache_china_line() {
	printf '%s' "$1" | awk -F '@@' '{print $NF}'
}

status_probe_host() {
	local host="$1"
	host="${host#*://}"
	host="${host%%/*}"
	host="${host%%:*}"
	host="${host#www.}"
	printf '%s' "${host}"
}

status_daemon_state_snapshot() {
	local jq_bin=""
	[ -s "${STATUS_STATE_FILE}" ] || return 1
	jq_bin="$(pick_jq_bin)" || return 1
	"${jq_bin}" -r '
		def pick($name):
			((.results // []) | map(select(.name == $name)) | .[0]) // {};
		[
			((pick("foreign4").ok // false) | tostring),
			((pick("foreign4").status_code // 0) | tostring),
			((pick("foreign4").elapsed_ms // 0) | tostring),
			((pick("china").ok // false) | tostring),
			((pick("china").status_code // 0) | tostring),
			((pick("china").elapsed_ms // 0) | tostring)
		] | @tsv
	' "${STATUS_STATE_FILE}" 2>/dev/null
}

write_status_logs_from_cache() {
	local cache_payload="$1"
	local current_name="$(get_node_name_by_id "$(fss_get_current_node_id)")"
	local foreign_line="$(status_cache_foreign_line "${cache_payload}")"
	local china_line="$(status_cache_china_line "${cache_payload}")"
	local foreign_ms="$(extract_status_ms "${foreign_line}")"
	local china_ms="$(extract_status_ms "${china_line}")"
	local foreign_url="$(dbus get ss_basic_furl)"
	local china_url="$(dbus get ss_basic_curl)"
	local foreign_host=""
	local china_host=""
	local foreign_ok=""
	local foreign_code=""
	local china_ok=""
	local china_code=""
	local snapshot=""
	local loop_count="${COUNT}"

	[ -n "${foreign_url}" ] || foreign_url="http://www.google.com/generate_204"
	[ -n "${china_url}" ] || china_url="http://connectivitycheck.platform.hicloud.com/generate_204"
	foreign_host="$(status_probe_host "${foreign_url}")"
	china_host="$(status_probe_host "${china_url}")"
	[ -n "${foreign_host}" ] || foreign_host="foreign"
	[ -n "${china_host}" ] || china_host="china"

	snapshot="$(status_daemon_state_snapshot)" || snapshot=""
	if [ -n "${snapshot}" ]; then
		IFS='	' read -r foreign_ok foreign_code foreign_ms china_ok china_code china_ms <<-EOF
		${snapshot}
		EOF
	fi

	[ -n "${foreign_ok}" ] || foreign_ok="$(status_line_ok "${foreign_line}" && echo true || echo false)"
	[ -n "${china_ok}" ] || china_ok="$(status_line_ok "${china_line}" && echo true || echo false)"
	[ -n "${foreign_code}" ] || foreign_code="$([ "${foreign_ok}" = "true" ] && echo 200 || echo 000)"
	[ -n "${china_code}" ] || china_code="$([ "${china_ok}" = "true" ] && echo 200 || echo 000)"

	LOGTIME1=⌚$(TZ=UTC-8 date -R "+%H:%M:%S")
	if [ "${foreign_ok}" = "true" ]; then
		append_status_log_line "${LOGFILE_F}" "${LOGSTREAM_F}" "${LOGTIME1} ➡️ ${foreign_host} ⏱ ${foreign_ms} ms 🌎 ${foreign_code} OK ✈️ ${current_name} 🧮${loop_count}"
	else
		append_status_log_line "${LOGFILE_F}" "${LOGSTREAM_F}" "${LOGTIME1} ➡️ ${foreign_host} ⏱ --- ms 🌎 ${foreign_code} failed ✈️ ${current_name} 🧮${loop_count}"
	fi
	if [ "${china_ok}" = "true" ]; then
		append_status_log_line "${LOGFILE_C}" "${LOGSTREAM_C}" "${LOGTIME1} ➡️ ${china_host} ⏱ ${china_ms} ms 🌎 ${china_code} OK 🧮${loop_count}"
	else
		append_status_log_line "${LOGFILE_C}" "${LOGSTREAM_C}" "${LOGTIME1} ➡️ ${china_host} ⏱ --- ms 🌎 ${china_code} failed 🧮${loop_count}"
	fi
}

refresh_status_cache_files() {
	local cache_payload=""
	cache_payload="$(cat "${STATUS_FRONT_CACHE}" 2>/dev/null)"
	[ -n "${cache_payload}" ] || return 1
	printf '%s@@%s\n' "${cache_payload}" "$(dbus get ss_heart_beat)" > "${STATUS_BACK_CACHE}"
	if [ "${cache_payload}" != "${LAST_CACHE}" ];then
		LAST_CACHE="${cache_payload}"
		write_status_logs_from_cache "${cache_payload}"
		return 0
	fi
	return 2
}

_get_interval() {
	case "$CHK_INTER" in
	1)
		echo "0-1000"
		;;
	2)
		echo "2000-5000"
		;;
	3)
		echo "6000-13000"
		;;
	4)
		echo "14000-29000"
		;;
	5)
		echo "30000-61000"
		;;
	esac
}

failover_now_epoch() {
	date +%s 2>/dev/null || echo 0
}

failover_state_read() {
	FAILOVER_LAST_TS=0
	FAILOVER_STREAK=0
	FAILOVER_OK_STREAK=0
	FAILOVER_LAST_OK_SIG=""
	if [ -f "${FAILOVER_STATE_FILE}" ]; then
		read -r FAILOVER_LAST_TS FAILOVER_STREAK FAILOVER_OK_STREAK FAILOVER_LAST_OK_SIG < "${FAILOVER_STATE_FILE}" 2>/dev/null || true
	fi
	case "${FAILOVER_LAST_TS}" in ''|*[!0-9]*) FAILOVER_LAST_TS=0 ;; esac
	case "${FAILOVER_STREAK}" in ''|*[!0-9]*) FAILOVER_STREAK=0 ;; esac
	case "${FAILOVER_OK_STREAK}" in ''|*[!0-9]*) FAILOVER_OK_STREAK=0 ;; esac
}

failover_state_write() {
	printf '%s %s %s %s\n' "${1:-0}" "${2:-0}" "${3:-0}" "${4:-}" > "${FAILOVER_STATE_FILE}" 2>/dev/null || true
}

failover_cooldown_seconds() {
	local streak="${1:-0}"
	if [ "${streak}" -le 0 ]; then
		echo 0
	elif [ "${streak}" -eq 1 ]; then
		echo 60
	elif [ "${streak}" -eq 2 ]; then
		echo 180
	else
		echo 300
	fi
}

failover_is_ssconfig_locked() {
	if command -v flock >/dev/null 2>&1; then
		[ -f "${FAILOVER_LOCK_FILE}" ] || return 1
		if ( exec 9>"${FAILOVER_LOCK_FILE}" && flock -n 9 ) >/dev/null 2>&1; then
			return 1
		fi
		return 0
	fi
	ps 2>/dev/null | grep -E "ssconfig\.sh" | grep -v grep >/dev/null 2>&1
}

failover_record_probe_health() {
	[ -f "${LOGFILE_F}" ] || return 0
	failover_state_read
	[ "${FAILOVER_STREAK}" -gt 0 ] || return 0
	# 仅统计过滤掉 fancyss 诊断日志与 === 分隔标记后的有效探测记录数，避免分隔标记引起误计数
	local probe_count="$(sed '/fancyss/d; /===/d; /^[[:space:]]*$/d' "${LOGFILE_F}" 2>/dev/null | wc -l | tr -d ' ')"
	local last_line="$(sed '/fancyss/d; /===/d; /^[[:space:]]*$/d' "${LOGFILE_F}" 2>/dev/null | tail -n 1)"
	[ -n "${last_line}" ] || return 0
	local line_sig="${probe_count}:$(printf '%s' "${last_line}" | cksum | awk '{print $1}')"
	if [ "${line_sig}" = "${FAILOVER_LAST_OK_SIG}" ]; then
		return 0
	fi
	if echo "${last_line}" | grep -Eq "200 OK|204 OK"; then
		local next_ok=$((FAILOVER_OK_STREAK + 1))
		if [ "${next_ok}" -ge "${FAILOVER_RECOVERY_RESET}" ]; then
			failover_state_write 0 0 0 "${line_sig}"
			LOGM "$LOGTIME1 fancyss：节点已连续恢复正常 ${next_ok} 次，重置故障转移退避计数。"
		else
			failover_state_write "${FAILOVER_LAST_TS}" "${FAILOVER_STREAK}" "${next_ok}" "${line_sig}"
		fi
	elif [ "${FAILOVER_OK_STREAK}" -gt 0 ]; then
		failover_state_write "${FAILOVER_LAST_TS}" "${FAILOVER_STREAK}" 0 "${line_sig}"
	fi
}

failover_should_throttle() {
	local action_kind="${1:-restart}"
	local now="$(failover_now_epoch)"
	failover_state_read

	if failover_is_ssconfig_locked; then
		LOGM "$LOGTIME1 fancyss：检测到 ssconfig 正在执行，跳过本次故障转移以避免并发冲突。"
		return 0
	fi

	if [ "${action_kind}" = "restart" ] && [ "${FAILOVER_STREAK}" -ge "${FAILOVER_MAX_STREAK}" ]; then
		LOGM "$LOGTIME1 fancyss：故障自动重启已达连续上限（${FAILOVER_MAX_STREAK} 次），停止循环重启并保持 Fail-Closed 拦截状态（防出口 IP 泄漏），请检查节点或手动切换。"
		return 0
	fi

	local cooldown="$(failover_cooldown_seconds "${FAILOVER_STREAK}")"
	local elapsed=$((now - FAILOVER_LAST_TS))
	if [ "${FAILOVER_LAST_TS}" -gt 0 ] && [ "${cooldown}" -gt 0 ] && [ "${elapsed}" -lt "${cooldown}" ]; then
		local remain=$((cooldown - elapsed))
		LOGM "$LOGTIME1 fancyss：故障转移处于退避冷却期（剩余 ${remain}s，第 ${FAILOVER_STREAK} 次连续故障），暂不重复触发。"
		return 0
	fi

	local next_streak=$((FAILOVER_STREAK + 1))
	failover_state_write "${now}" "${next_streak}" 0 "${FAILOVER_LAST_OK_SIG}"
	return 1
}

failover_mark_separator() {
	local reason="${1:-recovery}"
	# 注意：此处不含 "fancyss" 字样，避免被 failover_check_1/2/3 的 sed '/fancyss/d' 过滤
	printf '%s === failover %s ===\n' "${LOGTIME1}" "${reason}" >> "${LOGFILE_F}" 2>/dev/null || true
}

failover_next_node() {
	local current="$1" next="" ids=""
	if [ "${ss_basic_mode}" != "7" ]; then
		fss_get_next_node_id_in_order "${current}"
		return $?
	fi
	ids="$(fss_list_node_ids)" || return 1
	printf '%s\n' "${ids}" | awk -v current="${current}" '
		NF { nodes[++n]=$0; if ($0==current) pos=n }
		END { for (i=1; i<=n; i++) print nodes[(pos+i-1)%n+1] }
	' | while IFS= read -r next; do
		if [ "${next}" != "${current}" ] && fss_shunt_target_is_proxy_node "${next}"; then
			printf '%s\n' "${next}"
			return 0
		fi
	done
}

failover_exec_recovery() {
	local reason="${1:-restart}"
	local target_node_id="${2:-}"
	[ "${reason}" = "restart" ] || [ -n "${target_node_id}" ] || return 1
	failover_mark_separator "${reason}"
	if [ -n "${target_node_id}" ]; then
		# 完整重启会停止状态监测，切换事务须由独立持锁进程执行。
		run start-stop-daemon -S -q -b -x /koolshare/ss/ssconfig.sh -- failover_switch "${reason}" "$3" "${target_node_id}"
		return $?
	fi
	if sh /koolshare/ss/ssconfig.sh reload_proxy_only >> "${FAILOVER_SS_LOG_FILE}" 2>&1; then
		return 0
	fi
	LOGM "$LOGTIME1 fancyss：轻量重载代理未就绪，回退 Fail-Closed 安全重启..."
	run start-stop-daemon -S -q -b -x /koolshare/ss/ssconfig.sh -- restart
}

failover_action(){
	FLAG=$1
	PING=$2
	local current_id=$(fss_get_current_node_id)
	local current_name=$(get_node_name_by_id "${current_id}")
	if [ "$ss_failover_s4_1" == "0" ];then
		[ "$FLAG" == "1" ] && LOGM "$LOGTIME1 fancyss：检测到连续${ss_failover_s1}个状态故障，按配置关闭插件（注意：将恢复直连）！"
		[ "$FLAG" == "2" ] && LOGM "$LOGTIME1 fancyss：检测到最近${ss_failover_s2_1}个状态中，故障次数超过${ss_failover_s2_2}个，按配置关闭插件（注意：将恢复直连）！"
		[ "$FLAG" == "3" ] && LOGM "$LOGTIME1 fancyss：检测到最近${ss_failover_s3_1}个状态平均延迟:${PING}超过${ss_failover_s3_2} ms，按配置关闭插件（注意：将恢复直连）！"
		dbus set ss_basic_enable="0"
		# 关闭
		dbus set ss_heart_beat="1"
		run start-stop-daemon -S -q -b -x /koolshare/ss/ssconfig.sh -- stop
	elif [ "$ss_failover_s4_1" == "1" ];then
		if failover_should_throttle "restart"; then
			return
		fi
		[ "$FLAG" == "1" ] && LOGM "$LOGTIME1 fancyss：检测到连续${ss_failover_s1}个状态故障，执行 Fail-Closed 轻量重启！"
		[ "$FLAG" == "2" ] && LOGM "$LOGTIME1 fancyss：检测到最近${ss_failover_s2_1}个状态中，故障次数超过${ss_failover_s2_2}个，执行 Fail-Closed 轻量重启！"
		[ "$FLAG" == "3" ] && LOGM "$LOGTIME1 fancyss：检测到最近${ss_failover_s3_1}个状态平均延迟:${PING}超过${ss_failover_s3_2} ms，执行 Fail-Closed 轻量重启！"
		failover_exec_recovery "restart"
	elif [ "$ss_failover_s4_1" == "2" ];then
		if failover_should_throttle "switch"; then
			return
		fi
		if [ "$ss_failover_s4_2" == "3" ];then
			if [ ! -f "/tmp/upload/webtest_bakcup.txt" ];then
				LOGM "$LOGTIME1 fancyss：没有找到web延迟测试结果，采取切换到下个节点的策略..."
				ss_failover_s4_2="2"
			fi
			local CURR_NODE=${current_id}
			local FAST_NODE=$(pick_fastest_webtest_node "${CURR_NODE}" "/tmp/upload/webtest_bakcup.txt")
			if [ -z "${FAST_NODE}" ];then
				LOGM "$LOGTIME1 fancyss：没有找到web延迟测试最低的节点，采取切换到下个节点的策略..."
				ss_failover_s4_2="2"
			fi
		fi
	
		if [ "$ss_failover_s4_2" == "1" ];then
			local backup_id=$(fss_get_failover_node_id)
			local backup_name=$(get_node_name_by_id "${backup_id}")
			[ "$FLAG" == "1" ] && LOGM "$LOGTIME1 fancyss：检测到连续${ss_failover_s1}个状态故障，切换到备用节点：[${backup_name}]！同时把主节点降级为备用节点！"
			[ "$FLAG" == "2" ] && LOGM "$LOGTIME1 fancyss：检测到最近${ss_failover_s2_1}个状态中，故障次数超过${ss_failover_s2_2}个，切换到备用节点：[${backup_name}]！同时把主节点降级为备用节点！"
			[ "$FLAG" == "3" ] && LOGM "$LOGTIME1 fancyss：检测到最近${ss_failover_s3_1}个状态平均延迟:${PING}超过${ss_failover_s3_2} ms，切换到备用节点：[${backup_name}]！同时把主节点降级为备用节点！"
			# 切换
			failover_exec_recovery "switch_backup" "${backup_id}" "${current_id}" || return 1
		elif [ "$ss_failover_s4_2" == "2" ];then
			NEXT_NODE=$(failover_next_node "${current_id}")
			local NEXT_NAME=$(get_node_name_by_id "${NEXT_NODE}")
			local NODE_COUNT=$(fss_get_node_count)
			if [ "${NODE_COUNT}" -le "1" ] || [ -z "${NEXT_NODE}" ];then
				LOGM "$LOGTIME1 fancyss：没有可切换的兼容节点！为防止出口 IP 泄漏，保持 Fail-Closed 拦截状态并告警（如需恢复直连请手动关闭插件）。"
				failover_mark_separator "single_node_hold"
				return
			fi
			[ "$FLAG" == "1" ] && LOGM "$LOGTIME1 fancyss：检测到连续${ss_failover_s1}个状态故障，切换到节点列表的下个节点：[${NEXT_NAME}]！"
			[ "$FLAG" == "2" ] && LOGM "$LOGTIME1 fancyss：检测到最近${ss_failover_s2_1}个状态中，故障次数超过${ss_failover_s2_2}个，切换到节点列表的下个节点：[${NEXT_NAME}]！"
			[ "$FLAG" == "3" ] && LOGM "$LOGTIME1 fancyss：检测到最近${ss_failover_s3_1}个状态平均延迟:${PING}超过${ss_failover_s3_2} ms，切换到节点列表的下个节点：[${NEXT_NAME}]！"
			# 切换
			failover_exec_recovery "switch_next" "${NEXT_NODE}" "${current_id}" || return 1
		elif [ "$ss_failover_s4_2" == "3" ];then
			LOGM "$LOGTIME1 fancyss：切换到web延迟最低节点：[$(get_node_name_by_id "${FAST_NODE}")]..."
			failover_exec_recovery "switch_fastest" "${FAST_NODE}" "${current_id}" || return 1
		fi
	fi	
}

failover_check_1(){
	local LINES=$(($ss_failover_s1 + 3))
	local START_MARK=$(cat "$LOGFILE_F" | sed '/fancyss/d' | tail -n "$LINES" | grep "===")
	if [ -n "$START_MARK" ];then
		#echo "$LOGTIME1 fancyss：1-检测到前$LINES行刚提交，先不检测！"
		return
	fi
	
	local OK_MARK=$(cat "$LOGFILE_F" | sed '/fancyss/d' | tail -n "$ss_failover_s1" | grep -Ec "200 OK|204 OK")
	if [ "$OK_MARK" == "0" ];then
		failover_action 1
	fi
}

failover_check_2(){
	local LINES=$(($ss_failover_s2_1 + 3))
	local START_MARK=$(cat "$LOGFILE_F" | sed '/fancyss/d' | tail -n "$LINES" | grep "===")
	if [ -n "$START_MARK" ];then
		#echo "$LOGTIME1 fancyss：2-检测到前$LINES行刚提交，先不检测！"
		return
	fi

	local OK_MARK=$(cat "$LOGFILE_F" | sed '/fancyss/d' | tail -n "$ss_failover_s2_1" | grep -Evc "200 OK|204 OK")
	if [ "$OK_MARK" -gt "$ss_failover_s2_2" ];then
		failover_action 2
	fi
}

failover_check_3(){
	local LINES=$(($ss_failover_s3_1 + 3))
	local START_MARK=$(cat "$LOGFILE_F" | sed '/fancyss/d' | tail -n "$LINES" | grep "===")
	if [ -n "$START_MARK" ];then
		#echo "$LOGTIME1 fancyss：3-检测到前$LINES行刚提交，先不检测！"
		return
	fi

	local OK_MARK=$(cat "$LOGFILE_F" | sed '/fancyss/d' | tail -n "$ss_failover_s3_1" | grep -E "200 OK|204 OK" | grep -oe "⏱ [0-9].* ms" | sed 's/⏱ //g'| sed 's/ ms//g' | awk '{sum+=$1} END {print sum/NR}' | awk '{printf "%.0f\n",$1}')
	#echo "$LOGTIME1 fancyss：前15次状态平均延迟：$OK_MARK ！"
	if [ "$OK_MARK" -gt "$ss_failover_s3_2" ];then
		failover_action 3 "$OK_MARK"
	fi
}

heath_check(){
	#LOGTIME1=$(TZ=UTC-8 date -R "+%m-%d %H:%M:%S")
	LOGTIME1=⌚$(TZ=UTC-8 date -R "+%H:%M:%S")
	
	[ "${ss_failover_enable}" != "1" ] && return
	[ "${COUNT}" -eq "2" ] && echo "${LOGTIME1} fancyss：跳过刚提交后的2个状态，从此处开始的状态用于故障检测"
	[ "${COUNT}" -le "2" ] && return

	failover_record_probe_health

	[ "${ss_failover_c1}" == "1" ] && failover_check_1
	[ "${ss_failover_c2}" == "1" ] && failover_check_2
	[ "${ss_failover_c3}" == "1" ] && failover_check_3
}

main(){
	while : ; do
		# sleep 2s first in case of to early detection
		usleep 2000000
	
		# refresh dbus data in every loop
		eval $(dbus export ss_failover)
		[ "${ss_failover_enable}" != "1" ] && exit
		LOG_MAX=${ss_failover_s5}
		[ -z "${LOG_MAX}" ] && LOG_MAX=2000
		
		# clean clog incase of log grow too big
		if [ -f "/tmp/upload/ssf_status.txt" ];then
			clean_f_log
			clean_c_log
		fi
		
		# exit loop when fancyss not enabled
		[ "$(dbus get ss_basic_enable)" != "1" ] && exit
		
		if ! ps | grep -E "/koolshare/bin/status-tool daemon" | grep -v grep >/dev/null 2>&1; then
			sh /koolshare/scripts/ss_status_daemon.sh restart >/dev/null 2>&1
		fi

		if [ "$(ps|grep ssconfig.sh|grep -v grep)" ];then
			# wait until ssconfig.sh finished running
			echo ${LOGTIME1} ssconfig.sh running "[$(get_node_name_by_id "$(fss_get_current_node_id)")]" >> $LOGFILE_F
			#continue
		else
			refresh_status_cache_files >/dev/null 2>&1
		fi

		# do health check after result obtain
		heath_check >> ${LOGFILE_F}

		# conter
		let COUNT++
		
		# random sleep $(_get_interval) time
		local INTER=$(shuf -i $(_get_interval) -n 1)
		INTER=$((${INTER} * 1000))
		#echo $LOGTIME1 $INTER >> /tmp/inter.txt
		usleep ${INTER}
	done
}

main
