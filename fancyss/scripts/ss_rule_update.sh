#!/bin/sh

# fancyss script for asuswrt/merlin based router with software center

source "${FSS_BASE_FILE:-/koolshare/scripts/base.sh}"
eval $(dbus export ss_basic_)
alias echo_date='echo 【$(TZ=UTC-8 date -R +%Y%m%d\ %X)】:'
RULE_FILE="${FSS_RULE_FILE:-/koolshare/ss/rules/rules.json.js}"
RULE_SAVE_DIR="${FSS_RULE_SAVE_DIR:-/koolshare/ss/rules}"
RULE_LOG_FILE="${FSS_RULE_LOG_FILE:-/tmp/upload/ss_log.txt}"
RULE_LOCK_DIR="${FSS_RULE_LOCK_DIR:-/tmp/fancyss_rule_update.lock}"
RULE_TMP_ROOT="${FSS_RULE_TMP_ROOT:-/tmp}"
URL_MAIN="${FSS_RULE_URL_MAIN:-https://raw.githubusercontent.com/astralmagic/fancyss_hnd_LTS/lts/rules_ng}"
FSS_RULE_ACTIVE_TMPDIR=""
FSS_RULE_LOCK_OWNED=0

run(){
	env -i PATH="${PATH}" "$@"
}

finish_rule_log_error(){
	mkdir -p "$(dirname "${RULE_LOG_FILE}")"
	echo XU6J03M6 >> "${RULE_LOG_FILE}"
}

cleanup_rule_update(){
	if [ -n "${FSS_RULE_ACTIVE_TMPDIR}" ]; then
		rm -rf "${FSS_RULE_ACTIVE_TMPDIR}"
		FSS_RULE_ACTIVE_TMPDIR=""
	fi
	if [ "${FSS_RULE_LOCK_OWNED}" = "1" ] && [ "$(cat "${RULE_LOCK_DIR}/pid" 2>/dev/null)" = "$$" ]; then
		rm -rf "${RULE_LOCK_DIR}"
	fi
	FSS_RULE_LOCK_OWNED=0
}

create_rule_tmpdir(){
	local base_dir="${1:-/tmp}"
	local prefix="${2:-fancyss_rule_download}"
	local tmp_dir=""
	local try=0

	mkdir -p "${base_dir}" 2>/dev/null || return 1
	if type mktemp >/dev/null 2>&1; then
		tmp_dir="$(mktemp -d "${base_dir}/${prefix}.XXXXXX" 2>/dev/null)" && [ -n "${tmp_dir}" ] && [ -d "${tmp_dir}" ] && {
			printf '%s\n' "${tmp_dir}"
			return 0
		}
		tmp_dir="$(mktemp -d -p "${base_dir}" "${prefix}.XXXXXX" 2>/dev/null)" && [ -n "${tmp_dir}" ] && [ -d "${tmp_dir}" ] && {
			printf '%s\n' "${tmp_dir}"
			return 0
		}
	fi

	while [ "${try}" -lt 32 ]
	do
		tmp_dir="${base_dir}/${prefix}.$$.${try}"
		if (umask 077 && mkdir "${tmp_dir}") 2>/dev/null; then
			printf '%s\n' "${tmp_dir}"
			return 0
		fi
		try=$((try + 1))
	done

	return 1
}

acquire_rule_lock(){
	local holder_pid=""
	mkdir -p "$(dirname "${RULE_LOCK_DIR}")"
	if mkdir "${RULE_LOCK_DIR}" 2>/dev/null; then
		printf '%s\n' "$$" > "${RULE_LOCK_DIR}/pid"
		FSS_RULE_LOCK_OWNED=1
		return 0
	fi
	holder_pid="$(cat "${RULE_LOCK_DIR}/pid" 2>/dev/null)"
	if [ -n "${holder_pid}" ] && kill -0 "${holder_pid}" 2>/dev/null; then
		echo_date "已有规则更新任务正在运行（PID: ${holder_pid}），跳过本次更新！"
		return 1
	fi
	rm -rf "${RULE_LOCK_DIR}"
	if mkdir "${RULE_LOCK_DIR}" 2>/dev/null; then
		printf '%s\n' "$$" > "${RULE_LOCK_DIR}/pid"
		FSS_RULE_LOCK_OWNED=1
		return 0
	fi
	echo_date "无法获取规则更新锁，退出！"
	return 1
}

rules_runtime_numbers_tsv(){
	local json_file="$1"
	[ -f "${json_file}" ] || return 1
	run jq -r '
		def text($v):
			if $v == null then
				""
			elif ($v | type) == "string" then
				$v
			else
				($v | tostring)
			end;
		[
			text(.gfwlist.date),
			text(.chnroute.date),
			text(.chnlist.date),
			text(.gfwlist.count),
			text(.chnroute.count),
			text(.chnroute.count_ip),
			text(.chnlist.count)
		] | join("\u001f")
	' "${json_file}" 2>/dev/null
}

start_update(){
	local rule_down_dir=""
	local rule_stage_dir=""
	local online_manifest=""
	local updated_entries=""
	local batch_failed=0
	local reboot=0
	local rule_spec=""
	local rule_key=""
	local rule_enabled=""

	if [ ! -f "${RULE_FILE}" ];then
		echo_date "没有找到规则版本号文件: rules.json.js！请尝试覆盖安装插件解决！退出！"
		finish_rule_log_error
		return 1
	fi
	if ! run jq --tab . "${RULE_FILE}" >/dev/null 2>&1;then
		echo_date "本地规则版本号文件解析失败：${RULE_FILE}！请尝试覆盖安装插件解决！退出！"
		finish_rule_log_error
		return 1
	fi
	if ! acquire_rule_lock; then
		finish_rule_log_error
		return 1
	fi
	trap 'cleanup_rule_update' EXIT INT TERM
	if ! rule_down_dir="$(create_rule_tmpdir "${RULE_TMP_ROOT}" "fancyss_rule_download")"; then
		echo_date "创建规则更新临时目录失败，退出！"
		cleanup_rule_update
		finish_rule_log_error
		return 1
	fi
	FSS_RULE_ACTIVE_TMPDIR="${rule_down_dir}"
	rule_stage_dir="${rule_down_dir}/stage"
	online_manifest="${rule_down_dir}/rules.json.js"
	updated_entries="${rule_down_dir}/updated.tsv"

	mkdir -p "${rule_stage_dir}" "${RULE_SAVE_DIR}"
	: > "${updated_entries}"
	echo ==================================================================================================
	echo_date "开始更新fancyss规则，请等待..."

	if ! wget -4 --timeout=8 -qO "${online_manifest}" "${URL_MAIN}/rules.json.js" || [ ! -s "${online_manifest}" ]; then
		echo_date "没有检测到在线版本，可能是访问github有问题，去大陆白名单模式试试吧！"
		cleanup_rule_update
		finish_rule_log_error
		return 1
	fi
	if ! run jq --tab . "${online_manifest}" >/dev/null 2>&1;then
		echo_date "在线规则版本号文件解析失败：${URL_MAIN}/rules.json.js！退出！"
		cleanup_rule_update
		finish_rule_log_error
		return 1
	fi
	echo_date "检测到在线版本文件，继续..."

	update_rule() {
		local key="$1"
		local enabled="$2"
		local online_name=""
		local online_md5=""
		local online_date=""
		local local_date=""
		local local_md5=""
		local tmp_file=""
		local dst_file=""
		local tmp_md5=""

		if [ "${enabled}" != "1" ]; then
			echo_date "你并没有勾选${key}更新！"
			return 0
		fi

		online_name="$(run jq -r --arg key "${key}" '.[$key].name // empty' "${online_manifest}" 2>/dev/null)"
		online_md5="$(run jq -r --arg key "${key}" '.[$key].md5 // empty' "${online_manifest}" 2>/dev/null)"
		online_date="$(run jq -r --arg key "${key}" '.[$key].date // empty' "${online_manifest}" 2>/dev/null)"
		local_date="$(run jq -r --arg key "${key}" '.[$key].date // empty' "${RULE_FILE}" 2>/dev/null)"
		if [ -z "${online_name}" ] || [ -z "${online_md5}" ] || [ -z "${online_date}" ]; then
			echo_date "在线版本文件缺少 ${key} 的名称、MD5 或日期，整批更新中止！"
			return 1
		fi
		case "${online_name}" in
		*/*|*..*|*[\ \	]*)
			echo_date "在线版本文件包含非法规则文件名：${online_name}，整批更新中止！"
			return 1
			;;
		esac
		if ! printf '%s' "${online_md5}" | grep -Eiq '^[0-9a-f]{32}$'; then
			echo_date "在线版本文件包含非法 MD5：${key}，整批更新中止！"
			return 1
		fi

		dst_file="${RULE_SAVE_DIR}/${online_name}"
		if [ "${local_date}" = "${online_date}" ] && [ "${force_update}" != "1" ] && [ -f "${dst_file}" ]; then
			local_md5="$(md5sum "${dst_file}" 2>/dev/null | awk '{print $1}')"
			if [ "${local_md5}" = "${online_md5}" ]; then
				echo_date "检测到${key}本地版本号和在线版本号相同，不进行更新!"
				return 0
			fi
			echo_date "检测到${key}本地文件校验不一致，重新下载修复..."
		fi

		echo_date "检测到新版本${key}，开始更新..."
		tmp_file="${rule_stage_dir}/${online_name}"
		if ! wget -4 --timeout=8 -qO "${tmp_file}" "${URL_MAIN}/${online_name}" || [ ! -s "${tmp_file}" ]; then
			echo_date "${online_name}下载失败，整批更新中止！"
			return 1
		fi
		tmp_md5="$(md5sum "${tmp_file}" | awk '{print $1}')"
		if [ "${tmp_md5}" != "${online_md5}" ]; then
			echo_date "${online_name}校验未通过（${tmp_md5} != ${online_md5}），整批更新中止！"
			return 1
		fi
		printf '%s\t%s\n' "${key}" "${online_name}" >> "${updated_entries}"
		echo_date "${online_name}下载完成，校验通过。"
		return 0
	}

	for rule_spec in \
		"gfwlist:${ss_basic_gfwlist_update}" \
		"chnroute:${ss_basic_chnroute_update}" \
		"chnlist:${ss_basic_chnlist_update}" \
		"chnroute6:1" \
		"rotlist:1" \
		"white_list:1" \
		"black_list:1" \
		"block_list:1" \
		"apple_china:1" \
		"google_china:1" \
		"cdn_test:1" \
		"udplist:1"
	do
		rule_key="${rule_spec%%:*}"
		rule_enabled="${rule_spec#*:}"
		if ! update_rule "${rule_key}" "${rule_enabled}"; then
			batch_failed=1
			break
		fi
	done

	if [ "${batch_failed}" = "1" ]; then
		echo_date "规则下载或校验失败，本次未替换任何规则文件，旧规则保持不动。"
		cleanup_rule_update
		finish_rule_log_error
		return 1
	fi

	commit_batch() {
		local backup_dir="${rule_down_dir}/backup"
		local commit_dir="${RULE_SAVE_DIR}/.rule_update.$$"
		local keys_json="${rule_down_dir}/keys.json"
		local merged_manifest="${rule_down_dir}/rules.merged.json.js"
		local key=""
		local name=""

		[ -s "${updated_entries}" ] || return 0
		rm -rf "${backup_dir}" "${commit_dir}"
		mkdir -p "${backup_dir}" "${commit_dir}" || return 1
		cp -f "${RULE_FILE}" "${backup_dir}/rules.json.js" || return 1
		run jq -Rn '[inputs | select(length > 0) | split("\t")[0]]' "${updated_entries}" > "${keys_json}" || return 1
		if ! run jq -n \
			--slurpfile current "${RULE_FILE}" \
			--slurpfile online "${online_manifest}" \
			--slurpfile keys "${keys_json}" \
			'$current[0] as $current | $online[0] as $online | reduce $keys[0][] as $key ($current; .[$key] = $online[$key])' \
			> "${merged_manifest}" || [ ! -s "${merged_manifest}" ]; then
			return 1
		fi
		while IFS="$(printf '\t')" read -r key name
		do
			[ -n "${name}" ] || continue
			[ ! -f "${RULE_SAVE_DIR}/${name}" ] || cp -f "${RULE_SAVE_DIR}/${name}" "${backup_dir}/${name}" || return 1
			cp -f "${rule_stage_dir}/${name}" "${commit_dir}/${name}" || return 1
		done < "${updated_entries}"
		cp -f "${merged_manifest}" "${commit_dir}/rules.json.js" || return 1

		while IFS="$(printf '\t')" read -r key name
		do
			[ -n "${name}" ] || continue
			if ! mv -f "${commit_dir}/${name}" "${RULE_SAVE_DIR}/${name}"; then
				echo_date "替换${name}失败，正在恢复更新前规则。"
				restore_batch "${backup_dir}"
				rm -rf "${commit_dir}"
				return 1
			fi
		done < "${updated_entries}"
		if ! mv -f "${commit_dir}/rules.json.js" "${RULE_FILE}"; then
			echo_date "替换规则清单失败，正在恢复更新前规则。"
			restore_batch "${backup_dir}"
			rm -rf "${commit_dir}"
			return 1
		fi
		rm -rf "${commit_dir}"
		reboot=1
		echo_date "整批规则校验与替换完成。"
		return 0
	}

	restore_batch() {
		local backup_dir="$1"
		local key=""
		local name=""
		while IFS="$(printf '\t')" read -r key name
		do
			[ -n "${name}" ] || continue
			if [ -f "${backup_dir}/${name}" ]; then
				cp -f "${backup_dir}/${name}" "${RULE_SAVE_DIR}/${name}"
			else
				rm -f "${RULE_SAVE_DIR}/${name}"
			fi
		done < "${updated_entries}"
		cp -f "${backup_dir}/rules.json.js" "${RULE_FILE}"
	}

	if ! commit_batch; then
		echo_date "规则提交失败，本次未保留任何部分更新。"
		cleanup_rule_update
		finish_rule_log_error
		return 1
	fi

	echo_date " --------------------------------------------------------------------"
	echo_date "规则更新进程运行完毕！"
	IFS="$(printf '\037')" read -r rule_gfw_date rule_chnroute_date rule_chnlist_date rule_gfw_count rule_chnroute_count rule_chnroute_ip_count rule_chnlist_count <<-EOF
	$(rules_runtime_numbers_tsv "${RULE_FILE}")
	EOF
	nvram set update_gfwlist="${rule_gfw_date}"
	nvram set update_chnroute="${rule_chnroute_date}"
	nvram set update_chnlist="${rule_chnlist_date}"
	nvram set gfwlist_numbers="${rule_gfw_count}"
	nvram set chnroute_numbers="${rule_chnroute_count}"
	nvram set chnroute_ips="${rule_chnroute_ip_count}"
	nvram set chnlist_numbers="${rule_chnlist_count}"
	if [ "${reboot}" = "1" ] && [ "${FSS_SKIP_RESTART:-0}" != "1" ];then
		echo_date "自动重启fancyss，以应用新的规则文件！请稍后！"
		run sh /koolshare/ss/ssconfig.sh restart
	fi
	cleanup_rule_update
	echo ==================================================================================================
	return 0
}

change_cru(){
	echo ==================================================================================================
	sed -i '/ssupdate/d' /var/spool/cron/crontabs/* >/dev/null 2>&1
	if [ "1" == "${ss_basic_rule_update}" ]; then
		case "${ss_basic_rule_update_time}" in
		''|*[!0-9]*)
			echo_date "fancyss规则定时更新时间参数非法，跳过设置！"
			return 1
			;;
		esac
		if [ "${ss_basic_rule_update_time}" -lt 0 ] || [ "${ss_basic_rule_update_time}" -gt 23 ]; then
			echo_date "fancyss规则定时更新时间超出范围（0-23），跳过设置！"
			return 1
		fi
		echo_date "应用fancyss规则定时更新任务：每天${ss_basic_rule_update_time}:00自动检测更新规则."
		cru a ssupdate "0 ${ss_basic_rule_update_time} * * * /bin/sh /koolshare/scripts/ss_rule_update.sh"
	else
		echo_date "fancyss规则定时更新任务未启用！"
	fi
}

if [ "$#" -gt 1 ]; then
	case $2 in
	1)
		mkdir -p "$(dirname "${RULE_LOG_FILE}")"
		: > "${RULE_LOG_FILE}"
		http_response "$1"
		change_cru > "${RULE_LOG_FILE}" 2>&1
		echo XU6J03M6 >> "${RULE_LOG_FILE}"
		exit 0
		;;
	2)
		mkdir -p "$(dirname "${RULE_LOG_FILE}")"
		: > "${RULE_LOG_FILE}"
		http_response "$1"
		ss_basic_gfwlist_update=1
		ss_basic_chnroute_update=1
		ss_basic_chnlist_update=1
		force_update=0
		change_cru > "${RULE_LOG_FILE}" 2>&1
		rc=0
		start_update >> "${RULE_LOG_FILE}" 2>&1 || rc=$?
		if [ "${rc}" -eq 0 ]; then
			echo XU6J03M6 >> "${RULE_LOG_FILE}"
		fi
		exit "${rc}"
		;;
	esac
	exit 0
fi

case $1 in
force)
	ss_basic_gfwlist_update=1
	ss_basic_chnroute_update=1
	ss_basic_chnlist_update=1
	force_update=1
	start_update
	;;
update)
	ss_basic_gfwlist_update=1
	ss_basic_chnroute_update=1
	ss_basic_chnlist_update=1
	force_update=0
	start_update
	;;
*)
	change_cru
	start_update
	;;
esac
