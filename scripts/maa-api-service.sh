#!/usr/bin/env bash
# MAA-API 的 launchd 服务管理（macOS LaunchAgent）。
#
#     ./scripts/maa-api-service.sh <命令>
#
# launchd 负责保活：登录即起、进程退出即被拉起。本脚本把 launchctl 的各类子命令与
# 文件位置收在一处，避免每次手敲 launchctl 时记错 domain、标签或 plist 路径。
#
# 标签与 plist 路径是刻意固定的：
#   label  local.maa-api
#   plist  ~/Library/LaunchAgents/local.maa-api.plist
# 由 scripts/launchd/local.maa-api.plist.template 渲染而来 —— 不要直接编辑
# ~/Library/LaunchAgents 下那份，改模板后重新 install。
#
# 环境变量：MAA_PORT 覆盖端口（默认 8002，与 scripts/start-maa-api.sh 一致）。
set -euo pipefail

LABEL="local.maa-api"
DOMAIN="gui/$(id -u)"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/launchd/local.maa-api.plist.template"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
START_SCRIPT="$REPO_ROOT/scripts/start-maa-api.sh"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
CONFIG="$REPO_ROOT/config.yaml"
LOG_DIR="$REPO_ROOT/resource/log"
LAUNCHD_OUT="$LOG_DIR/uvicorn.out"
LAUNCHD_ERR="$LOG_DIR/uvicorn.err"
PORT="${MAA_PORT:-8002}"
TRUNCATE_BYTES=$((2 * 1024 * 1024))
#: 应用日志的日期戳（maa_api/log.py 用自己的本地时间命名文件）
TODAY="$(date +%Y-%m-%d)"

if [[ -t 1 ]]; then
  C_OK=$'\033[32m'; C_ERR=$'\033[31m'; C_WARN=$'\033[33m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_OK=; C_ERR=; C_WARN=; C_DIM=; C_OFF=
fi
ok()   { printf '%s✓%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s!%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
err()  { printf '%s✗%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; }
dim()  { printf '%s  %s%s\n' "$C_DIM" "$*" "$C_OFF"; }
step() { printf '\n%s\n' "$*"; }

die() { err "$*"; exit 1; }

usage() {
  cat <<EOF
MAA-API 的 launchd 服务管理（macOS）

用法：./scripts/maa-api-service.sh <命令> [参数]

  install          渲染模板并安装/更新 LaunchAgent（已加载则先卸载），随后启动
  uninstall        停止并删除 LaunchAgent（默认保留日志，--purge-logs 一并删除）
  start            加载并启动（已加载且已停用时先恢复启用）
  stop             停止并卸载。KeepAlive 仍处于启用状态，reload/重登会再次拉起
  restart          重启服务（等价于 launchctl kickstart -k）
  reload           restart 的别名：改了 config.yaml 之后用它
  enable           允许开机/登录自启（与 disable 配对）
  disable          停用自启：立即停止，且之后不再被拉起，直到 enable
  status           查看 plist、加载状态、PID、端口监听与日志位置
  logs [n] [-f]    打印最近访问日志与错误日志（默认 40 行，-f 持续跟随）
  paths            只打印各文件路径，方便喂给其它命令或编辑器
  check            只做启动前体检，不改动任何状态

环境变量：
  MAA_PORT         端口，默认 8002（须与 start-maa-api.sh 实际监听一致）

文件位置：
  plist            $PLIST
  模板             $TEMPLATE
  启动器           $START_SCRIPT
  解释器           $VENV_PYTHON
  配置             $CONFIG
  日志             $LOG_DIR/YYYY-MM-DD.log（应用）、uvicorn.out / uvicorn.err（launchd 兜底）
EOF
}

# --------------------------------------------------------------------------
# 只读探测
# --------------------------------------------------------------------------

# launchctl print 在「服务不存在」时退出码不是 0，但也不保证稳定；按输出文本判定。
is_loaded() {
  local out
  out="$(launchctl print "$DOMAIN/$LABEL" 2>&1 || true)"
  [[ "$out" != *"Could not find service"* ]]
}

# 取 launchctl print 里的一行键值，例如 pid / state / program。
info() {
  launchctl print "$DOMAIN/$LABEL" 2>/dev/null \
    | sed -nE "s/^[[:space:]]*$1 = (.*)$/\1/p" \
    | head -1
}

# 监听指定端口的进程 pid（取不到就输出空）。优先 lsof，退化到 nc 只判有无。
#
# 注意结尾的 `|| true`：`set -o pipefail` 下，端口空闲时 lsof 退出码 1 会让整条管道
# 返回 1，`$(...)` 赋值随即失败并被 `set -e` 静默杀掉脚本（实测踩到过）。凡是
# 「正常可能一无所获」的管道都要显式兜底。
port_pid() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1 || true
  elif nc -z 127.0.0.1 "$PORT" 2>/dev/null; then
    echo unknown
  fi
}

# 占用某端口的进程名（lsof 的 c 字段，如 Python / node）。取不到就输出空。
port_process_name() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -Fc 2>/dev/null | sed -n 's/^c//p' | head -1 || true
  fi
}

# 占用某端口的进程的完整命令行。macOS 的 lsof 只给 comm（截断到 16 字符、且是可执行
# 文件名，不含 `python -m uvicorn ...` 这类参数），所以优先 ps；但**不能依赖它**：
# 受限 shell 下 `ps -o command=` 会静默返回空。
port_process_command() {
  local pid="$1"
  [[ -n "$pid" && "$pid" != unknown ]] || return 0
  ps -o command= -p "$pid" 2>/dev/null || true
}

# 该 pid 是不是本项目的服务。判定顺序：
#   1. 命令行含 maa_api.main（最准）
#   2. 进程名是 python/uvicorn（ps 不可用时的退化判据）
#   3. 该进程打开的文件位于本仓库内（拿 cwd 反查）
# 只看「是不是 python」确实不够精确，但比「监听中的端口一律算别人的进程」好：
# 误判最多是多停一个 python 实例，误报则会让 install 直接拒绝服务。
is_our_service() {
  local pid="$1"
  [[ -n "$pid" && "$pid" != unknown ]] || return 1
  local cmd
  cmd="$(port_process_command "$pid")"
  [[ -n "$cmd" ]] && grep -q "maa_api\.main" <<<"$cmd" && return 0
  local name
  name="$(port_process_name)"
  [[ "$name" == "Python" || "$name" == python* || "$name" == uvicorn* ]] && return 0
  if command -v lsof >/dev/null 2>&1; then
    lsof -p "$pid" -Fn 2>/dev/null | grep -q "^n$REPO_ROOT" && return 0
  fi
  return 1
}

wait_port_free() {
  local deadline=$((SECONDS + 15))
  while ((SECONDS < deadline)); do
    [[ -z "$(port_pid)" ]] && return 0
    sleep 0.5
  done
  return 1
}

# KeepAlive 反复重启时日志会持续增长；启动前把 launchd 兜底日志截到最后 2MB。
truncate_log() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  [[ -n "$(find "$f" -size +${TRUNCATE_BYTES}c 2>/dev/null)" ]] || return 0
  local tmp="$f.tmp"
  tail -c "$TRUNCATE_BYTES" "$f" >"$tmp" && mv "$tmp" "$f"
  dim "已截断日志：$(basename "$f") → 保留最后 2MB"
}

# --------------------------------------------------------------------------
# 前置检查
# --------------------------------------------------------------------------

require_macos() {
  [[ "$(uname -s)" == "Darwin" ]] \
    || die "launchd 只存在于 macOS（当前 $(uname -s)）；Linux 请用 systemd，容器请用 restart 策略"
}

# 渲染模板到 $PLIST：占位符替换 + 语法校验 + 显式 chmod。
# 先写临时文件再原子 mv，避免 plutil 失败时留下半成品；原文件备份在 .bak。
render_plist() {
  [[ -f "$TEMPLATE" ]] || die "找不到模板：$TEMPLATE"
  [[ -f "$START_SCRIPT" ]] || die "找不到启动器：$START_SCRIPT"

  local python_bin="${MAA_PYTHON:-$VENV_PYTHON}"
  [[ -x "$python_bin" ]] || die "解释器不可执行：$python_bin（用 MAA_PYTHON 覆盖，或先建 .venv）"
  [[ -f "$CONFIG" ]] || warn "没找到 $CONFIG，服务大概率起不来（从 config.template.yaml 复制一份）"

  local tmp="$PLIST.rendered.$$"
  mkdir -p "$(dirname "$PLIST")"
  # 路径里不该出现 "|"；真出现就报错而不是渲染出一份坏 plist
  if grep -q '|' <<<"$REPO_ROOT$python_bin"; then
    die "路径里含 '|'，无法安全渲染：$REPO_ROOT / $python_bin"
  fi
  sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__PYTHON_BIN__|$python_bin|g" \
    "$TEMPLATE" >"$tmp"
  if grep -q '__REPO_ROOT__\|__PYTHON_BIN__' "$tmp"; then
    rm -f "$tmp"
    die "模板里还有未替换的占位符，检查 $TEMPLATE"
  fi
  if ! plutil -lint "$tmp" >/dev/null; then
    rm -f "$tmp"
    die "渲染出的 plist 语法不合法，模板有问题：$TEMPLATE"
  fi
  [[ -f "$PLIST" ]] && cp -p "$PLIST" "$PLIST.bak"
  mv "$tmp" "$PLIST"
  chmod 644 "$PLIST"
  ok "已写入 $PLIST"
  [[ -f "$PLIST.bak" ]] && dim "上一版备份在 $PLIST.bak"
  return 0
}

# --------------------------------------------------------------------------
# 生命周期
# --------------------------------------------------------------------------

# 起服务前腾出端口：只动本项目自己的 uvicorn，别的进程占用就明确报错。
ensure_port_free() {
  local pid name detail
  pid="$(port_pid)"
  [[ -z "$pid" ]] && return 0
  name="$(port_process_name)"
  if ! is_our_service "$pid"; then
    err "端口 $PORT 被其它进程占用（pid=$pid${name:+，进程名=$name}）："
    detail="$(port_process_command "$pid")"
    [[ -n "$detail" ]] && sed 's/^/    /' <<<"$detail"
    die "先停掉它，或换端口：MAA_PORT=<端口> $0 start（start-maa-api.sh 同步生效）"
  fi
  warn "端口 $PORT 上已有本项目实例（pid=$pid），先优雅停掉它"
  kill "$pid" 2>/dev/null || true
  wait_port_free || die "端口 $PORT 在 15 秒内没释放，检查进程 $pid"
  ok "端口 $PORT 已释放"
}

bootout_service() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || return 0
}

stop_service() {
  local pid
  pid="$(info pid)"
  bootout_service
  # KeepAlive 已随 bootout 一起撤销；若端口仍被占（旧进程还在收尾），等它退干净
  [[ -n "$(port_pid)" ]] && wait_port_free || true
  if [[ -n "$pid" ]]; then
    ok "已停止（原 pid=$pid）"
  else
    ok "已停止"
  fi
}

start_service() {
  [[ -f "$PLIST" ]] || die "还没安装，先跑：$0 install"
  ensure_port_free
  mkdir -p "$LOG_DIR"
  truncate_log "$LAUNCHD_OUT"
  truncate_log "$LAUNCHD_ERR"
  # 已加载则原地重启，未加载则加载（bootstrap 顺带触发 RunAtLoad）
  if is_loaded; then
    launchctl kickstart -k "$DOMAIN/$LABEL"
    ok "已重启"
  else
    if ! launchctl bootstrap "$DOMAIN" "$PLIST"; then
      err "launchctl bootstrap 失败。launchd 的报错一向含糊，按这个顺序排查："
      dim "1. 确认 plist 真的在磁盘上且可读：ls -l $PLIST"
      dim "2. 手工跑一次启动器看它能否起来：$START_SCRIPT"
      dim "3. 'Input/output error' 通常是 launchd 读不到 plist（路径/权限/文件不存在）"
      dim "4. 端口被别的实例占着时，先 $0 stop 再 $0 start"
      return 1
    fi
    ok "已加载并启动"
  fi
  sleep 1.5
  # 只用一个变量、且先赋默认值：`set -u` 下任何未绑定的展开都会让脚本在**回显**这一步
  # 直接死掉，而服务其实已经起来了——最糟的失败方式。变量一律 init 后再读。
  local pid="" state=""
  pid="$(info pid || true)"
  state="$(info state || true)"
  if [[ -n "$pid" ]]; then
    ok "运行中：pid=$pid，state=$state"
  else
    warn "launchd 已接管，但还没看到 pid（state=${state:-未知}）"
    dim "复查：$0 status       诊断：launchctl print $DOMAIN/$LABEL | grep -E 'state|pid'"
  fi
}

# 尝试启动脚本自己的配置读取，提前暴露「服务起不来」的典型原因。
preflight() {
  step "体检"
  local failures=0

  if [[ -f "$PLIST" ]]; then
    ok "plist 已安装：$PLIST"
  else
    warn "plist 尚未安装（跑 $0 install）"
  fi

  if [[ -x "${MAA_PYTHON:-$VENV_PYTHON}" ]]; then
    ok "解释器可执行：${MAA_PYTHON:-$VENV_PYTHON}"
  else
    err "解释器不可执行：${MAA_PYTHON:-$VENV_PYTHON}"
    failures=$((failures + 1))
  fi

  if [[ -f "$CONFIG" ]]; then
    ok "配置文件存在：$CONFIG"
  else
    err "缺少 $CONFIG"
    failures=$((failures + 1))
  fi

  # 启动器会 import maa_api 读设置；这一步失败，服务起来也会立刻退出
  if "${MAA_PYTHON:-$VENV_PYTHON}" -c 'import maa_api.main, maa_api.settings' 2>/dev/null; then
    ok "maa_api 可导入（settings 与 main 装配正常）"
  else
    err "import maa_api 失败，服务会被 launchd 反复重启；手工跑一次看 traceback："
    dim "${MAA_PYTHON:-$VENV_PYTHON} -c 'import maa_api.main'"
    failures=$((failures + 1))
  fi

  local pid
  pid="$(port_pid)"
  if [[ -z "$pid" ]]; then
    ok "端口 $PORT 空闲"
  elif is_our_service "$pid"; then
    warn "端口 $PORT 上已有本项目实例（pid=$pid）；install/start 会先停掉它"
  else
    err "端口 $PORT 被其它进程占用（pid=$pid）"
    failures=$((failures + 1))
  fi

  if command -v adb >/dev/null 2>&1; then
    ok "adb 可用：$(command -v adb)"
  else
    warn "PATH 里没有 adb；内核连设备时会失败（launchd 的 PATH 已含 /opt/homebrew/bin 与 /usr/local/bin）"
  fi

  if ((failures > 0)); then
    err "体检未通过：$failures 项需要处理"
    return 1
  fi
  ok "体检通过"
  return 0
}

# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

cmd_install() {
  require_macos
  step "安装 LaunchAgent"
  preflight || die "先修掉上面几项再安装"
  render_plist

  if is_loaded; then
    dim "已加载的旧实例先卸载"
    bootout_service
  fi
  # 曾被执行过 disable 的标签要显式恢复，否则 bootstrap 起来也不会自启
  launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
  start_service

  step "完成"
  dim "看状态：$0 status       看日志：$0 logs -f"
  dim "改完 config.yaml：$0 reload"
  dim "首次加载时 macOS 可能弹「后台项目已添加」通知，属正常"
}

cmd_uninstall() {
  require_macos
  local purge=0
  [[ "${1:-}" == "--purge-logs" ]] && purge=1
  step "卸载 LaunchAgent"
  if is_loaded; then
    stop_service
  else
    dim "服务未加载"
  fi
  launchctl disable "$DOMAIN/$LABEL" 2>/dev/null || true
  if [[ -f "$PLIST" ]]; then
    rm -f "$PLIST"
    ok "已删除 $PLIST"
  fi
  [[ -f "$PLIST.bak" ]] && { rm -f "$PLIST.bak"; dim "已删除 $PLIST.bak"; }
  if ((purge)); then
    rm -f "$LAUNCHD_OUT" "$LAUNCHD_ERR"
    ok "已删除 launchd 兜底日志（应用日志 $LOG_DIR 保留）"
  fi
  dim "仓库内的模板、脚本都没动；需要恢复就再 install"
}

cmd_start() {
  require_macos
  [[ -f "$PLIST" ]] || die "还没安装，先跑：$0 install"
  step "启动服务"
  launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
  start_service
}

cmd_stop() {
  require_macos
  step "停止服务"
  if ! is_loaded; then
    dim "服务未加载"
    # 未加载也可能有手工起的实例占着端口
    local pid
    pid="$(port_pid)"
    if is_our_service "$pid"; then
      ensure_port_free
    fi
    return 0
  fi
  stop_service
  dim "注意：KeepAlive 仍处于启用状态，重新登录或 reload 会再次拉起；要彻底停用用 $0 disable"
}

cmd_restart() {
  require_macos
  [[ -f "$PLIST" ]] || die "还没安装，先跑：$0 install"
  step "重启服务"
  if ! is_loaded; then
    warn "服务未加载，改为直接启动"
    start_service
    return 0
  fi
  mkdir -p "$LOG_DIR"
  truncate_log "$LAUNCHD_OUT"
  truncate_log "$LAUNCHD_ERR"
  local old_pid
  old_pid="$(info pid)"
  launchctl kickstart -k "$DOMAIN/$LABEL"
  sleep 1.5
  ok "已重启（旧 pid=${old_pid:-未知} → 新 pid=$(info pid)）"
}

cmd_enable() {
  require_macos
  launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
  ok "已允许自启（$LABEL）"
  is_loaded || dim "服务当前未加载，需要立即运行请用 $0 start"
}

cmd_disable() {
  require_macos
  local pid
  pid="$(info pid)"
  launchctl disable "$DOMAIN/$LABEL" 2>/dev/null || true
  bootout_service
  [[ -n "$(port_pid)" ]] && wait_port_free || true
  ok "已停用自启并停止服务（${pid:+原 pid=$pid}）"
  dim "恢复：$0 enable && $0 start"
}

cmd_status() {
  require_macos
  step "MAA-API 服务状态"

  if [[ -f "$PLIST" ]]; then
    ok "plist 存在：$PLIST"
  else
    warn "plist 不存在（尚未安装）：$PLIST"
  fi

  if is_loaded; then
    local pid state
    pid="$(info pid)"; state="$(info state)"
    ok "launchd：已加载（state=${state:-未知}，pid=${pid:-无}）"
    local program
    program="$(info program)"
    [[ -n "$program" ]] && dim "program: $program"
  else
    warn "launchd：未加载"
  fi

  # 自启是否被 disable 掉，决定 bootout 之后会不会被拉回来
  if launchctl print-disabled "$DOMAIN" 2>/dev/null | grep -q "\"$LABEL\" => disabled"; then
    warn "自启：已停用（$0 enable 恢复）"
  else
    dim "自启：启用"
  fi

  local pid
  pid="$(port_pid)"
  if is_our_service "$pid"; then
    ok "端口 $PORT：本项目服务在监听（pid=$pid）"
  elif [[ -n "$pid" ]]; then
    err "端口 $PORT：被其它进程占用（pid=$pid）"
  else
    dim "端口 $PORT：无监听"
  fi

  step "日志"
  if [[ -f "$LOG_DIR/$TODAY.log" ]]; then
    ok "应用日志：$LOG_DIR/$TODAY.log（$(wc -l <"$LOG_DIR/$TODAY.log" | tr -d ' ') 行）"
  else
    dim "今天的应用日志还不存在：$LOG_DIR/$TODAY.log（服务没起来过或刚跨天）"
  fi
  for f in "$LAUNCHD_OUT" "$LAUNCHD_ERR"; do
    if [[ -s "$f" ]]; then
      dim "$(basename "$f")：$(wc -c <"$f" | tr -d ' ') 字节，最后修改 $(date -r "$f" '+%m-%d %H:%M')"
    fi
  done
  dim "跟随日志：$0 logs -f"
}

cmd_logs() {
  local lines=40 follow=""
  while (($#)); do
    case "$1" in
      -f|--follow) follow="-f" ;;
      ''|*[!0-9]*) die "无法识别的参数：$1（用法：logs [行数] [-f]）" ;;
      *) lines="$1" ;;
    esac
    shift
  done

  step "应用日志 $LOG_DIR/$TODAY.log（末 $lines 行）"
  if [[ -f "$LOG_DIR/$TODAY.log" ]]; then
    tail -n "$lines" "$LOG_DIR/$TODAY.log"
  else
    dim "不存在：$LOG_DIR/$TODAY.log"
  fi

  step "启动期兜底日志（只在这里能看 venv 缺失、config 语法错这类早期失败）"
  for f in "$LAUNCHD_ERR" "$LAUNCHD_OUT"; do
    [[ -s "$f" ]] || continue
    printf '\n%s\n' "${C_DIM}--- $(basename "$f") ---${C_OFF}"
    tail -n "$lines" "$f"
  done

  if [[ -n "$follow" ]]; then
    step "持续跟随（Ctrl-C 退出）"
    exec tail -n 0 -F "$LAUNCHD_ERR" "$LAUNCHD_OUT" "$LOG_DIR/$TODAY.log"
  fi
}

cmd_paths() {
  printf 'label       %s\n' "$LABEL"
  printf 'domain      %s\n' "$DOMAIN"
  printf 'plist       %s\n' "$PLIST"
  printf 'template    %s\n' "$TEMPLATE"
  printf 'launcher    %s\n' "$START_SCRIPT"
  printf 'python      %s\n' "${MAA_PYTHON:-$VENV_PYTHON}"
  printf 'config      %s\n' "$CONFIG"
  printf 'log_dir     %s\n' "$LOG_DIR"
  printf 'log_today   %s\n' "$LOG_DIR/$TODAY.log"
  printf 'log_stderr  %s\n' "$LAUNCHD_ERR"
  printf 'log_stdout  %s\n' "$LAUNCHD_OUT"
  printf 'port        %s\n' "$PORT"
}

cmd_check() {
  require_macos
  preflight || return 1
  return 0
}

main() {
  local cmd="${1:-}"
  [[ $# -gt 0 ]] && shift || true
  case "$cmd" in
    install)   cmd_install "$@" ;;
    uninstall) cmd_uninstall "$@" ;;
    start)     cmd_start "$@" ;;
    stop)      cmd_stop "$@" ;;
    restart|reload) cmd_restart "$@" ;;
    enable)    cmd_enable "$@" ;;
    disable)   cmd_disable "$@" ;;
    status)    cmd_status "$@" ;;
    logs)      cmd_logs "$@" ;;
    paths)     cmd_paths "$@" ;;
    check)     cmd_check "$@" ;;
    ""|-h|--help|help) usage ;;
    *) usage >&2; die "未知命令：$cmd" ;;
  esac
}

main "$@"
