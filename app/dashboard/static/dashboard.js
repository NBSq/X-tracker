const page = document.body.dataset.page;
const POLL_INTERVAL = 30000;

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value == null ? "" : String(value);
  return node.innerHTML;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

async function getJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

function signalRows(signals) {
  if (!signals.length) return '<tr><td colspan="7" class="empty-state">No signals have been generated.</td></tr>';
  return signals.map((signal) => {
    const primary = signal.token || signal.narrative || "Unknown";
    const secondary = signal.token && signal.narrative ? `<small>${escapeHtml(signal.narrative)}</small>` : "";
    const action = String(signal.action || "watch").toLowerCase();
    const outcome = String(signal.outcome_status || "pending").toLowerCase();
    return `<tr>
      <td><strong>${escapeHtml(primary)}</strong>${secondary}</td>
      <td><span class="type-label">${escapeHtml(signal.signal_type)}</span></td>
      <td><b>${Math.round(signal.hype_score)}</b><span class="score-muted">/100</span></td>
      <td>${Math.round(signal.momentum_score)}</td>
      <td>${signal.confidence}/10</td>
      <td><span class="action action-${escapeHtml(action)}">${escapeHtml(action)}</span></td>
      <td><span class="outcome outcome-${escapeHtml(outcome)}">${escapeHtml(signal.outcome_status || "Pending")}</span></td>
    </tr>`;
  }).join("");
}

function rankingItems(items) {
  if (!items.length) return '<p class="empty-state">No data in the last 24 hours.</p>';
  return items.map((item) => `<div class="ranking-item">
    <div><strong>${escapeHtml(item.name)}</strong><span>${item.mentions} mentions · ${item.average_importance} importance</span></div>
    <div class="ranking-score"><span class="score-bar"><i style="width:${item.hype_score}%"></i></span><b>${item.hype_score}</b></div>
  </div>`).join("");
}

function rankingRows(items, includeMomentum) {
  if (!items.length) return `<tr><td colspan="${includeMomentum ? 6 : 5}" class="empty-state">No data in the last 24 hours.</td></tr>`;
  return items.map((item, index) => `<tr>
    <td class="rank">${index + 1}</td><td><strong>${escapeHtml(item.name)}</strong></td>
    <td>${item.mentions}</td><td>${item.average_importance}</td>
    <td><span class="score-bar"><i style="width:${item.hype_score}%"></i></span><b>${item.hype_score}</b></td>
    ${includeMomentum ? `<td>${item.momentum_score}</td>` : ""}
  </tr>`).join("");
}

function performanceList(items) {
  if (!items.length) return '<p class="empty-state">Collecting outcomes.</p>';
  return items.map((item) => `<div><strong>${escapeHtml(item.name)}</strong><span>${item.average_momentum_change >= 0 ? "+" : ""}${item.average_momentum_change.toFixed(1)} momentum</span></div>`).join("");
}

async function refreshStatus() {
  const status = await getJson("/api/status");
  setText("system-status", status.status.charAt(0).toUpperCase() + status.status.slice(1));
  setText("metric-posts", status.analyzed_posts);
}

async function refreshSignals(limit = 50) {
  const data = await getJson(`/api/signals?limit=${limit}`);
  const body = document.getElementById("signals-body");
  if (body) body.innerHTML = signalRows(data.signals);
}

async function refreshPerformance() {
  const data = await getJson("/api/performance");
  setText("metric-signals", data.signals_generated);
  setText("metric-evaluated", data.signals_evaluated);
  setText("metric-accuracy", `${data.accuracy.toFixed(1)}%`);
  setText("metric-momentum", data.average_momentum.toFixed(1));
  setText("metric-confidence", `${data.average_confidence.toFixed(1)}/10`);
  const strip = document.getElementById("outcome-strip");
  if (strip) strip.innerHTML = `<div class="success"><strong>${data.success}</strong><span>Success</span></div><div class="neutral"><strong>${data.neutral}</strong><span>Neutral</span></div><div class="failed"><strong>${data.failed}</strong><span>Failed</span></div><div><strong>${data.average_momentum_change >= 0 ? "+" : ""}${data.average_momentum_change.toFixed(1)}</strong><span>Avg. momentum change</span></div>`;
  const best = document.getElementById("best-narratives");
  const worst = document.getElementById("worst-narratives");
  if (best) best.innerHTML = performanceList(data.best_narratives);
  if (worst) worst.innerHTML = performanceList(data.worst_narratives);
}

async function refreshRankings(kind, target = "rankings-body") {
  const data = await getJson(`/api/${kind}`);
  const items = data[kind];
  const element = document.getElementById(target);
  if (!element) return;
  element.innerHTML = target === "rankings-body" ? rankingRows(items, kind === "narratives") : rankingItems(items);
}

async function refreshPage() {
  const tasks = [refreshStatus()];
  if (page === "overview") tasks.push(refreshSignals(8), refreshPerformance(), refreshRankings("narratives", "narratives-list"), refreshRankings("tokens", "tokens-list"));
  if (page === "signals") tasks.push(refreshSignals());
  if (page === "performance") tasks.push(refreshPerformance());
  if (page === "narratives" || page === "tokens") tasks.push(refreshRankings(page));
  try {
    await Promise.all(tasks);
    setText("refresh-time", new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  } catch (error) {
    setText("refresh-time", "Retrying");
    console.error("Dashboard refresh failed", error);
  }
}

window.setInterval(refreshPage, POLL_INTERVAL);
