let currentJobId = null;
let pollTimer = null;

function $(id) {
  return document.getElementById(id);
}

function setStatus(text, type) {
  const el = $("status");
  el.innerHTML = text;
  el.style.borderColor = "rgba(255,255,255,0.12)";
  if (type === "ok") el.style.borderColor = "rgba(61,220,151,0.35)";
  if (type === "bad") el.style.borderColor = "rgba(255,79,109,0.35)";
}

function statusLabel(s) {
  const map = {
    queued: "排队中",
    starting: "启动中",
    navigating: "打开页面/进入评论区",
    loading: "滚动加载更多评论",
    extracting: "提取评论内容",
    topic: "主题模型构建",
    saving: "保存文件",
    mbert: "mBERT 建模/降维",
    done: "完成",
    no_comments: "未抓到评论（可能需要登录/验证）",
    error: "出错",
  };
  return map[s] || s;
}

function clearDownloads() {
  $("downloads").innerHTML = "";
}

function clearResultViews() {
  const s = $("summary");
  const p = $("preview");
  if (s) {
    s.style.display = "none";
    s.innerHTML = "";
  }
  if (p) {
    p.style.display = "none";
    p.innerHTML = "";
  }
}

function renderSummary(result) {
  const box = $("summary");
  if (!box) return;
  const summary = result?.summary || {};
  const n = summary.n ?? 0;
  const sc = summary.sentiment_counts || {};
  const kws = summary.top_keywords || [];
  const attrs = summary.top_attributes || [];
  const topics = summary.top_topics || [];

  const scLine = Object.keys(sc).length
    ? Object.entries(sc)
        .map(([k, v]) => `${k}:${v}`)
        .join(" | ")
    : "无（未开启情感/或为空）";

  const kwLine = kws.length ? kws.slice(0, 10).map((x) => `${x.word}(${x.count})`).join("，") : "无";
  const attrLine = attrs.length ? attrs.slice(0, 10).map((x) => `${x.word}(${x.count})`).join("，") : "无";
  const topicLine = topics.length ? topics.slice(0, 8).map((x) => `T${x.topic}(${x.count})`).join("，") : "无";

  box.style.display = "block";
  box.innerHTML = `
    <div style="font-weight:700; margin-bottom:6px;">分析结果摘要</div>
    <div>总评论数：<span class="pill">${n}</span></div>
    <div style="margin-top:6px;">情感分布：${scLine}</div>
    <div style="margin-top:6px;">高频关键词：${kwLine}</div>
    <div style="margin-top:6px;">属性关键词：${attrLine}</div>
    <div style="margin-top:6px;">主题分布：${topicLine}</div>
  `;

  // 如果 Plotly 可用：绘制三张柱状图（情感/属性/主题）
  if (window.Plotly) {
    try {
      const scKeys = Object.keys(sc);
      if (scKeys.length) {
        Plotly.newPlot(
          "sentimentChart",
          [{ x: scKeys, y: scKeys.map((k) => sc[k]), type: "bar", marker: { color: "#4f8cff" } }],
          { margin: { l: 30, r: 10, t: 20, b: 30 }, title: "情感分布", paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: "rgba(255,255,255,0.9)" } },
          { displayModeBar: false, responsive: true }
        );
      }
      if (attrs.length) {
        Plotly.newPlot(
          "attrChart",
          [{ x: attrs.slice(0, 10).map((a) => a.word), y: attrs.slice(0, 10).map((a) => a.count), type: "bar", marker: { color: "#7c5cff" } }],
          { margin: { l: 30, r: 10, t: 20, b: 70 }, title: "属性关键词 Top10", paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: "rgba(255,255,255,0.9)" }, xaxis: { tickangle: -35 } },
          { displayModeBar: false, responsive: true }
        );
      }
      if (topics.length) {
        Plotly.newPlot(
          "topicChart",
          [{ x: topics.slice(0, 10).map((t) => `T${t.topic}`), y: topics.slice(0, 10).map((t) => t.count), type: "bar", marker: { color: "#3ddc97" } }],
          { margin: { l: 30, r: 10, t: 20, b: 30 }, title: "主题分布", paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: "rgba(255,255,255,0.9)" } },
          { displayModeBar: false, responsive: true }
        );
      }
    } catch {
      // ignore chart errors
    }
  }
}

function renderPreview(result) {
  const box = $("preview");
  if (!box) return;
  const summary = result?.summary || {};
  const rows = summary.preview || [];
  const cols = summary.preview_cols || [];
  if (!rows.length || !cols.length) return;

  const thead = `<tr>${cols.map((c) => `<th style="text-align:left; padding:6px; border-bottom:1px solid rgba(255,255,255,0.12);">${c}</th>`).join("")}</tr>`;
  const tbody = rows
    .map((r) => {
      return `<tr>${cols
        .map((c) => {
          const v = (r[c] ?? "").toString();
          const short = v.length > 60 ? v.slice(0, 60) + "…" : v;
          return `<td title="${v.replaceAll('"', "&quot;")}" style="padding:6px; border-bottom:1px solid rgba(255,255,255,0.06); color: rgba(255,255,255,0.85);">${short}</td>`;
        })
        .join("")}</tr>`;
    })
    .join("");

  box.style.display = "block";
  box.innerHTML = `
    <div style="font-weight:700; margin-bottom:8px;">数据预览（前 30 条）</div>
    <table style="width:100%; border-collapse:collapse; font-size:12px;">
      <thead>${thead}</thead>
      <tbody>${tbody}</tbody>
    </table>
  `;
}

async function loadResult(jobId) {
  try {
    const r = await fetch(`/api/jobs/${jobId}/result`);
    if (!r.ok) return;
    const data = await r.json();
    renderSummary(data);
    renderPreview(data);
  } catch {
    // ignore
  }
}

function renderDownloads(job) {
  const box = $("downloads");
  box.innerHTML = "";
  if (job.has_xlsx) {
    const a = document.createElement("a");
    a.href = `/api/jobs/${job.job_id}/download/xlsx`;
    a.textContent = "下载 Excel（xlsx）";
    box.appendChild(a);
  }
  if (job.has_csv) {
    const a = document.createElement("a");
    a.href = `/api/jobs/${job.job_id}/download/csv`;
    a.textContent = "下载 CSV";
    box.appendChild(a);
  }
  if (job.has_mbert) {
    const a = document.createElement("a");
    a.href = `/api/jobs/${job.job_id}/download/mbert`;
    a.textContent = "下载 mBERT 可视化数据（json）";
    box.appendChild(a);

    const btn = document.createElement("button");
    btn.className = "btn secondary";
    btn.style.width = "100%";
    btn.style.marginTop = "10px";
    btn.textContent = "查看散点图（mBERT）";
    btn.addEventListener("click", () => showViz(job.job_id));
    box.appendChild(btn);
  }
}

async function checkServer() {
  try {
    const r = await fetch("/api/ping", { method: "GET" });
    $("serverPill").textContent = r.ok ? "在线" : "异常";
  } catch {
    $("serverPill").textContent = "离线";
  }
}

async function createJob(url, role) {
  const r = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, role, mbert: $("mbert")?.checked || false }),
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const data = await r.json();
      if (data && data.detail) msg = data.detail;
    } catch {
      const txt = await r.text();
      if (txt) msg = txt;
    }
    throw new Error(msg);
  }
  const data = await r.json();
  return data.job_id;
}

async function fetchJob(jobId) {
  const r = await fetch(`/api/jobs/${jobId}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return await r.json();
}

async function pollJob(jobId) {
  try {
    const job = await fetchJob(jobId);
    const s = statusLabel(job.status);
    if (job.status === "done") {
      setStatus(`状态：<span class="ok">${s}</span>`, "ok");
      renderDownloads(job);
      loadResult(jobId);
      stopPolling();
      $("startBtn").disabled = false;
      return;
    }
    if (job.status === "no_comments") {
      setStatus(
        `状态：<span class="bad">${s}</span><br/>建议：先在弹出的 Edge 里登录淘宝，再重试。`,
        "bad"
      );
      renderDownloads(job);
      stopPolling();
      $("startBtn").disabled = false;
      return;
    }
    if (job.status === "error") {
      setStatus(
        `状态：<span class="bad">${s}</span><br/>错误：${job.error || "未知错误"}`,
        "bad"
      );
      stopPolling();
      $("startBtn").disabled = false;
      return;
    }

    setStatus(`状态：${s}`);
  } catch (e) {
    setStatus(`状态获取失败：${e.message}`, "bad");
  }
}

function startPolling(jobId) {
  stopPolling();
  pollTimer = setInterval(() => pollJob(jobId), 1500);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function showViz(jobId) {
  try {
    const r = await fetch(`/api/jobs/${jobId}/download/mbert`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const pts = (data && data.points) || [];
    if (!pts.length) {
      setStatus("mBERT 数据为空，无法可视化。", "bad");
      return;
    }
    if (!window.Plotly) {
      setStatus("Plotly 未加载（可能网络受限），请改用下载 json 离线分析。", "bad");
      return;
    }
    const valid = pts.filter((p) => p && p.x != null && p.y != null);
    if (!valid.length) {
      setStatus("mBERT 坐标为空（可能都是空评论），无法绘制散点图。", "bad");
      return;
    }
    const x = valid.map((p) => p.x);
    const y = valid.map((p) => p.y);
    const text = valid.map((p) => `${p.i}. ${p.text || ""}`.slice(0, 120));
    const cluster = valid.map((p) => (p.cluster ?? 0));
    const trace = {
      x,
      y,
      mode: "markers",
      type: "scattergl",
      text,
      hovertemplate: "%{text}<extra>cluster=%{marker.color}</extra>",
      marker: {
        size: 9,
        opacity: 0.85,
        color: cluster,
        colorscale: "Viridis",
        showscale: true,
      },
    };
    const layout = {
      margin: { l: 30, r: 10, t: 30, b: 30 },
      title: "mBERT 2D 散点（PCA）",
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: "rgba(255,255,255,0.9)" },
      xaxis: { zeroline: false, gridcolor: "rgba(255,255,255,0.08)" },
      yaxis: { zeroline: false, gridcolor: "rgba(255,255,255,0.08)" },
    };
    Plotly.newPlot("viz", [trace], layout, { displayModeBar: false, responsive: true });
  } catch (e) {
    setStatus(`可视化加载失败：${e.message}`, "bad");
  }
}

async function onStart() {
  const url = $("url").value.trim();
  const role = $("role").value;
  if (!url) {
    setStatus("请先粘贴商品链接。", "bad");
    return;
  }

  clearDownloads();
  clearResultViews();
  $("startBtn").disabled = true;
  setStatus("正在创建任务…");

  try {
    currentJobId = await createJob(url, role);
    try {
      localStorage.setItem("lastJobId", currentJobId);
    } catch {}
    setStatus(`任务已创建：${currentJobId}<br/>正在运行…`);
    startPolling(currentJobId);
  } catch (e) {
    setStatus(`创建任务失败：${e.message}`, "bad");
    $("startBtn").disabled = false;
  }
}

window.addEventListener("load", () => {
  checkServer();
  $("startBtn").addEventListener("click", onStart);
  // 自动恢复上一次任务（避免后端 reload 后看不到结果）
  try {
    const last = localStorage.getItem("lastJobId");
    if (last) {
      currentJobId = last;
      startPolling(currentJobId);
      loadResult(currentJobId);
    }
  } catch {}
});

