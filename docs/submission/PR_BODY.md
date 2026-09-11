# 向 Tencent-Hunyuan/Hy3 的 rhinobird2026 分支提交 PR —— 状态与操作

> 对应 issue：[#4 Build a vibe-coded application powered by Hy3](https://github.com/Tencent-Hunyuan/Hy3/issues/4)
> 当前状态：**fork 与分支已备好，PR 尚未打开**（等作者过目标题与正文）

---

## ✅ ① fork —— 已完成（2026-09-11）

```bash
gh repo fork Tencent-Hunyuan/Hy3 --clone=false
# → https://github.com/tzz-12/Hy3 （isFork = true）
```

## ✅ ② 分支与提交材料 —— 已完成

fork 上已建好分支 `submissions/edueval-hy3`（基于上游 `rhinobird2026` 的 `8a12d9a`），
并加了一个提交 `58566fe`：

```
submissions/edueval-hy3/README.md      新增 1 个文件（3312 B），无其他改动
```

照同类提交的惯例（参考上游 PR #223 / #222 / #219：独立仓库只加一个
`submissions/<项目名>/README.md` 作指针，内容 = 项目说明 + 独立仓库链接）。

**差异预览**（这不是 PR，只是看改了什么）：

```
https://github.com/Tencent-Hunyuan/Hy3/compare/rhinobird2026...tzz-12:submissions/edueval-hy3
```

## ⏳ ③ 提 PR —— 待执行

⚠️ **base 必须是 `rhinobird2026`，不是 `main`。**

```bash
gh pr create --repo Tencent-Hunyuan/Hy3 \
  --base rhinobird2026 \
  --head tzz-12:submissions/edueval-hy3 \
  --title "【犀牛鸟实战】EduEval：基于 Hy3 的初中数学教学设计质量评估器（独立仓库）" \
  --body-file /Users/tzz/WorkBuddy/edu-eval-hy3/docs/submission/PR_MESSAGE.md
```

### 撤销方法（都不影响上游仓库）

```bash
gh api -X DELETE /repos/tzz-12/Hy3/git/refs/heads/submissions/edueval-hy3   # 只删分支
gh repo delete tzz-12/Hy3                                                   # 删掉整个 fork
```

---

## PR 标题

```
【犀牛鸟实战】EduEval：基于 Hy3 的初中数学教学设计质量评估器（独立仓库）
```

## PR 正文

**唯一事实来源：`docs/submission/PR_MESSAGE.md`**（`--body-file` 直接指向它）。
下方不再复制一份，避免两份正文漂移。

正文包含五节：项目简介 / Hy3 在系统中承担的角色 / 对照 issue 要求 / 项目亮点 / 说明。
