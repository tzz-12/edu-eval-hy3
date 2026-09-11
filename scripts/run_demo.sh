#!/usr/bin/env bash
# EduEval Demo · 一键启动 / 停止 / 查看状态
# 用法：
#   bash scripts/run_demo.sh start [PORT]              # 默认 8000；未设 HY3_MOCK 时走 mock
#   bash scripts/run_demo.sh start [PORT] --live       # 强制走真实 API（需 .env 已配 HY3_API_KEY）
#   bash scripts/run_demo.sh stop
#   bash scripts/run_demo.sh restart [PORT] [--live]
#   bash scripts/run_demo.sh status
#   bash scripts/run_demo.sh tail                       # 看日志
#   bash scripts/run_demo.sh pregen [--live]            # 用 mock / 真实 API 重生 data/demo_reports/
#
# 行为：
#   默认 HY3_MOCK=1 + 缓存目录指到 /tmp/edu_eval_demo_cache，避免 sandbox 拦截
#   `results/.cache/` 下的 os.replace（PermissionError）。要跑真实评测传 --live。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${2:-8000}"
FLAG="${3:-}"
PIDFILE="$ROOT/.demo.pid"
LOG="$ROOT/results/demo.log"

is_live() { [ "$FLAG" = "--live" ]; }

ensure_paths() {
  mkdir -p "$(dirname "$LOG")"
  # SQLite 历史库路径由 storage.py 启动探测决定（result/demo.db 不可写时自动降级到
  # 系统临时目录），这里不强制指定，正常终端下即可落库持久化。
  if ! is_live; then
    # 走 mock：缓存放 /tmp 避免 sandbox 拦截；前置 mock 开关
    export HY3_MOCK=1
    export EDU_EVAL_CACHE_DIR="${EDU_EVAL_CACHE_DIR:-/tmp/edu_eval_demo_cache}"
  else
    # 走真实 API：缓存也放 /tmp，避免 sandbox 拦截
    export EDU_EVAL_CACHE_DIR="${EDU_EVAL_CACHE_DIR:-/tmp/edu_eval_demo_cache_live}"
  fi
  # 关键：必须用「:+$ROOT/src」追加而非「:-」默认。
  # WorkBuddy shell 已默认设 PYTHONPATH 指向其 vendor shim（与项目 src 无关），
  # 默认值表达式在该变量已设时不会覆盖，导致 edu_eval 模块找不到 → ModuleNotFoundError。
  export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
}

cmd_start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "已在运行（PID=$(cat "$PIDFILE")，端口=$PORT）"
    return 0
  fi
  if is_live && [ ! -f "$ROOT/.env" ]; then
    echo "✗ 未找到 $ROOT/.env，请先配置 HY3_API_KEY 等环境变量" >&2
    return 1
  fi
  ensure_paths
  if [ -f "$ROOT/.env" ]; then
    set -a; source "$ROOT/.env"; set +a
  fi
  mode="$(is_live && echo 'live' || echo 'mock')"
  echo "启动 EduEval Demo on http://127.0.0.1:${PORT} (mode=${mode}, log=${LOG})"
  nohup /Users/tzz/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
      -m uvicorn edu_eval.api.main:fastapi_app \
      --host "${HOST:-127.0.0.1}" --port "$PORT" --log-level info \
      >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 2
  if curl -s "http://127.0.0.1:$PORT/api/health" > /dev/null; then
    echo "✓ 启动成功，PID=$(cat "$PIDFILE")"
    echo "  → 浏览器打开 http://localhost:$PORT"
  else
    echo "✗ 启动失败，查看日志：tail -50 $LOG" >&2
    return 1
  fi
}

cmd_stop() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    PID="$(cat "$PIDFILE")"
    kill "$PID"
    rm -f "$PIDFILE"
    echo "✓ 已停止 PID=$PID"
  else
    echo "未在运行"
    rm -f "$PIDFILE"
  fi
}

cmd_restart() {
  cmd_stop || true
  cmd_start
}

cmd_status() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "运行中 PID=$(cat "$PIDFILE") 端口=$PORT"
    curl -s "http://127.0.0.1:$PORT/api/health" || true
    echo
  else
    echo "未运行"
  fi
}

cmd_tail() {
  tail -50 "$LOG" 2>/dev/null || echo "无日志"
}

cmd_pregen() {
  ensure_paths
  if is_live; then
    if [ ! -f "$ROOT/.env" ]; then
      echo "✗ 未找到 $ROOT/.env，请先配置 HY3_API_KEY 等环境变量" >&2
      return 1
    fi
    set -a; source "$ROOT/.env"; set +a
    unset HY3_MOCK
  fi
  /Users/tzz/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
      "$ROOT/scripts/pregen_demo_reports.py"
}

case "${1:-start}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  tail)    cmd_tail ;;
  pregen)  FLAG="${2:-}"; cmd_pregen ;;
  *) echo "用法: $0 {start|stop|restart|status|tail|pregen} [PORT|--live]"; exit 2 ;;
esac
