#!/usr/bin/env python3
"""生成 EduEval 演示视频（docs/demo.mp4 + docs/demo.gif）。

按 `docs/demo_video.md` 的分镜脚本操作浏览器，使用 Playwright 原生视频录制
（逐帧捕获页面渲染，含鼠标移动轨迹），再经 ffmpeg 转码为 H.264 MP4 并导出 GIF。
全程无需人工操作，可在演示报告更新后重复执行。

前置条件：
    pip install playwright
    playwright install chromium
    demo 服务已在本机启动（bash scripts/run_demo.sh start 8000 --live）
    本机安装 ffmpeg

用法：
    python scripts/record_demo_video.py http://127.0.0.1:8000 --output docs/demo.mp4
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


VIEWPORT = {"width": 1280, "height": 832}


def dwell(page, seconds: float, *, scroll: int = 0):
    """在当前位置停留 seconds 秒；scroll>0 时缓慢竖向滚动，模拟真实浏览。"""
    if scroll:
        steps = max(6, int(seconds * 4))
        per = scroll / steps
        for _ in range(steps):
            page.mouse.wheel(0, per)
            time.sleep(seconds / steps)
    else:
        end = time.time() + seconds
        while time.time() < end:
            time.sleep(0.25)


def move_to(page, selector: str, *, steps: int = 18):
    """把鼠标平滑移到目标元素中心（默认 18 步，比瞬移更接近真实操作）。"""
    box = page.locator(selector).bounding_box()
    if not box:
        return
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx, cy, steps=steps)


def slow_click(page, selector: str):
    move_to(page, selector)
    page.click(selector, timeout=8000)
    time.sleep(0.35)


def run_storyboard(page):
    """六镜分镜表（docs/demo_video.md §2），总时长约 100 秒。"""

    # 镜 1：欢迎页（0:00–0:12）
    print("[1/6] 欢迎页")
    move_to(page, ".welcome-inner h2")
    dwell(page, 3.0)
    move_to(page, ".feat:nth-of-type(1)", steps=12)
    dwell(page, 1.5)
    move_to(page, ".feat:nth-of-type(2)", steps=12)
    dwell(page, 1.5)
    page.mouse.wheel(0, 260)
    dwell(page, 3.0)
    page.mouse.wheel(0, -260)
    dwell(page, 2.5)

    # 镜 2：评测说明（0:12–0:22）
    print("[2/6] 评测说明")
    slow_click(page, "#headHelpBtn")
    dwell(page, 6.0, scroll=300)
    dwell(page, 2.0)
    page.keyboard.press("Escape")
    time.sleep(0.5)

    # 镜 3：好样本（0:22–1:00）
    print("[3/6] 好样本 01")
    slow_click(page, ".demo-card:nth-of-type(1)")
    dwell(page, 6.0)                              # 总览：判定 + 总分 + 雷达
    slow_click(page, ".rtab[data-p='dims']")
    dwell(page, 4.0)
    dwell(page, 5.0, scroll=380)                  # 维度详情滚动
    slow_click(page, ".rtab[data-p='consistency']")
    dwell(page, 7.0)                              # 一致性面板
    slow_click(page, ".rtab[data-p='overview']")
    dwell(page, 4.0)
    slow_click(page, "#newChatBtn")

    # 镜 4：硬伤 FAIL（1:00–1:18）
    print("[4/6] 硬伤 02")
    slow_click(page, ".demo-card:nth-of-type(2)")
    dwell(page, 8.0)
    dwell(page, 5.0, scroll=160)
    slow_click(page, "#newChatBtn")

    # 镜 5：伪启发（1:18–1:36）
    print("[5/6] 伪启发 03")
    slow_click(page, ".demo-card:nth-of-type(3)")
    dwell(page, 5.0)                              # 总览：红线通知
    slow_click(page, ".rtab[data-p='dims']")
    dwell(page, 4.0)
    dwell(page, 5.0, scroll=420)
    slow_click(page, ".rtab[data-p='overview']")
    dwell(page, 3.0)

    # 镜 6：收尾（1:36–1:44）
    print("[6/6] 收尾")
    dwell(page, 6.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="docs/demo.mp4")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("需要 ffmpeg 用于转码", file=sys.stderr)
        sys.exit(1)

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_webm = output.with_suffix(".raw.webm")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(output.parent),
            record_video_size=VIEWPORT,
        )
        page = ctx.new_page()
        page.goto(args.url, wait_until="networkidle")
        time.sleep(0.6)

        run_storyboard(page)

        video = page.video
        path = video.path()  # webm 原始录制
        ctx.close()
        browser.close()

    # webm → H.264 MP4
    subprocess.run(
        [ffmpeg, "-y", "-i", path,
         "-vf", "fps=30,format=yuv420p",
         "-c:v", "libx264", "-crf", "23", "-preset", "medium",
         "-movflags", "+faststart", "-an", str(output)],
        check=True,
    )
    Path(path).unlink(missing_ok=True)
    raw_webm.unlink(missing_ok=True)

    # GIF（README 内嵌自动播放用，800 宽 / 10fps 控制体积）
    gif = output.with_suffix(".gif")
    subprocess.run(
        [ffmpeg, "-y", "-i", str(output),
         "-vf", "fps=10,scale=800:-1:flags=lanczos,split[s0][s1];"
                "[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4",
         str(gif)],
        check=True,
    )

    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(output)],
        capture_output=True, text=True).stdout.strip()
    print(f"完成: {output}（{float(dur):.0f}s）、{gif}（{gif.stat().st_size / 1e6:.1f}MB）")


if __name__ == "__main__":
    main()
