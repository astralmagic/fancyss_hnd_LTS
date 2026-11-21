#!/bin/sh

# fancyss script for asuswrt/merlin based router with software center

source /koolshare/scripts/base.sh
ss_basic_enable=$(dbus get ss_basic_enable)
LOCK_FILE=/var/lock/fancyss.lock
SCHEMA2_POSTSAVE_IDS_KEY="fss_node_postsave_ids"

set_lock(){
	exec 1000>${LOCK_FILE}
	flock -n 1000 || {
		# bring back to original log
		http_response "$ACTION"
		# echo_date "$BASH $ARGS" | tee -a ${LOG_FILE}
		exit 1
	}
}

unset_lock() {
	flock -u 1000
	rm -rf ${LOCK_FILE}
}

pre_stop(){
	local current_pid=$$
	local ss_config_pids=$(ps|grep -E "ss_config\.sh"|awk '{print $1}'|grep -v ${current_pid})
	if [ -n "${ss_config_pids}" ];then
		for ss_config_pid in ${ss_config_pids}; do
			kill -9 ${ss_config_pid} >/dev/null 2>&1
		done
	fi

	local ssconfig_pids=$(ps|grep ssconfig.sh|grep -v grep|awk '{print $1}')
	if [ -n "${ssconfig_pids}" ];then
		for ssconfig_pid in ${ssconfig_pids}; do
			kill -9 ${ssconfig_pid} >/dev/null 2>&1
		done
	fi

	if [ -f "/var/lock/koolss.lock" ];then
		rm -rf /var/lock/koolss.lock
	fi
	
	if [ -f "/var/lock/fancyss.lock" ];then
		rm -rf /var/lock/fancyss.lock
	fi
}

stop_fancyss(){
	# start fancyss
	sh /koolshare/ss/ssconfig.sh stop
}

pre_start(){
	# 计数器
	local flag_count=0
	
	# 主脚本开启前，进行检查，看是否有ssconfig.sh进程卡住的
	local ssconfig_pids=$(ps|grep ssconfig.sh|grep -v grep|awk '{print $1}')
	if [ -n "${ssconfig_pids}" ];then
		echo "${ssconfig_pids}"
		for ssconfig_pid in ${ssconfig_pids}; do
			kill -9 ${ssconfig_pid} >/dev/null 2>&1
		done
		let flag_count+=1
	fi

	# 移除ssconfig.sh的文件锁
	if [ -f "/var/lock/koolss.lock" ];then
		rm -rf /var/lock/koolss.lock
		let flag_count+=1
	fi

	if [ "${flag_count}" -gt "0" ];then
		dbus set ss_basic_status="1"
	fi

	local postsave_ids=""
	postsave_ids="$(dbus get ${SCHEMA2_POSTSAVE_IDS_KEY})"
	if [ -n "${postsave_ids}" ] && [ -x "/koolshare/scripts/ss_node_postsave.sh" ];then
		sh /koolshare/scripts/ss_node_postsave.sh rebuild "${postsave_ids}" >/dev/null 2>&1 || true
		dbus remove ${SCHEMA2_POSTSAVE_IDS_KEY}
	fi
}

start_fancyss(){
	# start fancyss
	sh /koolshare/ss/ssconfig.sh restart
}

start_fancyss_shunt_hot(){
	local need_dns_flag="${HOT_NEED_DNS_RELOAD_FILE:-/tmp/fancyss_shunt_hot_reload/need_dns_reload}"
	if [ "$(dbus get ss_basic_shunt_hot_reload)" = "1" ] && [ -x "/koolshare/scripts/ss_shunt_hot_reload.sh" ]; then
		echo_date "[hot-reload] 尝试通过 Xray API 热更新节点分流规则..."
		if sh /koolshare/scripts/ss_shunt_hot_reload.sh apply; then
			if [ -f "${need_dns_flag}" ]; then
				rm -f "${need_dns_flag}" >/dev/null 2>&1 || true
				echo_date "[hot-reload] 检测到分流域名或新节点白名单变化，自动联动局部重载 DNS..."
				if ! sh /koolshare/ss/ssconfig.sh reload_scopes "dns"; then
					echo_date "[hot-reload] 联动局部重载 DNS 失败，回退到完整重启。"
					start_fancyss
					return
				fi
			fi
			return 0
		fi
		echo_date "[hot-reload] 热更新失败，回退到完整重启。"
	fi
	start_fancyss
}

save_only_fancyss(){
	echo_date ======================= 梅林固件 - 【科学上网】 ========================
	echo_date
	echo_date "[partial-reload] 本次修改仅涉及保存项（界面/订阅/测速偏好等），无需重启服务。"
	echo_date "[partial-reload] 配置已保存生效！"
	echo_date
	echo_date ======================= 梅林固件 - 【科学上网】 ========================
	return 0
}

reload_fancyss_scopes(){
	local raw_arg="$1"
	local scopes_raw="${raw_arg#reload_}"
	local scope=""
	local need_shunt_hot="0"
	local ssconfig_scopes=""
	local old_ifs="${IFS}"

	if [ -z "${scopes_raw}" ] || ! printf '%s' "${scopes_raw}" | grep -Eq '^[a-z0-9_-]+$'; then
		echo_date "[partial-reload] 参数格式非法（${raw_arg}），回退到完整重启。"
		start_fancyss
		return
	fi

	IFS='-'
	for scope in ${scopes_raw}; do
		[ -n "${scope}" ] || continue
		case "${scope}" in
		shunt_hot)
			need_shunt_hot="1"
			;;
		ipset|dnsmasq|dns|iptables|cron|status)
			if [ -n "${ssconfig_scopes}" ]; then
				ssconfig_scopes="${ssconfig_scopes}-${scope}"
			else
				ssconfig_scopes="${scope}"
			fi
			;;
		*)
			IFS="${old_ifs}"
			echo_date "[partial-reload] 未识别的局部重载域（${scope}），回退到完整重启。"
			start_fancyss
			return
			;;
		esac
	done
	IFS="${old_ifs}"

	if [ "${need_shunt_hot}" = "0" ] && [ -z "${ssconfig_scopes}" ]; then
		echo_date "[partial-reload] 无有效局部重载域，回退到完整重启。"
		start_fancyss
		return
	fi

	if [ "${need_shunt_hot}" = "1" ]; then
		local need_dns_flag="${HOT_NEED_DNS_RELOAD_FILE:-/tmp/fancyss_shunt_hot_reload/need_dns_reload}"
		if [ "$(dbus get ss_basic_shunt_hot_reload)" = "1" ] && [ -x "/koolshare/scripts/ss_shunt_hot_reload.sh" ]; then
			echo_date "[hot-reload] 尝试通过 Xray API 热更新节点分流规则..."
			if ! sh /koolshare/scripts/ss_shunt_hot_reload.sh apply; then
				echo_date "[hot-reload] 热更新失败，回退到完整重启。"
				start_fancyss
				return
			fi
			if [ -f "${need_dns_flag}" ]; then
				rm -f "${need_dns_flag}" >/dev/null 2>&1 || true
				case "-${ssconfig_scopes}-" in
				*-dns-*)
					;;
				*)
					echo_date "[hot-reload] 检测到分流域名或新节点白名单变化，自动追加局部重载 DNS。"
					if [ -n "${ssconfig_scopes}" ]; then
						ssconfig_scopes="dns-${ssconfig_scopes}"
					else
						ssconfig_scopes="dns"
					fi
					;;
				esac
			fi
		else
			echo_date "[hot-reload] 分流热重载未启用或脚本不可用，回退到完整重启。"
			start_fancyss
			return
		fi
	fi

	if [ -n "${ssconfig_scopes}" ]; then
		if ! sh /koolshare/ss/ssconfig.sh reload_scopes "${ssconfig_scopes}"; then
			echo_date "[partial-reload] 局部重载（${ssconfig_scopes}）失败，自动回退到完整重启。"
			start_fancyss
			return
		fi
	fi
}

# call by ws
case $1 in
start)
	set_lock
	true > /tmp/upload/ss_log.txt
	pre_start
	start_fancyss 2>&1 | tee -a /tmp/upload/ss_log.txt
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
start_shunt_hot)
	set_lock
	true > /tmp/upload/ss_log.txt
	pre_start
	start_fancyss_shunt_hot 2>&1 | tee -a /tmp/upload/ss_log.txt
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
save_only)
	set_lock
	true > /tmp/upload/ss_log.txt
	pre_start
	save_only_fancyss 2>&1 | tee -a /tmp/upload/ss_log.txt
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
reload_*)
	set_lock
	true > /tmp/upload/ss_log.txt
	pre_start
	reload_fancyss_scopes "$1" 2>&1 | tee -a /tmp/upload/ss_log.txt
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
start_by_ws)
	set_lock
	true > /tmp/upload/ss_log.txt
	pre_start
	start_fancyss 2>&1 | tee -a /tmp/upload/ss_log.txt
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
stop)
	# 为了避免ss_config.sh本身也卡住，所以stop过程不使用文件锁，强行关闭
	true > /tmp/upload/ss_log.txt
	pre_stop
	stop_fancyss | tee -a /tmp/upload/ss_log.txt 2>&1
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	rm -rf ${LOCK_FILE}
	;;
test)
	sleep 100
	;;
esac

# call by httpdb
case $2 in
start)
	set_lock
	true > /tmp/upload/ss_log.txt
	http_response "$1"
	pre_start
	start_fancyss | tee -a /tmp/upload/ss_log.txt 2>&1
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
start_shunt_hot)
	set_lock
	true > /tmp/upload/ss_log.txt
	http_response "$1"
	pre_start
	start_fancyss_shunt_hot | tee -a /tmp/upload/ss_log.txt 2>&1
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
save_only)
	set_lock
	true > /tmp/upload/ss_log.txt
	http_response "$1"
	pre_start
	save_only_fancyss | tee -a /tmp/upload/ss_log.txt 2>&1
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
reload_*)
	set_lock
	true > /tmp/upload/ss_log.txt
	http_response "$1"
	pre_start
	reload_fancyss_scopes "$2" | tee -a /tmp/upload/ss_log.txt 2>&1
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
start_by_ws)
	set_lock
	true > /tmp/upload/ss_log.txt
	pre_start
	start_fancyss | tee -a /tmp/upload/ss_log.txt 2>&1
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	unset_lock
	;;
stop)
	# 为了避免ss_config.sh本身也卡住，所以stop过程不使用文件锁，强行关闭
	true > /tmp/upload/ss_log.txt
	http_response "$1"
	pre_stop
	stop_fancyss | tee -a /tmp/upload/ss_log.txt 2>&1
	echo XU6J03M6 | tee -a /tmp/upload/ss_log.txt
	rm -rf ${LOCK_FILE}
	;;
test)
	sleep 100
	;;
esac
