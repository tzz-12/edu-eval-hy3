"""评测源文件端点：把「被评测的那份课件」原样取出来给前端侧边预览。

    GET /api/source?demo_id=<id>    → 读 data/samples/demo/<id>.md
    GET /api/source?report_id=<id>  → 读历史报告入库时存下的源文本

为什么不把正文塞进报告 payload：
历史列表每次都要读 payload，正文可达数百 KB，挂上去等于每次列表请求都拖着
正文跑。源文件是「按需查看」的东西，所以单独一列 + 单独端点。

安全：demo id 来自 URL，必须防路径穿越。这里做两道——
① 字符串层只允许纯文件名（无分隔符、无 `..`、无 NUL）；
② 拼出路径后校验 `resolve()` 之后父目录**仍等于**样本目录。
只做①容易被编码/软链接绕过，只做②会让错误信息变成 500，所以两道都要。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from ..dirs import demo_samples_dir
from .. import storage
from ..storage import get_report, get_report_source

router = APIRouter()

#: 允许出现在 demo id 里的字符：中文、字母、数字、下划线、连字符、点（用于 `1.2` 这类）。
#: 白名单比黑名单可靠——黑名单永远漏一种编码。
_DEMO_ID_RE = re.compile(r"^[\w.\-\u4e00-\u9fff]+$")


def _resolve_demo_source(demo_id: str) -> Path:
    """把 demo id 解析成样本目录下的真实文件路径；非法一律 400。"""
    did = (demo_id or "").strip()
    if not did or not _DEMO_ID_RE.match(did) or ".." in did:
        raise HTTPException(400, "非法 demo id")
    root = demo_samples_dir().resolve()
    path = (root / f"{did}.md").resolve()
    if path.parent != root:  # resolve 之后再校验一次
        raise HTTPException(400, "非法 demo id")
    return path


@router.get("/api/source")
def source(demo_id: Optional[str] = Query(None, description="演示样本 id"),
           report_id: Optional[int] = Query(None, description="历史报告 id")) -> Dict[str, Any]:
    """取一份源文件正文。两个参数必须二选一。"""
    if (demo_id is None) == (report_id is None):
        raise HTTPException(400, "必须且只能指定 demo_id 或 report_id 之一")

    if demo_id is not None:
        path = _resolve_demo_source(demo_id)
        if not path.exists() or not path.is_file():
            raise HTTPException(404, f"演示样本源文件不存在：{demo_id}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="utf-8", errors="replace")
        kind = "demo"
    else:
        assert report_id is not None  # for type checker
        row = get_report(int(report_id))
        if row is None:
            raise HTTPException(404, "历史报告不存在")
        stored = get_report_source(int(report_id))
        if stored is None:
            # 报告存在但没存正文（旧版本写的）——降级而不是 500。
            return {
                "source_kind": "report", "content": "", "length": 0,
                "reason": "该报告没有源文本（可能生成于源文件功能上线之前）",
            }
        content, kind = stored[0], stored[1]

    truncated = len(content) > storage.MAX_SOURCE_CHARS
    if truncated:
        content = content[: storage.MAX_SOURCE_CHARS]
    return {
        "source_kind": kind,
        "content": content,
        "length": len(content),
        "truncated": truncated,
        "reason": "已截断显示" if truncated else "",
    }