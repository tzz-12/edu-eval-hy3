#!/usr/bin/env python3
"""生成 EduEval 演示视频（docs/demo.mp4）。

按 `docs/demo_video.md` 的分镜脚本自动操作浏览器并逐帧截图，
再经 ffmpeg 合成为 30fps H.264 MP4。生成过程无需人工操作，可重复执行，
用于在演示报告更新后重新产出视频。

前置条件：
    pip install playwright
    playwright install chromium-headless-shell
    demo 服务已在本机启动（bash scripts/run_demo.sh start 8000 --live）

用法：
    python scripts/record_demo_video.py http://127.0.0.1:8000 --output docs/demo.mp4
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


VIEWPORT = {"width": 1280, "height": 832}
FPS = 30
SHOT_EVERY_S = 0.5


def now():
    return time.time()


def shoot(page, frame_dir: Path, idx: int):
    page.screenshot(path=str(frame_dir / f"frame_{idx:04d}.png"), full_page=False)
    return idx + 1


def wait_and_shoot(page, frame_dir: Path, idx: int, duration: float):
    """停留 duration 秒，每 SHOT_EVERY_S 秒截图一次。"""
    end = now() + duration
    idx = shoot(page, frame_dir, idx)
    while now() + SHOT_EVERY_S < end:
        time.sleep(SHOT_EVERY_S)
        idx = shoot(page, frame_dir, idx)
    remaining = end - now()
    if remaining > 0:
        time.sleep(remaining)
    return shoot(page, frame_dir, idx)


def record(url: str, output: Path):
    frame_dir = Path(tempfile.mkdtemp(prefix="edu_eval_demo_frames_"))
    print(f"帧目录: {frame_dir}")
    idx = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(url, wait_until="networkidle")
        time.sleep(0.5)

        # 镜 1：欢迎页（0-12s）
        print("[1/6] 欢迎页 12s")
        page.evaluate("window.scrollTo(0,0)")
        idx = wait_and_shoot(page, frame_dir, idx, 12.0)

        # 镜 2：打开评测说明（12-22s）
        print("[2/6] 评测说明 10s")
        page.click("#headHelpBtn")
        time.sleep(0.4)
        idx = wait_and_shoot(page, frame_dir, idx, 10.0)
        # 关闭说明（按 Escape 或点关闭按钮，backdrop 可能被面板内容拦截）
        page.press("body", "Escape")
        time.sleep(0.4)

        # 镜 3：好样本（22-60s）
        print("[3/6] 好样本 38s")
        page.click(".demo-card:nth-of-type(1)")
        time.sleep(0.4)
        idx = wait_and_shoot(page, frame_dir, idx, 8.0)        # 总览
        page.click(".rtab[data-p='dims']")
        time.sleep(0.3)
        idx = wait_and_shoot(page, frame_dir, idx, 7.0)        # 维度详情
        page.evaluate("document.querySelector('.messages').scrollBy(0, 320)")
        idx = wait_and_shoot(page, frame_dir, idx, 5.0)        # 滚动看维度
        page.click(".rtab[data-p='consistency']")
        time.sleep(0.3)
        idx = wait_and_shoot(page, frame_dir, idx, 6.0)        # 一致性
        page.click(".rtab[data-p='overview']")
        time.sleep(0.3)
        idx = wait_and_shoot(page, frame_dir, idx, 8.0)        # 回到总览
        page.click("#newChatBtn")
        time.sleep(0.3)

        # 镜 4：硬伤 FAIL（60-78s）
        print("[4/6] 硬伤 FAIL 18s")
        page.click(".demo-card:nth-of-type(2)")
        time.sleep(0.4)
        idx = wait_and_shoot(page, frame_dir, idx, 18.0)
        page.click("#newChatBtn")
        time.sleep(0.3)

        # 镜 5：伪启发（78-96s）
        print("[5/6] 伪启发 18s")
        page.click(".demo-card:nth-of-type(3)")
        time.sleep(0.4)
        idx = wait_and_shoot(page, frame_dir, idx, 5.0)        # 总览（含红线通知）
        page.click(".rtab[data-p='dims']")
        time.sleep(0.3)
        idx = wait_and_shoot(page, frame_dir, idx, 6.0)        # 维度详情
        page.evaluate("document.querySelector('.messages').scrollBy(0, 360)")
        idx = wait_and_shoot(page, frame_dir, idx, 6.0)        # 滚动到维度 9 区域

        # 镜 6：停 03 报告（96-110s）
        print("[6/6] 停在 03 报告 14s")
        page.click(".rtab[data-p='overview']")
        time.sleep(0.3)
        idx = wait_and_shoot(page, frame_dir, idx, 14.0)

        browser.close()

    # 合成
    output.parent.mkdir(parents=True, exist_ok=True)
    output_abs = str(output.resolve())
    concat_txt = str(frame_dir / "frames.txt")
    n_frames = idx
    with open(concat_txt, "w") as f:
        for i in range(n_frames - 1):
            f.write(f"file 'frame_{i:04d}.png'\n")
            f.write(f"duration {SHOT_EVERY_S}\n")
        # 最后一帧：用固定的停留时长补足到总时长
        f.write(f"file 'frame_{n_frames - 1:04d}.png'\n")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg 未找到", file=sys.stderr)
        sys.exit(1)

    total_s = (n_frames - 1) * SHOT_EVERY_S
    last_duration = 110.0 - total_s
    if last_duration < 0:
        last_duration = SHOT_EVERY_S

    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", concat_txt,
        "-vf", "fps=30,scale=1280:-1:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-crf", "24", "-preset", "slow", "-pix_fmt", "yuv420p", "-an",
        "-t", str(total_s + last_duration),
        output_abs,
    ]
    print("ffmpeg 命令:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"视频已生成: {output_abs}")

    # 清理
    shutil.rmtree(frame_dir)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="docs/demo.mp4")
    args = parser.parse_args()
    record(args.url, Path(args.output))


if __name__ == "__main__":
    main()
