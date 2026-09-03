#!/usr/bin/env python3
"""从国家中小学智慧教育平台免鉴权抓取 13 课题的国家课课件文本（superboard）。

数据链路（全部免登录静态接口）：
  1. 教材资源列表 s-file-2.../zxx/ndrs/national_lesson/teachingmaterials/{教材ID}/resources/parts.json
  2. 资源包详情  s-file-2.../zxx/ndrv2/national_lesson/resources/details/{activityId}.json
  3. superboard 课件 ZIP（r1-ndr-private 免鉴权可 GET）→ 逐页 JSON，文本在 insert 字段（富文本 delta）

用法：python scripts/fetch_smartedu_courseware.py
输出：data/samples/smartedu_md/{课题}_{课时}.md
"""

import io
import json
import re
import time
import urllib.request
import zipfile
from pathlib import Path

UA = {'User-Agent': 'Mozilla/5.0'}
S_FILE = 'https://s-file-2.ykt.cbern.com.cn'

# 2022 版人教版初中数学教材 ID
BOOKS = {
    '七年级上册': '921f145d-f79f-4ef4-91ee-4565622c95c6',
    '七年级下册': '8375f8b8-926c-4dba-a219-1b8714526458',
    '八年级上册': 'd92ca54e-2cdc-4921-95f3-769eafd0c814',
    '八年级下册': 'fa60dc59-2eea-444d-80fb-f008d6b8d572',
    '九年级上册': 'f83d3172-35fa-4912-8787-84893e64f58e',
    '九年级下册': 'fc56445a-c517-4dfb-8cba-7b299fa032b7',
}

# 13 课题 → 代表课时（资源包标题精确匹配）
LESSONS = [
    ('有理数运算', '七年级上册', '有理数的加法'),
    ('整式的加减', '七年级上册', '整式的加减'),
    ('一元一次方程', '七年级上册', '一元一次方程'),
    ('二元一次方程组', '七年级下册', '二元一次方程组'),
    ('二元一次方程组', '七年级下册', '消元—解二元一次方程组'),
    ('全等三角形', '八年级上册', '全等三角形'),
    ('全等三角形', '八年级上册', '三角形全等的判定-SSS'),
    ('整式的乘法与因式分解', '八年级上册', '整式的乘法'),
    ('整式的乘法与因式分解', '八年级上册', '因式分解—提公因式法'),
    ('分式', '八年级上册', '从分数到分式'),
    ('一次函数', '八年级下册', '一次函数的图象和性质'),
    ('勾股定理', '八年级下册', '勾股定理'),
    ('一元二次方程', '九年级上册', '21.1一元二次方程'),
    ('一元二次方程', '九年级上册', '21.2.1配方法'),
    ('二次函数', '九年级上册', '22.1.1二次函数'),
    ('相似三角形', '九年级下册', '相似三角形的判定'),
    ('锐角三角函数', '九年级下册', '锐角的正弦'),
]

INSERT_RE = re.compile(r'"insert":"((?:[^"\\]|\\.)*)"')


def _get(url: str, timeout: int, tries: int = 4):
    """带指数退避的 GET（平台对高频请求回 403）。"""
    import urllib.error
    for i in range(tries):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and i < tries - 1:
                wait = 3 * (2 ** i)
                print(f'  … {e.code}，{wait}s 后重试')
                time.sleep(wait)
                continue
            raise
    raise RuntimeError('unreachable')


def get_json(url: str):
    return json.loads(_get(url, 30))


def get_bytes(url: str) -> bytes:
    return _get(url, 60)


def fetch_resources(tmid: str) -> dict:
    parts = get_json(f'{S_FILE}/zxx/ndrs/national_lesson/teachingmaterials/{tmid}/resources/parts.json')
    resources = []
    for u in parts:
        resources.extend(get_json(u))
    return {r.get('title'): r for r in resources}


def find_superboard_url(activity_id: str):
    d = get_json(f'{S_FILE}/zxx/ndrv2/national_lesson/resources/details/{activity_id}.json')
    teacher = ''
    for t in d.get('teacher_list') or []:
        teacher = teacher + t.get('name', '') if isinstance(t, dict) else str(t)
    for rel in d.get('relations', {}).get('national_course_resource', []):
        if rel.get('resource_type_code_name') != '课件':
            continue
        for it in rel.get('ti_items', []):
            if it.get('ti_file_flag') == 'superboard':
                storages = it.get('ti_storages') or []
                if storages:
                    return storages[0], teacher
    return None, teacher


def page_texts(zip_data: bytes) -> list[str]:
    zf = zipfile.ZipFile(io.BytesIO(zip_data))
    pages = []
    # 按文件名中的页序 id 排序（与 zip 内顺序一致即可）
    for name in zf.namelist():
        if not name.endswith('.json'):
            continue
        raw = zf.read(name).decode('utf-8', 'replace')
        inserts = INSERT_RE.findall(raw)
        texts = []
        for t in inserts:
            t = t.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
            t = t.strip()
            if t:
                texts.append(t)
        if texts:
            pages.append('\n'.join(texts))
    return pages


def main():
    out_dir = Path(__file__).resolve().parent.parent / 'data' / 'samples' / 'smartedu_md'
    out_dir.mkdir(parents=True, exist_ok=True)

    cache = {}
    ok, fail = 0, 0
    for topic, book, lesson_title in LESSONS:
        fname = f'{topic}_{lesson_title}.md'.replace('/', '_')
        if (out_dir / fname).exists():
            print(f'↷ 跳过已存在：{fname}')
            continue
        try:
            if book not in cache:
                cache[book] = fetch_resources(BOOKS[book])
            res = cache[book].get(lesson_title)
            if not res:
                print(f'✗ [{book}] 未找到课时「{lesson_title}」')
                fail += 1
                continue
            activity_id = res['id']
            url, teacher = find_superboard_url(activity_id)
            if not url:
                print(f'✗ [{lesson_title}] 无 superboard 课件')
                fail += 1
                continue
            data = get_bytes(url)
            pages = page_texts(data)
            if not pages:
                print(f'✗ [{lesson_title}] 课件无文本')
                fail += 1
                continue

            md = [
                f'# {lesson_title}',
                '',
                f'- 课题：{topic}',
                f'- 教材：人教版{book}（2022 版）',
                f'- 授课教师：{teacher or "（平台未标注）"}',
                '- 来源：国家中小学智慧教育平台「课程教学」国家课（basic.smartedu.cn）',
                '- 形态：课件（superboard 白板逐页文本提取，非完整教案）',
                '',
                '---',
                '',
            ]
            for i, p in enumerate(pages, 1):
                md.append(f'## 第 {i} 页')
                md.append('')
                md.append(p)
                md.append('')
            fname = f'{topic}_{lesson_title}.md'.replace('/', '_')
            (out_dir / fname).write_text('\n'.join(md), encoding='utf-8')
            total_chars = sum(len(p) for p in pages)
            print(f'✓ {fname}（{len(pages)} 页 / {total_chars} 字）')
            ok += 1
            time.sleep(2)
        except Exception as e:
            print(f'✗ [{lesson_title}] {e}')
            fail += 1

    print(f'\n完成：成功 {ok} / 失败 {fail}，输出目录 {out_dir}')


if __name__ == '__main__':
    main()
