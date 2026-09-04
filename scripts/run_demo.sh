#!/usr/bin/env bash
# EduEval Demo · 一键启动 / 停止 / 查看状态
# 用法：
#   bash scripts/run_demo.sh start [PORT]   # 默认 8000
#   bash scripts/run_demo.sh stop
#   bash scripts/run_demo.sh restart
#   bash scripts/run_demo.sh status
#   bash scripts/run_demo.sh tail            # 看日志
#
# 前置：HY3_API_KEY / HY3_BASE_URL / HY3_MODEL 等需在仓库根 .env 已配置
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${2:-8000}"
PIDFILE="$ROOT/.demo.pid"
LOG="$ROOT/results/demo.log"

cmd_start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "已在运行（PID=$(cat "$PIDFILE")，端口=$PORT）"
    return 0
  fi
  if [ ! -f "$ROOT/.env" ]; then
    echo "✗ 未找到 $ROOT/.env，请先配置 HY3_API_KEY 等环境变量" >&2
    return 1
  fi
  set -a; source "$ROOT/.env"; set +a
  mkdir -p "$(dirname "$LOG")"
  echo "启动 EduEval Demo on http://0.0.0.0:$PORT（日志 $LOG）"
  nohup /Users/tzz/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
      -m uvicorn edu_eval.api.main:fastapi_app \
      --host 0.0.0.0 --port "$PORT" --log-level info \
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

case "${1:-start}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  tail)    cmd_tail ;;
  *) echo "用法: $0 {start|stop|restart|status|tail} [PORT]"; exit 2 ;;
esac
