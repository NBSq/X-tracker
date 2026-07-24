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
    const priority = signal.high_priority ? '<span class="priority-flag">High</span>' : "";
    const rules = signal.matched_rules ? `<small>Rule: ${escapeHtml(signal.matched_rules)}</small>` : "";
    return `<tr class="${signal.dashboard_highlight ? "rule-highlight" : ""}">
      <td><strong>${escapeHtml(primary)}${priority}</strong>${secondary}${rules}</td>
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

function outcomeRows(items) {
  if (!items.length) return '<tr><td colspan="7" class="empty-state">No evaluated signals match these filters.</td></tr>';
  return items.map((item) => {
    const primary = item.token || item.narrative || "Unknown";
    const secondary = item.token && item.narrative ? `<small>${escapeHtml(item.narrative)}</small>` : "";
    const change = (value) => `${value >= 0 ? "+" : ""}${Number(value).toFixed(1)}`;
    return `<tr><td><strong>${escapeHtml(primary)}</strong>${secondary}</td>
      <td><span class="outcome outcome-${item.status.toLowerCase()}">${escapeHtml(item.status.toLowerCase())}</span></td>
      <td>${item.evaluation_window_hours}h</td><td>${change(item.hype_change)}</td>
      <td>${change(item.momentum_change)}</td><td>${item.mentions_change >= 0 ? "+" : ""}${item.mentions_change}</td>
      <td>${escapeHtml(item.evaluated_at)}</td></tr>`;
  }).join("");
}

async function ruleRequest(url, method, body) {
  const response = await fetch(url, {
    method,
    headers: {"Content-Type": "application/json", Accept: "application/json"},
    body: body == null ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Request failed: ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

function rulePayload(form) {
  const actions = [...form.querySelectorAll('input[name="actions"]:checked')].map((item) => item.value);
  return {
    name: form.elements.name.value.trim(),
    enabled: form.elements.enabled.checked,
    priority: Number(form.elements.priority.value || 0),
    condition: JSON.parse(form.elements.condition.value),
    action: actions,
  };
}

function showRuleError(message) {
  const target = document.getElementById("rule-form-error");
  if (target) target.textContent = message;
}

function initializeRules() {
  const createForm = document.getElementById("rule-create-form");
  if (createForm) createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const rule = await ruleRequest("/api/rules", "POST", rulePayload(createForm));
      window.location.assign(`/rules/${rule.id}`);
    } catch (error) {
      showRuleError(error instanceof SyntaxError ? "Condition must be valid JSON." : error.message);
    }
  });

  const editForm = document.getElementById("rule-edit-form");
  if (editForm) editForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const ruleId = editForm.dataset.ruleId;
      await ruleRequest(`/api/rules/${ruleId}`, "PUT", rulePayload(editForm));
      window.location.reload();
    } catch (error) {
      showRuleError(error instanceof SyntaxError ? "Condition must be valid JSON." : error.message);
    }
  });

  document.querySelectorAll("[data-rule-toggle]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await ruleRequest(`/api/rules/${button.dataset.ruleToggle}`, "PUT", {enabled: button.dataset.enabled !== "true"});
        window.location.reload();
      } catch (error) {
        showRuleError(error.message);
      }
    });
  });

  const deleteButton = document.getElementById("delete-rule");
  if (deleteButton) deleteButton.addEventListener("click", async () => {
    const ruleId = document.getElementById("rule-edit-form").dataset.ruleId;
    if (!window.confirm("Delete this smart alert rule?")) return;
    try {
      await ruleRequest(`/api/rules/${ruleId}`, "DELETE");
      window.location.assign("/rules");
    } catch (error) {
      showRuleError(error.message);
    }
  });
}

async function refreshStatus() {
  const status = await getJson("/api/status");
  setText("system-status", status.status.charAt(0).toUpperCase() + status.status.slice(1));
  setText("metric-posts", status.analyzed_posts);
}

async function refreshSignals(limit = 50) {
  const watchlistId = new URLSearchParams(window.location.search).get("watchlist_id");
  const suffix = watchlistId ? `&watchlist_id=${encodeURIComponent(watchlistId)}` : "";
  const data = await getJson(`/api/signals?limit=${limit}${suffix}`);
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

async function refreshOutcomes() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("window")) {
    params.set("evaluation_window_hours", params.get("window"));
    params.delete("window");
  }
  const suffix = params.toString() ? `?${params}` : "";
  const [data, summary] = await Promise.all([
    getJson(`/api/outcomes${suffix}`),
    getJson(`/api/outcomes/summary${params.toString() ? `?${params}` : ""}`),
  ]);
  setText("outcome-total", summary.signals_evaluated);
  setText("outcome-rate", `${summary.success_rate.toFixed(1)}%`);
  setText("outcome-hype", `${summary.average_hype_change >= 0 ? "+" : ""}${summary.average_hype_change.toFixed(1)}`);
  setText("outcome-momentum", `${summary.average_momentum_change >= 0 ? "+" : ""}${summary.average_momentum_change.toFixed(1)}`);
  const body = document.getElementById("outcomes-body");
  if (body) body.innerHTML = outcomeRows(data.outcomes);
}

function historyBars(items, metric) {
  if (!items.length) return '<p class="empty-state">Collecting history.</p>';
  return items.slice(-8).map((item) => {
    const value = Number(item[metric] || 0);
    const label = metric === "signal_count" ? value : value.toFixed(1);
    return `<div class="history-bar-row"><span>${escapeHtml(item.bucket_start)}</span><i style="--bar-value:${Math.min(Math.max(value, 0), 100)}%"></i><b>${label}</b></div>`;
  }).join("");
}

function historyEntities(items, period, emptyText) {
  if (!items.length) return `<p class="empty-state">${escapeHtml(emptyText)}</p>`;
  return items.slice(0, 5).map((item) => `<div><strong><a href="/history/narratives/${encodeURIComponent(item.name)}?period=${encodeURIComponent(period)}">${escapeHtml(item.name)}</a></strong><span>${item.signal_count} signals · ${escapeHtml(item.trend.toLowerCase())}</span></div>`).join("");
}

function historyTimelineRows(items) {
  if (!items.length) return '<tr><td colspan="6" class="empty-state">No historical data.</td></tr>';
  return [...items].reverse().map((item) => `<tr><td><strong>${escapeHtml(item.bucket_start)}</strong><small>to ${escapeHtml(item.bucket_end)}</small></td><td>${item.signal_count}</td><td>${item.evaluated_count}</td><td>${item.success_rate == null ? "N/A" : `${item.success_rate.toFixed(1)}%`}</td><td>${item.average_hype_score == null ? "N/A" : item.average_hype_score.toFixed(1)}</td><td>${item.average_momentum_score == null ? "N/A" : item.average_momentum_score.toFixed(1)}</td></tr>`).join("");
}

async function refreshHistory() {
  const params = new URLSearchParams(window.location.search);
  const period = params.get("period") || "30d";
  const watchlistId = params.get("watchlist_id");
  const watchlistQuery = watchlistId ? `&watchlist_id=${encodeURIComponent(watchlistId)}` : "";
  const [summaryData, timelineData, narrativeData] = await Promise.all([
    getJson(`/api/history/summary?period=${encodeURIComponent(period)}${watchlistQuery}`),
    getJson(`/api/history/timeline?period=${encodeURIComponent(period)}${watchlistQuery}`),
    getJson(`/api/history/narratives?period=${encodeURIComponent(period)}${watchlistQuery}`),
  ]);
  const summary = summaryData.summary;
  setText("history-signals", summary.total_signals);
  setText("history-evaluated", summary.evaluated_signals);
  setText("history-success", `${summary.success_rate.toFixed(1)}%`);
  setText("history-hype", Number(summary.average_hype_score || 0).toFixed(1));
  setText("history-momentum", Number(summary.average_momentum_score || 0).toFixed(1));
  setText("history-confidence", Number(summary.average_confidence || 0).toFixed(1));
  document.querySelectorAll("[data-history-metric]").forEach((section) => {
    const target = section.querySelector("[data-history-bars]");
    if (target) target.innerHTML = historyBars(timelineData.timeline, section.dataset.historyMetric);
  });
  const narratives = narrativeData.narratives;
  const groups = {rising: "RISING", new: "NEW", declining: "DECLINING", inactive: "INACTIVE"};
  Object.entries(groups).forEach(([name, trend]) => {
    const target = document.getElementById(`history-${name}-narratives`);
    if (target) target.innerHTML = historyEntities(narratives.filter((item) => item.trend === trend), period, "No matching narratives.");
  });
  const successful = narratives.filter((item) => item.success_rate != null).sort((a, b) => b.success_rate - a.success_rate);
  const consistent = [...narratives].sort((a, b) => b.consistency_score - a.consistency_score);
  const successfulTarget = document.getElementById("history-most-successful");
  const consistentTarget = document.getElementById("history-most-consistent");
  if (successfulTarget) successfulTarget.innerHTML = successful.length ? successful.slice(0, 5).map((item) => `<div><strong>${escapeHtml(item.name)}</strong><span>${item.success_rate.toFixed(1)}% success</span></div>`).join("") : '<p class="empty-state">Collecting outcomes.</p>';
  if (consistentTarget) consistentTarget.innerHTML = consistent.length ? consistent.slice(0, 5).map((item) => `<div><strong>${escapeHtml(item.name)}</strong><span>${item.consistency_score.toFixed(1)} consistency</span></div>`).join("") : '<p class="empty-state">Collecting history.</p>';
  const timelineBody = document.getElementById("history-timeline-body");
  if (timelineBody) timelineBody.innerHTML = historyTimelineRows(timelineData.timeline);
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
  if (page === "outcomes") tasks.push(refreshOutcomes());
  if (page === "history") tasks.push(refreshHistory());
  if (page === "narratives" || page === "tokens") tasks.push(refreshRankings(page));
  try {
    await Promise.all(tasks);
    setText("refresh-time", new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  } catch (error) {
    setText("refresh-time", "Retrying");
    console.error("Dashboard refresh failed", error);
  }
}

async function watchlistRequest(url, method, body = null) {
  const response = await fetch(url, {
    method,
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : null,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

function showWatchlistError(message) {
  setText("watchlist-error", message);
}

function watchlistPayload(form) {
  const data = new FormData(form);
  return {
    name: String(data.get("name") || "").trim(),
    description: String(data.get("description") || "").trim(),
    priority: Number(data.get("priority") || 0),
    minimum_hype_score: Number(data.get("minimum_hype_score") || 0),
    minimum_momentum_score: Number(data.get("minimum_momentum_score") || 0),
    minimum_confidence: Number(data.get("minimum_confidence") || 0),
    enabled: data.has("enabled"),
    telegram_enabled: data.has("telegram_enabled"),
    include_in_digest: data.has("include_in_digest"),
    dashboard_highlight: data.has("dashboard_highlight"),
    case_insensitive: data.has("case_insensitive"),
  };
}

function initializeWatchlists() {
  const form = document.getElementById("watchlist-form");
  if (form) form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const id = form.dataset.watchlistId;
      const result = await watchlistRequest(
        id ? `/api/watchlists/${id}` : "/api/watchlists",
        id ? "PUT" : "POST",
        watchlistPayload(form),
      );
      window.location.assign(`/watchlists/${result.id}`);
    } catch (error) {
      showWatchlistError(error.message);
    }
  });

  const itemForm = document.getElementById("watchlist-item-form");
  if (itemForm) itemForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(itemForm);
    try {
      await watchlistRequest(`/api/watchlists/${itemForm.dataset.watchlistId}/items`, "POST", {
        item_type: data.get("item_type"),
        item_value: String(data.get("item_value") || "").trim(),
      });
      window.location.reload();
    } catch (error) {
      showWatchlistError(error.message);
    }
  });

  document.querySelectorAll("[data-watchlist-item]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await watchlistRequest(`/api/watchlists/${button.dataset.watchlistId}/items/${button.dataset.watchlistItem}`, "DELETE");
        window.location.reload();
      } catch (error) {
        showWatchlistError(error.message);
      }
    });
  });

  const deleteButton = document.getElementById("delete-watchlist");
  if (deleteButton) deleteButton.addEventListener("click", async () => {
    const id = document.getElementById("watchlist-form").dataset.watchlistId;
    if (!window.confirm("Delete this watchlist and its signal associations?")) return;
    try {
      await watchlistRequest(`/api/watchlists/${id}`, "DELETE");
      window.location.assign("/watchlists");
    } catch (error) {
      showWatchlistError(error.message);
    }
  });
}

window.setInterval(refreshPage, POLL_INTERVAL);
initializeRules();
initializeWatchlists();
