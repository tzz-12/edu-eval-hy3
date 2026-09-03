#!/bin/zsh
# 校准 v3：清洗版 Markdown + 16 课题断言集 + 定向补录（平移规律 / 不稳定性）
# v2 已证明扩题解封了 2 份样本，v3 补上断言粒度缺口后再跑一遍做终版
cd /Users/tzz/WorkBuddy/edu-eval-hy3 || exit 1
set -a
source .env
set +a
unset HY3_MOCK
export PYTHONPATH=/Users/tzz/WorkBuddy/edu-eval-hy3/src
PY=/Users/tzz/.workbuddy/binaries/python/versions/3.13.12/bin/python3
mkdir -p results/calibration_v3

$PY -m edu_eval.cli "data/samples/calibration_md/二次函数的图象和性质.md" --grade 九年级 --version 人教版 --topic 二次函数的图象和性质 --period 1课时 --json --out results/calibration_v3/quadratic.json 2>&1 | tail -1
$PY -m edu_eval.cli "data/samples/calibration_md/平行四边形性质.md" --grade 八年级 --version 人教版 --topic 平行四边形的性质 --period 1课时 --json --out results/calibration_v3/parallelogram.json 2>&1 | tail -1
$PY -m edu_eval.cli "data/samples/calibration_md/丰富多彩的正方形.md" --grade 八年级 --version 人教版 --topic 正方形 --period 1课时 --json --out results/calibration_v3/square.json 2>&1 | tail -1
$PY -m edu_eval.cli "data/samples/calibration_md/平面镶嵌.md" --grade 八年级 --version 人教版 --topic 平面镶嵌 --period 1课时 --json --out results/calibration_v3/tessellation.json 2>&1 | tail -1
echo ALL_DONE
