/* EduEval Demo · 对话式前端
 *
 * 结构：左栏历史会话 + 右栏对话流 + 底部输入区。
 * 每条助手消息是一张「评测报告卡片」，内嵌 5 类图表：
 *   ① 总分半环仪表盘  ② 九维雷达  ③ 维度得分横向条形
 *   ④ 双采样一致性环形 ⑤ 规则层发现分布 + 规则统计条形
 * 图表按 Tab 懒加载：只有切到该面板时才初始化，避免在 display:none
 * 容器里取到 0 宽度而画不出来。
 */

const API = "";  // 同源部署

// ============== 工具 ==============
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const escHtml = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

const fmtTime = (ts) => {
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return sameDay ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
};

async function api(method, path, body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  if (!r.ok) {
    let detail = `${r.status}`;
    try { const j = await r.json(); detail = j.detail || detail; } catch {}
    throw new Error(detail);
  }
  return r.json();
}

// 图表配色：Chart.js 不读 CSS 变量，只能在这边维护一份，改主题时记得同步
const C = {
  accent: "#3D4A8C",
  accentSoft: "rgba(61,74,140,0.14)",
  good: "#2F6B4F", goodSoft: "rgba(47,107,79,0.16)",
  warn: "#9A6B1E", warnSoft: "rgba(154,107,30,0.16)",
  bad:  "#B23A2E", badSoft:  "rgba(178,58,46,0.16)",
  ne:   "#8A8275", neSoft:   "rgba(138,130,117,0.18)",
  grid: "rgba(35,32,27,0.07)",
  label: "#6B6357",
  faint: "#9C9486",
  tooltipBg: "#23201B",
};

if (window.Chart) {
  Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif';
  Chart.defaults.font.size = 11;
  Chart.defaults.color = C.label;
}

const scoreColor = (s) => (s >= 4 ? C.good : s === 3 ? C.warn : C.bad);
const scoreColorSoft = (s) => (s >= 4 ? C.goodSoft : s === 3 ? C.warnSoft : C.badSoft);

const DIM_NAMES = {
  "1": "教学目标明确性与课标对齐", "2": "知识绝对正确性（G0）", "3": "学段与认知层次适配",
  "4": "教学环节设计合理性", "5": "表述清晰度与易懂性", "6": "安全合规与价值导向（红线）",
  "7": "学情分析", "8": "教-学-评一致性", "9": "学习者中心与启发探究", "A": "格式与基本可读性",
};
const DIM_SHORT = {
  "1": "教学目标", "2": "知识准确", "3": "学段适配", "4": "环节设计", "5": "表述清晰",
  "6": "安全合规", "7": "学情分析", "8": "教-学-评", "9": "启发引导", "A": "格式可读",
};
const DIM_PRI = {
  "2": "G0", "6": "P0 红线", "1": "P1", "3": "P1", "8": "P1",
  "4": "P2", "7": "P2", "9": "P2", "5": "P3", "A": "AUX",
};
const DIM_ORDER = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "A"];

// 演示样本的补充说明（id → 一句话说明）
const DEMO_DESC = {
  "01_good_二次函数": "结构完整的合格教案，九个维度均衡",
  "02_bad_formula": "故意植入 3 处公式错误，规则层零 LLM 拦截",
  "03_bad_fake_socratic": "只有提问外壳、没有认知引导的伪启发",
};

// ============== 状态 ==============
let uid = 0;
const charts = new Map();          // key → Chart 实例，重渲染时统一销毁
let currentConvId = null;          // null = 新会话
let healthInfo = null;
// welcome 节点必须持有引用：清空消息流用 innerHTML="" 会把它从 DOM 摘掉，
// 之后再 querySelector 就找不回来了（新建评测时需要把它挂回去）
let welcomeEl = null;

const nextId = () => `c${++uid}`;

function destroyCharts(prefix) {
  for (const [k, inst] of charts) {
    if (k.startsWith(prefix)) { try { inst.destroy(); } catch {} charts.delete(k); }
  }
}

// ============== 启动 ==============
(async function init() {
  welcomeEl = $("#welcome");
  try {
    healthInfo = await api("GET", "/api/health");
    const ok = healthInfo.api_key_configured;
    // 状态行固定「一个圆点 + 一句状态」，异常信息并入同一个点：
    // 原来是 db_note 时再追加一个 warn 点，网格布局下会被挤到第二行去。
    let dotCls = ok ? "dot-green" : "dot-red";
    let label = ok ? "API 已配置" : "API 未配置";
    if (healthInfo.demo_mode) {
      // 演示模式下没配 key 是预期状态，显示成「API 未配置」会被误读成系统坏了
      dotCls = "dot-demo"; label = "演示模式";
    }
    if (!healthInfo.db_writable) {
      dotCls = "dot-warn"; label = "历史库只读";
    }
    const dbTip = healthInfo.db_note || (healthInfo.db_writable ? "" : "历史库不可写");
    // 裁判模型名常驻侧栏：判别力/稳定性数据不可跨模型比较，换没换裁判要一眼可见
    const modelBit = healthInfo.model
      ? `<span class="status-model" title="${escHtml(healthInfo.model)}">${escHtml(healthInfo.model)}</span>`
      : "";
    $("#healthBadge").innerHTML =
      `<span class="dot ${dotCls}"${dbTip ? ` title="${escHtml(dbTip)}"` : ""}></span>` +
      `<span>${label}</span>` + modelBit;
    $("#headModel").textContent = healthInfo.model || "";
  } catch {
    $("#healthBadge").innerHTML = `<span class="dot dot-red"></span><span>后端未连接</span>`;
  }

  try {
    const g = await api("GET", "/api/grades");
    $("#gradeSelect").innerHTML = g.grades.map(x => `<option>${x}</option>`).join("");
    // 默认落在初中最高学段，演示样本都是九年级
    if (g.grades.includes("九年级")) $("#gradeSelect").value = "九年级";
  } catch {}

  await Promise.all([loadDemoSamples(), loadConversations()]);
  bindEvents();
})();

async function loadDemoSamples() {
  try {
    const d = await api("GET", "/api/demo-samples");
    const box = $("#demoButtons");
    if (!d.samples.length) {
      box.innerHTML = `<span class="hint">尚无演示样本，先运行 scripts/pregen_demo_reports.py</span>`;
      return;
    }
    box.innerHTML = d.samples.map(s => `
      <button class="demo-card" data-demo="${escHtml(s.id)}">
        <div class="demo-card-top">
          <span class="demo-card-name">${escHtml(s.label)}</span>
          <span class="badge-mini badge-${(s.admission || "NE").toLowerCase()}">${escHtml(s.admission || "—")}${s.total_score != null ? " " + Math.round(s.total_score) : ""}</span>
        </div>
        <div class="demo-card-desc">${escHtml(DEMO_DESC[s.id] || "点击秒级加载，不消耗额度")}</div>
      </button>
    `).join("");
    $$("#demoButtons .demo-card").forEach(b =>
      b.addEventListener("click", () => runDemo(b.dataset.demo)));
  } catch {
    $("#demoButtons").innerHTML = `<span class="hint">演示样本加载失败</span>`;
  }
}

// ============== 事件绑定 ==============
function bindEvents() {
  $("#sendBtn").addEventListener("click", submitEvaluate);
  $("#attachBtn").addEventListener("click", () => $("#fileInput").click());
  $("#fileInput").addEventListener("change", onFilePicked);
  $("#textInput").addEventListener("input", () => { updateCount(); autoGrow(); });
  $("#textInput").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); submitEvaluate(); }
  });

  $("#newChatBtn").addEventListener("click", newChat);
  $("#menuBtn").addEventListener("click", () => {
    $("#sidebar").classList.toggle("open");
    $("#scrim").classList.toggle("show", $("#sidebar").classList.contains("open"));
  });
  $("#scrim").addEventListener("click", () => {
    $("#sidebar").classList.remove("open");
    $("#scrim").classList.remove("show");
  });
}

let currentFileName = "";
function updateCount() {
  const n = $("#textInput").value.trim().length;
  $("#charCount").textContent = n ? `${n.toLocaleString()} 字` : "0 字";
}
function autoGrow() {
  const t = $("#textInput");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 200) + "px";
}
async function onFilePicked(e) {
  const f = e.target.files[0];
  if (!f) return;
  currentFileName = f.name;
  if (f.name.endsWith(".pdf")) {
    $("#textInput").value = `(PDF 上传由后端处理：${f.name})\n\n请等待评测时后端读 PDF…`;
  } else {
    $("#textInput").value = await f.text();
  }
  updateCount(); autoGrow();
  $("#textInput").focus();
}

// ============== 会话 ==============
function clearMessages() {
  destroyCharts("");
  if (!welcomeEl) welcomeEl = $("#welcome");
  $("#messages").innerHTML = "";
}

function newChat() {
  currentConvId = null;
  currentFileName = "";
  clearMessages();
  $("#messages").appendChild(welcomeEl);
  welcomeEl.classList.remove("hidden");
  $("#chatTitle").textContent = "新评测";
  $("#chatSub").textContent = "";
  $$(".conv-item").forEach(i => i.classList.remove("active"));
  $("#sidebar").classList.remove("open");
  $("#scrim").classList.remove("show");
  $("#textInput").value = "";
  updateCount(); autoGrow();
}

async function loadConversations() {
  const box = $("#convList");
  try {
    const list = await api("GET", "/api/reports");
    if (!list.length) {
      box.innerHTML = `
        <div class="conv-empty">
          <span class="conv-empty-icon">◎</span>
          <b>还没有评测记录</b>
          <span>粘贴一份课件、或从演示样本开始，<br>结果会留在这里</span>
        </div>`;
      return;
    }
    box.innerHTML = list.map(r => {
      const adm = (r.admission || "NE").toLowerCase();
      const score = r.total_score != null ? Math.round(r.total_score) + " 分" : "未出分";
      return `
      <button class="conv-item is-${adm}" data-id="${r.id}">
        <div class="conv-top">
          <span class="conv-name">${escHtml(r.file_name || "未命名")}</span>
          <span class="badge-mini badge-${adm}">${escHtml(r.admission || "—")}</span>
        </div>
        <div class="conv-meta">
          <span>${fmtTime(r.created_at)}</span>
          <span>${escHtml(r.grade || "")}</span>
          <span>${score}</span>
        </div>
      </button>`;
    }).join("");
    $$("#convList .conv-item").forEach(b =>
      b.addEventListener("click", () => openConversation(b.dataset.id)));
  } catch (e) {
    box.innerHTML = `
      <div class="conv-empty">
        <span class="conv-empty-icon">!</span>
        <b>历史加载失败</b>
        <span>${escHtml(e.message)}</span>
      </div>`;
  }
}

async function openConversation(id) {
  try {
    const full = await api("GET", `/api/reports/${id}`);
    currentConvId = id;
    $$(".conv-item").forEach(i => i.classList.toggle("active", i.dataset.id === String(id)));
    $("#sidebar").classList.remove("open");
    $("#scrim").classList.remove("show");

    clearMessages();

    addUserMessage({
      title: full.file_name || "历史记录",
      grade: full.grade,
      text: "",
      meta: `${fmtTime(full.created_at)} · 双采样 ${full.dual_sample ? "开" : "关"}`,
    });
    addReportMessage(full, { animate: false });
    $("#chatTitle").textContent = full.file_name || "历史记录";
    $("#chatSub").textContent = `${full.grade || ""} · ${fmtTime(full.created_at)}`;
  } catch (e) {
    alert("打开历史失败：" + e.message);
  }
}

// ============== 消息渲染 ==============
function hideWelcome() {
  if (welcomeEl) welcomeEl.classList.add("hidden");
}

function addUserMessage({ title, grade, text, meta }) {
  hideWelcome();
  const el = document.createElement("div");
  el.className = "msg msg-user";
  const preview = text ? escHtml(text.slice(0, 600)) + (text.length > 600 ? "\n…" : "") : "";
  el.innerHTML = `
    <div class="bubble">
      <div class="bubble-title">
        <strong>${escHtml(title)}</strong>
        ${grade ? `<span class="tag">${escHtml(grade)}</span>` : ""}
        ${meta ? `<span class="tag">${escHtml(meta)}</span>` : ""}
      </div>
      ${preview ? `<div class="bubble-body${text.length > 600 ? " fold" : ""}">${preview}</div>` : ""}
    </div>`;
  $("#messages").appendChild(el);
  scrollToBottom();
  return el;
}

function addThinking(text) {
  hideWelcome();
  const el = document.createElement("div");
  el.className = "msg msg-bot";
  el.innerHTML = `
    <div class="avatar">EE</div>
    <div class="bubble-wrap">
      <div class="thinking">
        <span class="typing-dots"><i></i><i></i><i></i></span>
        <span class="thinking-text">${escHtml(text)}</span>
        <span class="thinking-timer">0s</span>
      </div>
    </div>`;
  $("#messages").appendChild(el);
  scrollToBottom();

  // 真实模型一次评测 30~90s，没有任何反馈的等待很容易让人以为卡死了。
  // 只挂一个秒表（后端没有流式进度可依，不编造阶段），元素被移除后自停。
  const timerEl = el.querySelector(".thinking-timer");
  const t0 = Date.now();
  const tick = setInterval(() => {
    if (!document.body.contains(el)) { clearInterval(tick); return; }
    timerEl.textContent = `${Math.round((Date.now() - t0) / 1000)}s`;
  }, 500);

  return el;
}

function addReportMessage(report, { animate = true } = {}) {
  hideWelcome();
  const el = document.createElement("div");
  el.className = "msg msg-bot";
  const cid = nextId();
  el.innerHTML = `
    <div class="avatar">EE</div>
    <div class="bubble-wrap">
      ${reportCardHTML(report, cid)}
    </div>`;
  $("#messages").appendChild(el);
  if (animate) el.style.animation = "fade .22s ease";
  bindReportCard(el, report, cid);
  // 报告卡常高于可视区（约 585px vs 435px），若直接滚到底部，顶部「总分 / 判定」
  // 会被推出视口，用户第一眼看到的是中段。这里改为对齐卡片顶部。
  requestAnimationFrame(() => {
    scrollToEl(el, 6);
    // 图表（环形/雷达/柱状）是异步绘制的，会改变卡片高度，再校正一次
    setTimeout(() => scrollToEl(el, 6), 340);
  });
  return el;
}

function scrollToBottom() {
  const m = $("#messages");
  m.scrollTop = m.scrollHeight;
}

// 把某个元素滚到消息区可视范围顶部（用 rect 差值算，不依赖 offsetParent）
function scrollToEl(el, pad = 8) {
  const m = $("#messages");
  if (!m || !el) return;
  const delta = el.getBoundingClientRect().top - m.getBoundingClientRect().top;
  m.scrollTop = Math.max(0, m.scrollTop + delta - pad);
}

// ============== 评测提交 ==============
async function submitEvaluate() {
  const text = $("#textInput").value.trim();
  if (!text) { flashHint("请先粘贴课件文本或上传文件"); return; }
  const grade = $("#gradeSelect").value;
  if (!grade) { flashHint("请选择年级"); return; }

  const btn = $("#sendBtn");
  btn.disabled = true;

  // 用户消息气泡：标题优先用文件名，否则取正文首个一级标题
  const h1 = text.match(/^#\s+(.+)$/m);
  const title = currentFileName || (h1 ? h1[1] : "粘贴的课件文本");
  addUserMessage({
    title,
    grade,
    text,
    meta: `${text.length.toLocaleString()} 字 · 双采样 ${$("#dualSampleCheck").checked ? "开" : "关"}`,
  });

  const thinking = addThinking("正在评测，真实模型约 30–90 秒，请勿关闭页面…");
  $("#chatTitle").textContent = title;
  $("#chatSub").textContent = `${grade} · 评测中…`;

  try {
    const r = await api("POST", "/api/evaluate", {
      text,
      grade,
      file_name: currentFileName || undefined,
      source: "live",
      dual_sample: $("#dualSampleCheck").checked,
      dual_threshold: parseInt($("#dualThresholdInput").value, 10),
    });
    thinking.remove();
    addReportMessage(r);
    $("#chatSub").textContent = `${grade} · ${fmtTime(Date.now() / 1000)}`;
    await loadConversations();
  } catch (e) {
    thinking.remove();
    addErrorMessage(e.message);
    $("#chatSub").textContent = "评测失败";
  } finally {
    btn.disabled = false;
    $("#textInput").value = "";
    currentFileName = "";
    updateCount(); autoGrow();
  }
}

function addErrorMessage(msg) {
  const el = document.createElement("div");
  el.className = "msg msg-bot";
  el.innerHTML = `
    <div class="avatar" style="background:var(--bad)">!</div>
    <div class="bubble-wrap">
      <div class="report-card">
        <div class="report-body">
          <div class="notice" style="margin:0">评测失败：${escHtml(msg)}</div>
          <div class="hint">常见原因：API 额度用尽、网络不通、模型名错误。可先用演示样本验证链路。</div>
        </div>
      </div>
    </div>`;
  $("#messages").appendChild(el);
  scrollToBottom();
}

function flashHint(msg) {
  const el = $("#composerHint");
  el.textContent = msg;
  setTimeout(() => { el.textContent = ""; }, 2600);
}

async function runDemo(id) {
  const grade = $("#gradeSelect").value || "九年级";
  const label = id.replace(/^\d+_/, "").replace(/_/g, " ");
  addUserMessage({
    title: `演示样本 · ${label}`,
    grade,
    text: "",
    meta: "预生成报告，秒级返回、不消耗额度",
  });
  const thinking = addThinking(`加载演示样本 ${label}…`);
  $("#chatTitle").textContent = `演示 · ${label}`;
  $("#chatSub").textContent = "加载中…";

  try {
    const r = await api("POST", "/api/evaluate", {
      text: "",
      grade,
      source: `demo:${id}`,
      dual_sample: $("#dualSampleCheck").checked,
      dual_threshold: parseInt($("#dualThresholdInput").value, 10),
    });
    thinking.remove();
    addReportMessage(r);
    $("#chatSub").textContent = `${grade} · 演示样本`;
  } catch (e) {
    thinking.remove();
    addErrorMessage(e.message);
  }
}

/* ================================================================
 *                        报告卡片
 * ================================================================ */

function reportCardHTML(r, cid) {
  const agg = r.aggregation || {};
  const adm = r.admission || "NE";
  const total = agg.total_score;
  // 半圆环内的读数配色，与 drawGauge 的取色规则保持一致
  const gv = total != null ? Number(total) : null;
  const gaugeColor = gv == null ? C.ne : gv >= 85 ? C.good : gv >= 70 ? C.accent : gv >= 60 ? C.warn : C.bad;
  const rules = r.rules || {};
  const findings = rules.findings || [];
  const nFail = findings.filter(f => f.verdict === "fail").length;
  const nWarn = findings.filter(f => f.verdict === "warn").length;
  const ds = r.dual_sample || {};
  const scoreCount = Object.keys(r.scores || {}).length;

  const railCls = adm === "FAIL" ? "is-fail" : adm === "PASS" ? "is-pass" : "is-ne";

  // 元信息行：文档名 / 年级 / 解析置信度。字段名在不同来源下不一致
  // （demo 报告用 _demo_source，实时评测用 file_name），都要兜住。
  const docName = r.file_name || r._demo_source || "粘贴的课件文本";
  const gradeName = r.grade || r._demo_grade || "";
  const conf = r.parse && typeof r.parse.parse_confidence === "number"
    ? `解析 ${Math.round(r.parse.parse_confidence * 100)}%` : "";
  const metaBits = [docName, gradeName, conf].filter(Boolean);

  return `
  <div class="report-card ${railCls}" data-cid="${cid}">
    <div class="report-head">
      <div class="report-head-left">
        <div class="report-pill-row">
          <span class="eyebrow">评测报告</span>
          <span class="pill pill-${escHtml(adm)}">${escHtml(adm)}</span>
          ${agg.grade ? `<span class="badge-mini badge-${adm === "FAIL" ? "fail" : "pass"}">${escHtml(agg.grade)}</span>` : ""}
          ${r.redline ? `<span class="badge-mini badge-fail">红线触发</span>` : ""}
        </div>
        <div class="report-score-line">
          <span class="report-score-num">${total != null ? Number(total).toFixed(1) : "—"}</span>
          <span class="report-score-den">/ 100 加权总分</span>
          ${agg.verdict ? `<span class="report-verdict">· ${escHtml(agg.verdict)}</span>` : ""}
        </div>
        <div class="report-sub">${metaBits.map(m => escHtml(m)).join(" · ")}</div>
      </div>
      <div class="gauge">
        <canvas id="gauge-${cid}"></canvas>
        <span class="gauge-num" style="color:${gaugeColor}">${total != null ? Math.round(Number(total)) : "—"}</span>
      </div>
    </div>

    <div class="report-statbar">
      <span><b>${scoreCount}</b> 个维度出分</span>
      <span class="statbar-sep"></span>
      <span>权重覆盖 <b>${((agg.weight_coverage ?? 0) * 100).toFixed(0)}%</b></span>
      <span class="statbar-sep"></span>
      <span>知识库命中 <b>${r.kb_hits ?? 0}</b> 条</span>
      ${(agg.skipped_ne || []).length ? `<span class="statbar-sep"></span><span>未出分 NE <b>${agg.skipped_ne.length}</b></span>` : ""}
      ${ds.dims_total ? `<span class="statbar-sep"></span><span>双采样 <b>${ds.dims_total}</b> 维</span>` : ""}
      ${findings.length ? `<span class="statbar-sep"></span><span>规则层 <b>${findings.length}</b> 项发现</span>` : ""}
    </div>

    <div class="report-tabs">
      <button class="rtab active" data-p="overview" title="结论前置：关键结论与九维雷达">总览</button>
      <button class="rtab" data-p="dims" title="逐维评分、原文证据与判定理由">维度详情 <span class="rtab-badge">${scoreCount}</span></button>
      <button class="rtab" data-p="consistency" title="双采样两次评分的分歧情况">一致性 <span class="rtab-badge">${ds.dims_total || 0}</span></button>
      <button class="rtab" data-p="rules" title="零 LLM 的确定性检查发现">规则层 <span class="rtab-badge">${findings.length}</span></button>
      <button class="rtab" data-p="advice" title="按优先级排列的改进建议">改进建议 <span class="rtab-badge">${(r.suggestions || []).length}</span></button>
    </div>

    <div class="report-body">
      ${panelOverview(r, cid)}
      ${panelDims(r, cid)}
      ${panelConsistency(r, cid)}
      ${panelRules(r, cid)}
      ${panelAdvice(r, cid)}
    </div>
  </div>`;
}

/* ---------- 关键结论：把最重要的判断提前到第一屏 ----------
 * 规则：① G0 闸门 FAIL 最优先 ② 规则层确定性缺陷 ③ 最低分维度 ④ 都没有则给好消息
 * 最多 4 条，避免又变成一堵墙。 */
function keyFindings(r) {
  const items = [];
  const adm = r.admission || "NE";
  const findings = (r.rules && r.rules.findings) || [];

  if (adm === "FAIL") {
    items.push({ level: "bad", mark: "✗",
      text: "未通过知识准入闸门（G0 · 维度2），评分已终止，不给总分" });
  }

  findings.filter(f => f.verdict === "fail").slice(0, 2).forEach(f => {
    const ev = String(f.evidence || f.reason || "").slice(0, 88);
    items.push({ level: "bad", mark: "✗", text: `${f.rule_id || "规则"}：${ev}` });
  });

  const low = DIM_ORDER
    .filter(id => r.scores && r.scores[id] && !r.scores[id].ne)
    .map(id => ({ id, s: Number(r.scores[id].score) || 0 }))
    .filter(x => x.s <= 3)
    .sort((a, b) => a.s - b.s)
    .slice(0, 2);
  low.forEach(({ id, s }) => {
    items.push({
      level: s <= 2 ? "bad" : "warn",
      mark: s <= 2 ? "✗" : "△",
      text: `维度 ${id}「${DIM_SHORT[id]}」仅 ${s}/5`,
    });
  });

  if (!items.length) {
    items.push({ level: "good", mark: "✓",
      text: "未发现确定性硬伤，出分维度均在 4 分及以上" });
  }
  return items.slice(0, 4);
}

function keyFindingsHTML(r) {
  return `
  <div class="key-findings">
    <div class="kf-title">关键结论</div>
    ${keyFindings(r).map(it => `
      <div class="kf-item kf-${it.level}">
        <span class="kf-mark">${it.mark}</span>
        <span class="kf-text">${escHtml(it.text)}</span>
      </div>`).join("")}
  </div>`;
}

/* ---------- 面板 1：总览 ---------- */
function panelOverview(r, cid) {
  const agg = r.aggregation || {};
  const parse = r.parse || {};
  const adm = r.admission || "NE";

  const reason = adm === "FAIL"
    ? `<div class="notice" style="margin-bottom:16px">知识红线（G0 · 维度2）未通过：${escHtml(agg.reason || r.rules?.g0_rule_verdict || "未通过知识准确性校验")}。评分已终止，其余维度不再出分。</div>`
    : "";

  // 出分维度 / 权重覆盖 / KB 命中 / NE / 双采样 / 规则发现 已经在报告头下方的
  // 统计条里常驻，这里不再重复铺一遍指标块（同一组数字出现两次就是噪音）。
  return `
  <div class="rpanel active" data-p="overview">
    ${reason}
    <div class="chart-grid split">
      ${keyFindingsHTML(r)}
      <div class="chart-box">
        <div class="chart-title">九维得分雷达</div>
        <div class="chart-canvas"><canvas id="radar-${cid}"></canvas></div>
        <div class="chart-hint">满分 5 分；NE 按 0 绘制且不计入加权总分。</div>
      </div>
    </div>
    ${parse.notes ? `<div class="hint" style="margin-top:14px">解析备注：${escHtml(parse.notes)}</div>` : ""}
  </div>`;
}

/* ---------- 面板 2：维度详情 ---------- */
function panelDims(r, cid) {
  const scores = r.scores || {};
  const arbitration = r.arbitration || [];
  const items = DIM_ORDER.filter(id => scores[id]).map(id => {
    const s = scores[id];
    const ne = !!s.ne;
    const cls = ne ? "ne" : (Number(s.score) >= 4 ? "pass" : (id === "2" ? "g0" : "fail"));
    const score = ne
      ? `<span class="dim-score ne">NE</span>`
      : `<span class="dim-score score-${s.score}">${s.score}/5</span>`;

    // 五格迷你条：把 3/5 与 5/5 的差别在扫视层面拉开（只读数字要停下来比）。
    // 颜色在 JS 里按分值算，避免再维护一套 CSS 类组合。
    const sv = Number(s.score) || 0;
    const onColor = sv >= 4 ? "var(--good)" : sv === 3 ? "var(--warn)" : "var(--bad)";
    const meter = (!ne && sv > 0)
      ? `<span class="dim-meter">${[1, 2, 3, 4, 5].map(n =>
          n <= sv ? `<i class="on" style="background:${onColor}"></i>` : `<i></i>`
        ).join("")}</span>`
      : "";

    const arb = arbitration.includes(id) ? `<span class="badge-mini badge-warn">已仲裁</span>` : "";

    // 证据原文通常几百字，全量铺开会变成一堵文字墙。超阈值先折叠，
    // 展开后完整显示（文本不截断，只是视觉收起，避免丢失判定依据）。
    const evText = s.evidence ? String(s.evidence) : "";
    const ev = evText ? `
      <div class="dim-evidence${evText.length > 140 ? " folded" : ""}">
        <div class="dim-evidence-text">${escHtml(evText)}</div>
        ${evText.length > 140
          ? `<button class="ev-toggle" type="button">展开证据 ▾</button>` : ""}
      </div>` : "";

    // rationale 是复核/仲裁给出的判定理由，与 evidence 原文不是一回事，分开展示
    const rat = s.rationale
      ? `<div class="dim-rationale"><span class="dim-rationale-tag">判定理由</span>${escHtml(String(s.rationale))}</div>`
      : "";

    return `
      <div class="dim-item ${cls}">
        <div class="dim-item-head">
          <span class="dim-title">
            <span class="dim-id">${id}</span>
            <span class="dim-name">${escHtml(DIM_NAMES[id])}</span>
            <span class="dim-priority">${DIM_PRI[id]}</span>${arb}
          </span>
          <span class="dim-tail">${meter}${score}</span>
        </div>
        ${ev}
        ${rat}
      </div>`;
  }).join("");

  let emptyHint = `<div class="hint">没有维度出分。</div>`;
  if (!items && (r.admission === "FAIL")) {
    const why = (r.rules && r.rules.findings || []).filter(f => f.verdict === "fail");
    emptyHint = `<div class="hint">
      <strong>未通过知识准入闸门（G0），未进入全维度评分。</strong><br>
      存在 ${why.length} 项确定性知识缺陷，系统按设计直接判定 FAIL 而不给总分——
      宁可不出分，也不让"有硬伤的课件"拿到一个看起来还行的分数。
      ${why.length ? "详见「规则层」标签页。" : ""}
    </div>`;
  }

  const barChart = Object.keys(r.scores || {}).length ? `
    <div class="chart-box" style="margin-bottom:16px">
      <div class="chart-title">维度得分对比</div>
      <div class="chart-canvas xshort"><canvas id="bar-${cid}"></canvas></div>
      <div class="chart-hint">横条按分值分档着色：≥4 绿、3 橙、≤2 红。</div>
    </div>` : "";

  return `
  <div class="rpanel" data-p="dims">
    ${barChart}
    ${items || emptyHint}
  </div>`;
}

/* ---------- 面板 3：双采样一致性 ---------- */
function panelConsistency(r, cid) {
  const ds = r.dual_sample || {};
  if (!ds.dims_total) {
    return `<div class="rpanel" data-p="consistency">
      <div class="hint">本次未启用双采样（开关关闭，或走的是单采样路径）。</div>
    </div>`;
  }
  const paired = ds.numeric_paired ?? ds.dims_total;
  const zero = ds.diff_zero ?? 0;
  const neCnt = ds.dims_total - paired;
  const avgPos = Math.max(0, (ds.avg_path ?? 0) - zero);
  const arb = ds.arbitrated ?? 0;
  const gt1 = ds.diff_gt1 ?? 0;

  const metrics = [
    ["参与维度", ds.dims_total, ""],
    ["两次完全一致", `${zero}/${paired}（${paired ? (zero / paired * 100).toFixed(0) : 0}%）`, zero === paired ? "good" : "warn"],
    ["触发层内仲裁", `${arb}（${((ds.disagreement_rate ?? 0) * 100).toFixed(0)}%）`, arb ? "warn" : "good"],
    ["分差最大", ds.diff_max ?? 0, (ds.diff_max ?? 0) > 1 ? "warn" : ""],
  ];
  const metricHTML = metrics.map(([l, v, cls]) => `
    <div class="metric">
      <span class="metric-label">${escHtml(l)}</span>
      <span class="metric-value ${cls}">${escHtml(String(v))}</span>
    </div>`).join("");

  return `
  <div class="rpanel" data-p="consistency">
    <div class="chart-grid">
      <div class="chart-box">
        <div class="chart-title">两次采样一致性构成</div>
        <div class="chart-canvas short"><canvas id="donut-${cid}"></canvas></div>
        <div class="chart-hint">同一 Judge 用「严格量规」与「学习者视角」各评一次。</div>
      </div>
      <div class="chart-box">
        <div class="chart-title">分歧程度分布</div>
        <div class="chart-canvas short"><canvas id="diffbar-${cid}"></canvas></div>
        <div class="chart-hint">分差 &gt; 阈值（${ds.threshold ?? 1}）即触发层内仲裁，仲裁失败则保守降级为 NE。</div>
      </div>
    </div>
    <div class="metric-row" style="margin-top:16px">${metricHTML}</div>
  </div>`;
}

/* ---------- 面板 4：规则层 ---------- */
function panelRules(r, cid) {
  const rules = r.rules || {};
  const findings = rules.findings || [];
  const sum = rules.summary || {};

  const list = findings.length ? findings.map(f => {
    const cls = { fail: "rule-fail", warn: "rule-warn", pass: "rule-pass" }[f.verdict] || "";
    const sym = { fail: "✗", warn: "△", pass: "✓" }[f.verdict] || "·";
    const ev = f.evidence || f.reason || "";
    return `<div class="rule-item ${cls}"><span class="rule-id">${escHtml(f.rule_id || "—")}</span>${sym} ${escHtml(String(ev).slice(0, 300))}</div>`;
  }).join("") : `<div class="hint">规则层无发现。</div>`;

  const chips = [
    ["G0 判定", rules.g0_rule_verdict || "—",
      rules.g0_rule_verdict === "PASS" ? "good" : rules.g0_rule_verdict ? "bad" : ""],
    ["公式已校验", sum.formula_checked ?? 0, ""],
    ["公式失败", sum.formula_failed ?? 0, sum.formula_failed ? "bad" : "good"],
    ["年级越界", sum.grade_failed ?? 0, sum.grade_failed ? "bad" : "good"],
    ["结构缺失", sum.structure_missing ?? 0, sum.structure_missing ? "warn" : "good"],
  ];
  const chipHTML = chips.map(([l, v, cls]) =>
    `<span class="chip ${cls}">${escHtml(l)} <b>${escHtml(String(v))}</b></span>`).join("");

  return `
  <div class="rpanel" data-p="rules">
    <div class="chart-grid split">
      <div class="chart-box">
        <div class="chart-title">发现按判定分布</div>
        <div class="chart-canvas short"><canvas id="ruledonut-${cid}"></canvas></div>
        <div class="chart-hint">规则层零 LLM：公式恒等、年级越界、结构完整性三类确定性检查。</div>
      </div>
      <div>
        <div class="kf-title">检查项统计</div>
        <div class="chip-row">${chipHTML}</div>
      </div>
    </div>
    <div style="margin-top:16px">${list}</div>
    ${(r.warnings || []).length ? `
      <div class="chart-title" style="margin-top:16px">环境告警</div>
      ${r.warnings.map(w => `<div class="rule-item rule-warn">⚠ ${escHtml(w)}</div>`).join("")}` : ""}
  </div>`;
}

/* ---------- 面板 5：改进建议 ---------- */
/* 一次评测可能产出几十条建议（两位裁判各自给出，主题大量重叠），全量铺开
   会成为整页最长的一块。默认只展示前 6 条，其余折叠，编号全局连续。 */
function panelAdvice(r, cid) {
  const ss = r.suggestions || [];
  if (!ss.length) {
    return `<div class="rpanel" data-p="advice"><div class="hint">本次没有生成改进建议。</div></div>`;
  }
  const LIMIT = 6;
  const rest = ss.length - LIMIT;
  return `
  <div class="rpanel" data-p="advice">
    <ol class="suggest-list">
      ${ss.slice(0, LIMIT).map(s => `<li>${escHtml(s)}</li>`).join("")}
    </ol>
    ${rest > 0 ? `
      <ol class="suggest-list suggest-more hidden">
        ${ss.slice(LIMIT).map(s => `<li>${escHtml(s)}</li>`).join("")}
      </ol>
      <button class="suggest-toggle" type="button">展开其余 ${rest} 条建议 ▾</button>` : ""}
  </div>`;
}

/* ================================================================
 *                    图表绘制（按面板懒加载）
 * ================================================================ */

function bindReportCard(el, r, cid) {
  // 总览面板默认可见，先画它里面的图（维度条形图随 dims Tab 懒加载）
  drawGauge(r, cid);
  drawRadar(r, cid);

  // 折叠类交互统一走事件委托：维度证据展开、改进建议展开
  // （报告卡内容全是动态生成的，逐个绑定既啰嗦又易漏）
  el.addEventListener("click", (e) => {
    const evBtn = e.target.closest(".ev-toggle");
    if (evBtn) {
      const box = evBtn.closest(".dim-evidence");
      if (box) {
        const nowFolded = box.classList.toggle("folded");
        evBtn.textContent = nowFolded ? "展开证据 ▾" : "收起证据 ▴";
      }
      return;
    }
    const sgBtn = e.target.closest(".suggest-toggle");
    if (sgBtn) {
      const panel = sgBtn.closest(".rpanel");
      const more = panel && panel.querySelector(".suggest-more");
      if (more) {
        const nowHidden = more.classList.toggle("hidden");
        sgBtn.textContent = nowHidden
          ? `展开其余 ${more.children.length} 条建议 ▾`
          : "收起建议 ▴";
      }
    }
  });

  el.querySelectorAll(".rtab").forEach(btn => {
    btn.addEventListener("click", () => {
      el.querySelectorAll(".rtab").forEach(b => b.classList.toggle("active", b === btn));
      el.querySelectorAll(".rpanel").forEach(p =>
        p.classList.toggle("active", p.dataset.p === btn.dataset.p));
      ensurePanelCharts(r, cid, btn.dataset.p);
    });
  });
}

function ensurePanelCharts(r, cid, panel) {
  if (panel === "dims") { drawDimBar(r, cid); }
  if (panel === "consistency") { drawConsistency(r, cid); }
  if (panel === "rules") { drawRules(r, cid); }
}

function drawGauge(r, cid) {
  const el = document.getElementById(`gauge-${cid}`);
  if (!el || !window.Chart || charts.has(`gauge-${cid}`)) return;
  const total = r.aggregation?.total_score;
  const v = total != null ? Math.max(0, Math.min(100, Number(total))) : 0;
  const color = v >= 85 ? C.good : v >= 70 ? C.accent : v >= 60 ? C.warn : C.bad;
  charts.set(`gauge-${cid}`, new Chart(el, {
    type: "doughnut",
    data: {
      labels: ["得分", "剩余"],
      datasets: [{
        data: [v, 100 - v],
        backgroundColor: [color, "rgba(148,163,184,0.18)"],
        borderWidth: 0,
        circumference: 180,
        rotation: 270,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: "72%",
      animation: { duration: 600 },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  }));
}

function drawRadar(r, cid) {
  const el = document.getElementById(`radar-${cid}`);
  if (!el || !window.Chart || charts.has(`radar-${cid}`)) return;
  const scores = r.scores || {};
  const labels = DIM_ORDER.map(id => `${id} ${DIM_SHORT[id]}`);
  const data = DIM_ORDER.map(id => {
    const s = scores[id];
    if (!s || s.ne) return 0;
    return Number(s.score) || 0;
  });
  charts.set(`radar-${cid}`, new Chart(el, {
    type: "radar",
    data: {
      labels,
      datasets: [{
        label: "维度得分",
        data,
        backgroundColor: C.accentSoft,
        borderColor: C.accent,
        borderWidth: 2,
        pointBackgroundColor: data.map(d => scoreColor(d)),
        pointBorderColor: "#fff",
        pointBorderWidth: 1.5,
        pointRadius: 3.5,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        r: {
          min: 0, max: 5,
          ticks: { stepSize: 1, color: C.faint, backdropColor: "transparent", font: { size: 10 } },
          grid: { color: C.grid },
          angleLines: { color: C.grid },
          pointLabels: { color: C.label, font: { size: 11 } },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: C.tooltipBg, padding: 8, displayColors: false,
          callbacks: { label: (c) => ` ${c.parsed.r} / 5` },
        },
      },
    },
  }));
}

function drawDimBar(r, cid) {
  const el = document.getElementById(`bar-${cid}`);
  // 画布随 Tab 懒加载，反复切换不能重复创建实例（否则 Canvas is already in use）
  if (!el || !window.Chart || charts.has(`bar-${cid}`)) return;
  const scores = r.scores || {};
  const ids = DIM_ORDER.filter(id => scores[id]);
  const labels = ids.map(id => `${id} ${DIM_SHORT[id]}`);
  const data = ids.map(id => scores[id].ne ? 0 : Number(scores[id].score) || 0);
  const colors = ids.map(id => scores[id].ne ? C.ne : scoreColor(Number(scores[id].score) || 0));

  charts.set(`bar-${cid}`, new Chart(el, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "得分",
        data,
        backgroundColor: colors,
        borderRadius: 4,
        barThickness: 13,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { min: 0, max: 5, ticks: { stepSize: 1, color: C.faint }, grid: { color: C.grid }, border: { display: false } },
        y: { ticks: { color: C.label, font: { size: 11 } }, grid: { display: false }, border: { display: false } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: C.tooltipBg, padding: 8, displayColors: false,
          callbacks: {
            label: (c) => {
              const id = ids[c.dataIndex];
              return scores[id].ne ? " 未出分（NE）" : ` ${c.parsed.x} / 5`;
            },
          },
        },
      },
    },
  }));
}

function drawConsistency(r, cid) {
  const ds = r.dual_sample || {};
  if (!ds.dims_total) return;

  // ① 一致性构成环形
  const donut = document.getElementById(`donut-${cid}`);
  if (donut && !charts.has(`donut-${cid}`)) {
    const paired = ds.numeric_paired ?? ds.dims_total;
    const zero = ds.diff_zero ?? 0;
    const neCnt = ds.dims_total - paired;
    const avgPos = Math.max(0, (ds.avg_path ?? 0) - zero);
    const arb = ds.arbitrated ?? 0;
    charts.set(`donut-${cid}`, new Chart(donut, {
      type: "doughnut",
      data: {
        labels: ["两次完全一致", "有分差·走平均", "触发层内仲裁", "未出分 NE"],
        datasets: [{
          data: [zero, avgPos, arb, neCnt],
          backgroundColor: [C.good, C.warn, C.bad, C.ne],
          borderColor: "#fff", borderWidth: 2,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        cutout: "58%",
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, padding: 10, color: C.label } },
          tooltip: {
            backgroundColor: C.tooltipBg, padding: 8,
            callbacks: { label: (c) => ` ${c.label}：${c.parsed} 个维度` },
          },
        },
      },
    }));
  }

  // ② 分歧程度：分差 0 / 1 / >1 / NE 的分布
  const bar = document.getElementById(`diffbar-${cid}`);
  if (bar && !charts.has(`diffbar-${cid}`)) {
    const paired = ds.numeric_paired ?? ds.dims_total;
    const zero = ds.diff_zero ?? 0;
    const gt1 = ds.diff_gt1 ?? 0;
    const eq1 = Math.max(0, paired - zero - gt1);
    const neCnt = ds.dims_total - paired;
    charts.set(`diffbar-${cid}`, new Chart(bar, {
      type: "bar",
      data: {
        labels: ["完全一致", "分差 1", "分差 >1", "NE"],
        datasets: [{
          label: "维度数",
          data: [zero, eq1, gt1, neCnt],
          backgroundColor: [C.good, C.accent, C.bad, C.ne],
          borderRadius: 4,
          barThickness: 26,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: C.label }, grid: { display: false }, border: { display: false } },
          y: { beginAtZero: true, ticks: { stepSize: 1, color: C.faint }, grid: { color: C.grid }, border: { display: false } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: C.tooltipBg, padding: 8, displayColors: false,
                     callbacks: { label: (c) => ` ${c.parsed.y} 个维度` } },
        },
      },
    }));
  }
}

function drawRules(r, cid) {
  const rules = r.rules || {};
  const findings = rules.findings || [];

  // ① 发现按 verdict 分布
  const donut = document.getElementById(`ruledonut-${cid}`);
  if (donut && !charts.has(`ruledonut-${cid}`)) {
    const n = (v) => findings.filter(f => f.verdict === v).length;
    const data = [n("fail"), n("warn"), n("pass")];
    charts.set(`ruledonut-${cid}`, new Chart(donut, {
      type: "doughnut",
      data: {
        labels: ["fail", "warn", "pass"],
        datasets: [{
          data,
          backgroundColor: [C.bad, C.warn, C.good],
          borderColor: "#fff", borderWidth: 2,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        cutout: "58%",
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, padding: 10, color: C.label } },
          tooltip: { backgroundColor: C.tooltipBg, padding: 8,
                     callbacks: { label: (c) => ` ${c.label}：${c.parsed} 项` } },
        },
      },
    }));
  }

  // ② 检查项统计
  const bar = document.getElementById(`rulesum-${cid}`);
  if (bar && !charts.has(`rulesum-${cid}`)) {
    const s = rules.summary || {};
    const checked = s.formula_checked ?? 0;
    const failed = s.formula_failed ?? 0;
    const gradeF = s.grade_failed ?? 0;
    const structM = s.structure_missing ?? 0;
    charts.set(`rulesum-${cid}`, new Chart(bar, {
      type: "bar",
      data: {
        labels: ["公式已校验", "公式失败", "公式通过", "年级越界", "结构缺失"],
        datasets: [{
          label: "项数",
          data: [checked, failed, Math.max(0, checked - failed), gradeF, structM],
          backgroundColor: [C.accent, C.bad, C.good, C.bad, C.warn],
          borderRadius: 4,
          barThickness: 22,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { beginAtZero: true, ticks: { stepSize: 1, color: C.faint }, grid: { color: C.grid }, border: { display: false } },
          y: { ticks: { color: C.label, font: { size: 11 } }, grid: { display: false }, border: { display: false } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: C.tooltipBg, padding: 8, displayColors: false,
                     callbacks: { label: (c) => ` ${c.parsed.x} 项` } },
        },
      },
    }));
  }
}
