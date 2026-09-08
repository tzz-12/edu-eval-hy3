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
  accent: "#0d9488",
  accentSoft: "rgba(13,148,136,0.14)",
  good: "#047857", goodSoft: "rgba(4,120,87,0.16)",
  warn: "#b45309", warnSoft: "rgba(180,83,9,0.16)",
  bad:  "#be123c", badSoft:  "rgba(190,18,60,0.16)",
  ne:   "#94a3b8", neSoft:   "rgba(148,163,184,0.18)",
  grid: "rgba(22,32,43,0.07)",
  label: "#5b6b7c",
  faint: "#94a3b8",
  tooltipBg: "#16202b",
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
    let html;
    if (healthInfo.demo_mode) {
      // 演示模式下没配 key 是预期状态，显示成「API 未配置」会被误读成系统坏了
      html = `<span class="dot dot-demo"></span><span>演示模式</span>`;
    } else {
      html = `<span class="dot ${ok ? "dot-green" : "dot-red"}"></span><span>${ok ? "API 已配置" : "API 未配置"}</span>`;
    }
    if (!healthInfo.db_writable) {
      html = `<span class="dot dot-warn" title="${escHtml(healthInfo.db_note || "历史库不可写")}"></span><span>历史库只读</span>`;
    } else if (healthInfo.db_note) {
      html += `<span class="dot dot-warn" title="${escHtml(healthInfo.db_note)}"></span>`;
    }
    $("#healthBadge").innerHTML = html;
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
      box.innerHTML = `<div class="conv-empty">暂无历史，提交一次评测后会出现在这里</div>`;
      return;
    }
    box.innerHTML = list.map(r => `
      <button class="conv-item" data-id="${r.id}">
        <div class="conv-top">
          <span class="conv-name">${escHtml(r.file_name || "未命名")}</span>
          <span class="badge-mini badge-${(r.admission || "NE").toLowerCase()}">${escHtml(r.admission || "—")}</span>
        </div>
        <div class="conv-meta">
          <span>${fmtTime(r.created_at)}</span>
          <span>${escHtml(r.grade || "")}</span>
          <span>${r.total_score != null ? Math.round(r.total_score) + " 分" : "未出分"}</span>
        </div>
      </button>
    `).join("");
    $$("#convList .conv-item").forEach(b =>
      b.addEventListener("click", () => openConversation(b.dataset.id)));
  } catch (e) {
    box.innerHTML = `<div class="conv-empty">历史加载失败：${escHtml(e.message)}</div>`;
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
        <span>${escHtml(text)}</span>
      </div>
    </div>`;
  $("#messages").appendChild(el);
  scrollToBottom();
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
  scrollToBottom();
  return el;
}

function scrollToBottom() {
  const m = $("#messages");
  m.scrollTop = m.scrollHeight;
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

  const thinking = addThinking("正在评测（同步阻塞，真实模型约 30–90s）…");
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
  const rules = r.rules || {};
  const findings = rules.findings || [];
  const nFail = findings.filter(f => f.verdict === "fail").length;
  const nWarn = findings.filter(f => f.verdict === "warn").length;
  const ds = r.dual_sample || {};
  const scoreCount = Object.keys(r.scores || {}).length;

  return `
  <div class="report-card" data-cid="${cid}">
    <div class="report-head">
      <div class="report-head-left">
        <div class="report-pill-row">
          <span class="pill pill-${escHtml(adm)}">${escHtml(adm)}</span>
          ${agg.grade ? `<span class="badge-mini badge-${adm === "FAIL" ? "fail" : "pass"}">${escHtml(agg.grade)}</span>` : ""}
          ${agg.verdict ? `<span class="badge-mini badge-ne">${escHtml(agg.verdict)}</span>` : ""}
          ${r.redline ? `<span class="badge-mini badge-fail">红线触发</span>` : ""}
        </div>
        <div class="report-score-line">
          <span class="report-score-num">${total != null ? Number(total).toFixed(1) : "—"}</span>
          <span class="report-score-den">/ 100 加权总分</span>
        </div>
        <div class="report-sub">
          ${scoreCount} 个维度出分 · 有效权重覆盖 ${((agg.weight_coverage ?? 0) * 100).toFixed(0)}%
          · 知识库命中 ${r.kb_hits ?? 0} 条
          ${ds.dims_total ? ` · 双采样 ${ds.dims_total} 维` : ""}
          ${findings.length ? ` · 规则层 ${findings.length} 项发现` : ""}
        </div>
      </div>
      <div class="gauge">
        <canvas id="gauge-${cid}"></canvas>
        <div class="gauge-num">${total != null ? Math.round(total) : "—"}</div>
      </div>
    </div>

    <div class="report-tabs">
      <button class="rtab active" data-p="overview">总览</button>
      <button class="rtab" data-p="dims">维度详情 <span class="rtab-badge">${scoreCount}</span></button>
      <button class="rtab" data-p="consistency">一致性 <span class="rtab-badge">${ds.dims_total || 0}</span></button>
      <button class="rtab" data-p="rules">规则层 <span class="rtab-badge">${findings.length}</span></button>
      <button class="rtab" data-p="advice">改进建议 <span class="rtab-badge">${(r.suggestions || []).length}</span></button>
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

/* ---------- 面板 1：总览 ---------- */
function panelOverview(r, cid) {
  const agg = r.aggregation || {};
  const parse = r.parse || {};
  const adm = r.admission || "NE";
  const skipped = agg.skipped_ne || [];

  const rows = [
    ["准入判定", adm, adm === "PASS" ? "good" : adm === "FAIL" ? "bad" : ""],
    ["出分维度", agg.used_dimensions ?? 0, ""],
    ["未出分 (NE)", skipped.length, skipped.length ? "warn" : ""],
    ["有效权重", `${((agg.weight_coverage ?? 0) * 100).toFixed(0)}%`, (agg.weight_coverage ?? 0) < 0.8 ? "warn" : ""],
    ["解析置信度", parse.parse_confidence != null ? Number(parse.parse_confidence).toFixed(2) : "—", ""],
    ["知识库命中", r.kb_hits ?? 0, ""],
  ];
  const stats = rows.map(([l, v, cls]) => `
    <div class="stat">
      <div class="stat-label">${escHtml(l)}</div>
      <div class="stat-value ${cls}">${escHtml(String(v))}</div>
    </div>`).join("");

  const reason = adm === "FAIL"
    ? `<div class="notice">知识红线（G0 · 维度2）未通过：${escHtml(agg.reason || r.rules?.g0_rule_verdict || "未通过知识准确性校验")}。评分已终止，其余维度不再出分。</div>`
    : "";

  return `
  <div class="rpanel active" data-p="overview">
    ${reason}
    <div class="chart-grid">
      <div class="chart-box">
        <div class="chart-title">九维得分雷达</div>
        <div class="chart-canvas"><canvas id="radar-${cid}"></canvas></div>
        <div class="chart-hint">满分 5 分。NE（未出分）按 0 绘制，实际不计入加权总分。</div>
      </div>
      <div class="chart-box">
        <div class="chart-title">维度得分对比</div>
        <div class="chart-canvas"><canvas id="bar-${cid}"></canvas></div>
        <div class="chart-hint">横条按分值分档着色：≥4 绿、3 橙、≤2 红。</div>
      </div>
    </div>
    <div class="stat-grid">${stats}</div>
    ${parse.notes ? `<div class="hint" style="margin-top:12px">解析备注：${escHtml(parse.notes)}</div>` : ""}
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
    const arb = arbitration.includes(id) ? `<span class="badge-mini badge-warn">已仲裁</span>` : "";
    const ev = s.evidence ? `<div class="dim-evidence">${escHtml(String(s.evidence))}</div>` : "";
    return `
      <div class="dim-item ${cls}">
        <div class="dim-item-head">
          <span class="dim-title">
            <span class="dim-id">${id}</span>
            <span class="dim-name">${escHtml(DIM_NAMES[id])}</span>
            <span class="dim-priority">${DIM_PRI[id]}</span>${arb}
          </span>
          ${score}
        </div>
        ${ev}
      </div>`;
  }).join("");

  return `
  <div class="rpanel" data-p="dims">
    ${items || `<div class="hint">没有维度出分。</div>`}
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

  const rows = [
    ["参与维度", ds.dims_total, ""],
    ["两次完全一致", `${zero}/${paired}（${paired ? (zero / paired * 100).toFixed(0) : 0}%）`, zero === paired ? "good" : "warn"],
    ["走平均路径", ds.avg_path ?? 0, ""],
    ["触发层内仲裁", `${arb}（${((ds.disagreement_rate ?? 0) * 100).toFixed(0)}%）`, arb ? "warn" : "good"],
    ["分差均值", ds.diff_mean ?? 0, (ds.diff_mean ?? 0) > 1 ? "warn" : ""],
    ["分差最大", ds.diff_max ?? 0, (ds.diff_max ?? 0) > 1 ? "warn" : ""],
    ["分差 > 阈值", gt1, gt1 ? "warn" : "good"],
  ];
  const stats = rows.map(([l, v, cls]) => `
    <div class="stat">
      <div class="stat-label">${escHtml(l)}</div>
      <div class="stat-value ${cls}">${escHtml(String(v))}</div>
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
    <div class="stat-grid">${stats}</div>
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

  return `
  <div class="rpanel" data-p="rules">
    <div class="chart-grid">
      <div class="chart-box">
        <div class="chart-title">发现按判定分布</div>
        <div class="chart-canvas short"><canvas id="ruledonut-${cid}"></canvas></div>
        <div class="chart-hint">规则层零 LLM：公式恒等、年级越界、结构完整性三类确定性检查。</div>
      </div>
      <div class="chart-box">
        <div class="chart-title">检查项统计</div>
        <div class="chart-canvas short"><canvas id="rulesum-${cid}"></canvas></div>
        <div class="chart-hint">公式「已校验 / 失败」对比，另计年级越界与结构缺失。</div>
      </div>
    </div>
    <div class="stat-grid">
      <div class="stat"><div class="stat-label">G0 判定</div><div class="stat-value">${escHtml(rules.g0_rule_verdict || "—")}</div></div>
      <div class="stat"><div class="stat-label">公式已校验</div><div class="stat-value">${sum.formula_checked ?? 0}</div></div>
      <div class="stat"><div class="stat-label">公式失败</div><div class="stat-value ${sum.formula_failed ? "bad" : "good"}">${sum.formula_failed ?? 0}</div></div>
      <div class="stat"><div class="stat-label">年级越界</div><div class="stat-value ${sum.grade_failed ? "bad" : "good"}">${sum.grade_failed ?? 0}</div></div>
      <div class="stat"><div class="stat-label">结构缺失</div><div class="stat-value ${sum.structure_missing ? "warn" : "good"}">${sum.structure_missing ?? 0}</div></div>
    </div>
    <div style="margin-top:14px">${list}</div>
    ${(r.warnings || []).length ? `
      <div class="chart-title" style="margin-top:16px">环境告警</div>
      ${r.warnings.map(w => `<div class="rule-item rule-warn">⚠ ${escHtml(w)}</div>`).join("")}` : ""}
  </div>`;
}

/* ---------- 面板 5：改进建议 ---------- */
function panelAdvice(r, cid) {
  const ss = r.suggestions || [];
  const body = ss.length
    ? `<ul class="suggest-list">${ss.map(s => `<li>${escHtml(s)}</li>`).join("")}</ul>`
    : `<div class="hint">本次没有生成改进建议。</div>`;
  return `<div class="rpanel" data-p="advice">${body}</div>`;
}

/* ================================================================
 *                    图表绘制（按面板懒加载）
 * ================================================================ */

function bindReportCard(el, r, cid) {
  // 总览面板默认可见，先画
  drawGauge(r, cid);
  drawRadar(r, cid);
  drawDimBar(r, cid);

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
  if (panel === "consistency") { drawConsistency(r, cid); }
  if (panel === "rules") { drawRules(r, cid); }
}

function drawGauge(r, cid) {
  const el = document.getElementById(`gauge-${cid}`);
  if (!el || !window.Chart) return;
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
  if (!el || !window.Chart) return;
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
  if (!el || !window.Chart) return;
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
