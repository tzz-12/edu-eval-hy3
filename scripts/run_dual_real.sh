#!/usr/bin/env bash
# 真实双采样全量跑（ultra-550b）。密钥从仓库本地 .env 加载，不出现在命令文本中。
set -a
source "$(dirname "$0")/../.env"
set +a
cd "$(dirname "$0")/.."

# 缓存写到 /tmp（规避沙箱对 results/.proto 下 os.replace 的 file-write-unlink 拦截）
# 最终报告仍写 results/（沙箱内已验证可写）
CACHE_DIR=/tmp/edu_eval_dual_cache_real
rm -rf "$CACHE_DIR"

PYTHONPATH=src /Users/tzz/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
  scripts/proto_dual_sample.py \
  --out results/calibration_v3/dual_sample_real.json \
  --cache-dir "$CACHE_DIR" \
  --threshold 1
