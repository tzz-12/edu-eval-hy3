#!/bin/zsh
# 为校准补入的 3 个课题（14 平行四边形 / 15 正方形 / 16 平面镶嵌）生成 Tier2 断言
# 16 已完成，此脚本只跑 14/15（可用参数覆盖）
cd /Users/tzz/WorkBuddy/edu-eval-hy3 || exit 1
set -a
source .env
set +a
unset HY3_MOCK
export PYTHONPATH=/Users/tzz/WorkBuddy/edu-eval-hy3/src
PY=/Users/tzz/.workbuddy/binaries/python/versions/3.13.12/bin/python3

topics=("$@")
if (( ${#topics[@]} == 0 )); then topics=(14 15); fi

for N in "${topics[@]}"; do
  echo "===== TOPIC $N ====="
  $PY scripts/build_tier2_assertions.py --topic $N 2>&1
done
echo BUILD_DONE
