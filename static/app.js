/* EduEval Demo · 前端逻辑
 * - Tab 切换
 * - 健康检查 + 演示样本加载
 * - 评测提交（POST /api/evaluate）
 * - 报告渲染（雷达图 + 维度详情 + 双采样）
 * - 历史记录
 */

const API = "";  // 同源部署
let radarChart = null;
let currentReport = null;

// ============== 工具 ==============
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const fmtTime = (ts) => {
  const d = new Date(ts * 1000);
  return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
};
const escHtml = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
}[c]));

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

// ============== Tab 切换 ==============
$$(".tab").forEach(b => b.addEventListener("click", () => {
  $$(".tab").forEach(t => t.classList.remove("active"));
  $$(".tab-pane").forEach(p => p.classList.remove("active"));
  b.classList.add("active");
  const tab = b.dataset.tab;
  $(`#tab-${tab}`).classList.add("active");
  if (tab === "history") loadHistory();
}));

// ============== 启动 ==============
(async function init() {
  try {
    const h = await api("GET", "/api/health");
    const ok = h.api_key_configured;
    $("#healthBadge").innerHTML = `<span class="dot ${ok?"dot-green":"dot-red"}"></span><span>${ok ? "API 已配置" : "API 未配置"}</span>`;
  } catch (e) {
    $("#healthBadge").innerHTML = `<span class="dot dot-red"></span><span>API 异常</span>`;
  }
  try {
    const g = await api("GET", "/api/grades");
    const sel = $("#gradeSelect");
    sel.innerHTML = g.grades.map(x => `<option>${x}</option>`).join("");
  } catch {}
  try {
    const d = await api("GET", "/api/demo-samples");
    const box = $("#demoButtons");
    if (!d.samples.length) {
      box.innerHTML = `<span class="hint">尚无演示样本。先运行 scripts/pregen_demo_reports.py</span>`;
    } else {
      box.innerHTML = d.samples.map(s => `
        <button data-demo="${escHtml(s.id)}" title="点击秒级演示">
          🚀 ${escHtml(s.label)}
          <span class="badge-mini ${s.admission==="PASS"?"badge-pass":"badge-fail"}">${escHtml(s.admission||"")} ${s.total_score ?? ""}</span>
        </button>
      `).join("");
      $$(".demo-buttons button[data-demo]").forEach(b =>
        b.addEventListener("click", () => loadDemo(b.dataset.demo)));
    }
  } catch {}
  loadHistory();
})();

// ============== 文件上传 ==============
$("#fileInput").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  if (f.name.endsWith(".pdf")) {
    $("#textInput").value = `(PDF 上传由后端处理：${f.name})\n\n请等待评测时后端读 PDF…`;
  } else {
    $("#textInput").value = await f.text();
  }
});

// ============== 评测提交 ==============
$("#evaluateBtn").addEventListener("click", async () => {
  const text = $("#textInput").value.trim();
  if (!text) { alert("请先粘贴课件文本"); return; }
  const grade = $("#gradeSelect").value;
  if (!grade) { alert("请选择年级"); return; }

  const btn = $("#evaluateBtn");
  btn.disabled = true;
  $("#evaluateStatus").textContent = "提交中…";
  $("#evaluateProgress").classList.remove("hidden");
  $("#progressText").textContent = "正在调 LLM 评测…（同步阻塞 30-90s）";

  try {
    const r = await api("POST", "/api/evaluate", {
      text,
      grade,
      source: "live",
      dual_sample: $("#dualSampleCheck").checked,
      dual_threshold: parseInt($("#dualThresholdInput").value, 10),
    });
    renderReport(r);
    switchTab("report");
    loadHistory();
    $("#evaluateStatus").textContent = `✓ 完成（id=${r._id ?? "demo"}）`;
  } catch (e) {
    $("#evaluateStatus").textContent = `✗ 失败：${e.message}`;
    alert("评测失败：" + e.message);
  } finally {
    btn.disabled = false;
    $("#evaluateProgress").classList.add("hidden");
  }
});

function switchTab(name) {
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tab-pane").forEach(p => p.classList.toggle("active", p.id === `tab-${name}`));
}

// ============== 演示快捷入口 ==============
async function loadDemo(id) {
  $("#evaluateProgress").classList.remove("hidden");
  $("#progressText").textContent = `加载演示样本 ${id}…`;
  try {
    const r = await api("POST", "/api/evaluate", {
      text: "",
      grade: $("#gradeSelect").value || "九年级",
      source: `demo:${id}`,
      dual_sample: $("#dualSampleCheck").checked,
      dual_threshold: parseInt($("#dualThresholdInput").value, 10),
    });
    renderReport(r);
    switchTab("report");
  } catch (e) {
    alert("演示加载失败：" + e.message);
  } finally {
    $("#evaluateProgress").classList.add("hidden");
  }
}

// ============== 报告渲染 ==============
function renderReport(r) {
  currentReport = r;
  $("#reportEmpty").classList.add("hidden");
  $("#reportContent").classList.remove("hidden");

  const adm = r.admission || "NE";
  $("#admissionBadge").textContent = adm;
  $("#admissionBadge").className = "metric-value admission-" + adm;
  const agg = r.aggregation || {};
  $("#totalScore").textContent = agg.total_score != null ? `${agg.total_score}/100` : "—";
  $("#gradeLevel").textContent = agg.grade || "—";
  $("#verdict").textContent = agg.verdict || "—";

  const g0Notice = $("#g0Notice");
  if (adm === "FAIL") {
    g0Notice.classList.remove("hidden");
    g0Notice.textContent = `⚠️ 知识红线（G0 维度2）触发：${agg.reason || "未通过知识准确性校验"}`;
  } else {
    g0Notice.classList.add("hidden");
  }

  renderRadar(r.scores || {});
  renderDualSample(r.dual_sample || {});
  renderDimDetail(r.scores || {}, r.arbitration || []);
  renderRules(r.rules || {});
  renderWarnings(r.warnings || []);
  renderSuggestions(r.suggestions || []);
}

function renderRadar(scores) {
  const labels = ["1 教学目标", "2 知识准确", "3 学段适配", "4 环节设计",
                  "5 清晰度", "6 安全合规", "7 学情分析", "8 教-学-评", "9 启发引导"];
  const data = labels.map((_, i) => {
    const s = scores[String(i+1)] || {};
    if (s.ne) return 0;
    return Number(s.score || 0);
  });

  if (radarChart) radarChart.destroy();
  const ctx = $("#radarChart").getContext("2d");
  radarChart = new Chart(ctx, {
    type: "radar",
    data: {
      labels,
      datasets: [{
        label: "得分",
        data,
        backgroundColor: "rgba(78,201,176,0.18)",
        borderColor: "#4ec9b0",
        pointBackgroundColor: "#4ec9b0",
        pointRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        r: {
          min: 0, max: 5,
          ticks: { stepSize: 1, color: "#858585", backdropColor: "transparent" },
          grid: { color: "rgba(255,255,255,0.08)" },
          angleLines: { color: "rgba(255,255,255,0.08)" },
          pointLabels: { color: "#d4d4d4", font: { size: 11 } },
        },
      },
      plugins: { legend: { labels: { color: "#d4d4d4" } } },
    },
  });
}

function renderDualSample(ds) {
  const box = $("#dualSampleBlock");
  if (!ds.dims_total) {
    box.innerHTML = `<span class="hint">未启用双采样（关闭或单采样）</span>`;
    return;
  }
  box.innerHTML = `
    <div class="ds-stat"><span class="label">参与维度</span><span class="value">${ds.dims_total}</span></div>
    <div class="ds-stat"><span class="label">走平均路径</span><span class="value">${ds.avg_path}</span></div>
    <div class="ds-stat"><span class="label">触发层内仲裁</span><span class="value">${ds.arbitrated}（${(ds.disagreement_rate*100).toFixed(0)}%）</span></div>
    <div class="ds-stat"><span class="label">两次分差均值</span><span class="value">${ds.diff_mean}</span></div>
    <div class="ds-stat"><span class="label">两次分差最大</span><span class="value">${ds.diff_max}</span></div>
    <div class="ds-stat"><span class="label">完全一致维度</span><span class="value">${ds.diff_zero}/${ds.numeric_paired}</span></div>
  `;
}

const DIM_NAMES = {
  "1":"教学目标明确性与课标对齐","2":"知识绝对正确性（G0）","3":"学段与认知层次适配",
  "4":"教学环节设计合理性","5":"表述清晰度与易懂性","6":"安全合规与价值导向（红线）",
  "7":"学情分析","8":"教-学-评一致性","9":"学习者中心与启发探究","A":"格式与基本可读性"
};
const DIM_PRI = {"2":"G0","6":"P0 红线","1":"P1","3":"P1","8":"P1","4":"P2","7":"P2","9":"P2","5":"P3","A":"AUX"};

function renderDimDetail(scores, arbitration) {
  const ids = Object.keys(DIM_NAMES).sort();
  $("#dimDetail").innerHTML = ids.map(id => {
    const s = scores[id];
    if (!s) return "";
    const ne = s.ne ? `<span class="dim-score ne">NE</span>` :
            `<span class="dim-score score-${s.score}">${s.score}/5</span>`;
    const cls = s.ne ? "ne" : (s.score >= 4 ? "pass" : (id === "2" ? "g0" : "fail"));
    const arb = (arbitration || []).includes(id) ? `<span class="badge-mini badge-fail">已仲裁</span>` : "";
    return `
      <div class="dim-item ${cls}">
        <div class="dim-item-head">
          <span><span class="dim-id">${id}</span><span class="dim-name">${escHtml(DIM_NAMES[id])}</span>
            <span class="dim-priority">${DIM_PRI[id]}</span>${arb}</span>
          ${ne}
        </div>
        ${s.evidence ? `<div class="dim-evidence">📎 ${escHtml(String(s.evidence).slice(0,200))}</div>` : ""}
      </div>
    `;
  }).join("");
}

function renderRules(rules) {
  const findings = rules.findings || [];
  const verdict = rules.g0_rule_verdict || "—";
  if (!findings.length) {
    $("#rulesBlock").innerHTML = `<div class="hint">规则层无发现（verdict=${verdict}）</div>`;
    return;
  }
  $("#rulesBlock").innerHTML = `
    <div class="ds-stat"><span class="label">G0 判定</span><span class="value">${verdict}</span></div>
    ${findings.map(f => {
      const cls = {fail:"rule-fail",warn:"rule-warn",pass:"rule-pass"}[f.verdict] || "";
      const sym = {fail:"✗",warn:"△",pass:"✓"}[f.verdict] || "·";
      return `<div class="rule-item ${cls}">[${f.rule_id||""}] ${sym} ${escHtml(f.evidence || f.reason || "")}</div>`;
    }).join("")}
  `;
}

function renderWarnings(ws) {
  if (!ws.length) {
    $("#warningsBlock").innerHTML = `<span class="hint">无</span>`;
    return;
  }
  $("#warningsBlock").innerHTML = ws.map(w => `<div class="rule-item rule-warn">⚠ ${escHtml(w)}</div>`).join("");
}

function renderSuggestions(ss) {
  if (!ss.length) {
    $("#suggestionsList").innerHTML = `<li class="hint">无</li>`;
    return;
  }
  $("#suggestionsList").innerHTML = ss.map(s => `<li>${escHtml(s)}</li>`).join("");
}

// ============== 历史 ==============
async function loadHistory() {
  try {
    const list = await api("GET", "/api/reports");
    const body = $("#historyBody");
    if (!list.length) {
      body.innerHTML = `<tr><td colspan="8" class="loading">还没有历史记录</td></tr>`;
      return;
    }
    body.innerHTML = list.map(r => `
      <tr>
        <td>${r.id}</td>
        <td>${fmtTime(r.created_at)}</td>
        <td>${escHtml(r.grade||"—")}</td>
        <td class="admission-${r.admission}">${escHtml(r.admission)}</td>
        <td>${r.total_score ?? "—"}</td>
        <td>${r.dual_sample?"✓":"✗"}</td>
        <td title="${escHtml(r.file_name||"")}">${escHtml((r.file_name||"").slice(0,40))}</td>
        <td><button data-id="${r.id}">查看</button></td>
      </tr>
    `).join("");
    $$("#historyBody button[data-id]").forEach(b =>
      b.addEventListener("click", async () => {
        try {
          const full = await api("GET", `/api/reports/${b.dataset.id}`);
          renderReport(full);
          switchTab("report");
        } catch (e) { alert(e.message); }
      }));
  } catch (e) {
    $("#historyBody").innerHTML = `<tr><td colspan="8" class="loading">${escHtml(e.message)}</td></tr>`;
  }
}
