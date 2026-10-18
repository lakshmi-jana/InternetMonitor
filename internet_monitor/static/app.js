const token = new URLSearchParams(location.search).get("token") || "";
const withToken = (path) => {
  const separator = path.includes("?") ? "&" : "?";
  return token ? `${path}${separator}token=${encodeURIComponent(token)}` : path;
};

const $ = (id) => document.getElementById(id);
const number = (value, digits = 1) =>
  value === null || value === undefined ? "—" : Number(value).toFixed(digits);
const dateText = (value) => value ? new Date(value).toLocaleString() : "—";

async function fetchJson(path, options = {}) {
  const response = await fetch(withToken(path), {
    ...options,
    headers: token ? { ...options.headers, Authorization: `Bearer ${token}` } : options.headers,
  });
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

function renderStatus(status) {
  const connection = status.connectivity;
  if (connection) {
    if (status.probe_host) $("connection-label").textContent = status.probe_host;
    $("connection").textContent = connection.online ? "Online" : "Offline";
    $("connection").className = `status ${connection.online ? "online" : "offline"}`;
    $("connection-detail").textContent = connection.error || `${connection.failed_probes} failed probe(s)`;
    $("latency").textContent = number(connection.latency_ms);
    $("jitter").textContent = `Jitter ${number(connection.jitter_ms)} ms`;
    const loss = connection.packet_loss_percent;
    $("packet-loss").textContent = number(loss, 0);
    $("packet-loss-value").className = loss > 0 ? "loss" : "";
    $("packet-detail").textContent = connection.packets_sent
      ? `${connection.packets_lost} of ${connection.packets_sent} lost`
      : "No ping result";
    $("updated").textContent = `Last checked ${dateText(connection.checked_at)}`;
  }

  const speed = status.speed_test;
  if (speed) {
    $("download").textContent = number(speed.download_mbps, 2);
    $("upload").textContent = number(speed.upload_mbps, 2);
  }
  setTesting(Boolean(status.test_running));
  const cadence = status.speed_interval_until
    ? ` · every ${status.speed_interval_minutes} min until ${dateText(status.speed_interval_until)}`
    : "";
  $("schedule").textContent = status.next_test_at
    ? `Next speed test ${dateText(status.next_test_at)}${cadence}`
    : "Scheduling next speed test…";
}

function setTesting(running) {
  const button = $("run-test");
  button.disabled = running;
  button.textContent = running ? "Testing…" : "Run speed test";
}

function drawChart(canvas, series) {
  const width = Math.max(320, canvas.clientWidth);
  const height = Number(canvas.dataset.height) || 190;
  const scale = window.devicePixelRatio || 1;
  canvas.style.height = `${height}px`;
  canvas.width = width * scale;
  canvas.height = height * scale;
  const context = canvas.getContext("2d");
  context.scale(scale, scale);
  context.clearRect(0, 0, width, height);

  const points = series.flatMap((item) => item.values);
  if (!points.length) {
    context.fillStyle = "#8fa4bf";
    context.font = "13px Segoe UI";
    context.fillText("No data in this range", 18, 28);
    return;
  }

  const padding = { left: 43, right: 15, top: 22, bottom: 25 };
  const minTime = Math.min(...points.map((point) => point.time));
  const maxTime = Math.max(...points.map((point) => point.time));
  const maxValue = Math.max(1, ...points.map((point) => point.value)) * 1.12;
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const x = (time) => padding.left + ((time - minTime) / Math.max(1, maxTime - minTime)) * chartWidth;
  const y = (value) => padding.top + chartHeight - (value / maxValue) * chartHeight;

  context.strokeStyle = "#263a56";
  context.fillStyle = "#8fa4bf";
  context.font = "11px Segoe UI";
  for (let step = 0; step <= 4; step += 1) {
    const yy = padding.top + (chartHeight * step) / 4;
    context.beginPath();
    context.moveTo(padding.left, yy);
    context.lineTo(width - padding.right, yy);
    context.stroke();
    context.fillText(number(maxValue * (1 - step / 4), 0), 6, yy + 4);
  }

  series.forEach((item, index) => {
    context.strokeStyle = item.color;
    context.lineWidth = 2;
    context.beginPath();
    item.values.forEach((point, pointIndex) => {
      const method = pointIndex === 0 ? "moveTo" : "lineTo";
      context[method](x(point.time), y(point.value));
    });
    context.stroke();
    context.fillStyle = item.color;
    context.fillRect(padding.left + index * 105, 5, 10, 3);
    context.fillStyle = "#8fa4bf";
    context.fillText(item.name, padding.left + 15 + index * 105, 10);
  });
}

function renderHistory(history) {
  const connectivity = history.connectivity || [];
  const speed = history.speed_tests || [];
  drawChart($("latency-chart"), [{
    name: "Latency",
    color: "#5eead4",
    values: connectivity
      .filter((row) => row.latency_ms !== null)
      .map((row) => ({ time: Date.parse(row.checked_at), value: row.latency_ms })),
  }]);
  drawChart($("speed-chart"), [
    {
      name: "Download",
      color: "#60a5fa",
      values: speed
        .filter((row) => row.download_mbps !== null)
        .map((row) => ({ time: Date.parse(row.tested_at), value: row.download_mbps })),
    },
    {
      name: "Upload",
      color: "#fbbf24",
      values: speed
        .filter((row) => row.upload_mbps !== null)
        .map((row) => ({ time: Date.parse(row.tested_at), value: row.upload_mbps })),
    },
  ]);
  drawChart($("loss-chart"), [{
    name: "Packet loss",
    color: "#fb7185",
    values: connectivity
      .filter((row) => row.packet_loss_percent !== null && row.packet_loss_percent !== undefined)
      .map((row) => ({ time: Date.parse(row.checked_at), value: row.packet_loss_percent })),
  }]);

  const activity = [
    ...connectivity.map((row) => ({
      time: row.checked_at,
      type: "Connection check",
      detail: `${row.online ? `${number(row.latency_ms)} ms latency` : "Offline"}${row.packet_loss_percent == null ? "" : `, ${number(row.packet_loss_percent, 0)}% loss`}`,
      error: row.error,
    })),
    ...speed.map((row) => ({
      time: row.tested_at,
      type: "Speed test",
      detail: row.error || `${number(row.download_mbps, 2)} down / ${number(row.upload_mbps, 2)} up Mbps`,
      error: row.error,
    })),
  ].sort((a, b) => Date.parse(b.time) - Date.parse(a.time)).slice(0, 12);

  $("activity").replaceChildren(...(activity.length ? activity.map((item) => {
    const row = document.createElement("div");
    row.className = "activity-row";
    const time = document.createElement("time");
    time.textContent = dateText(item.time);
    const type = document.createElement("span");
    type.textContent = item.type;
    const detail = document.createElement("span");
    detail.textContent = item.detail;
    if (item.error) detail.className = "error";
    row.append(time, type, detail);
    return row;
  }) : [Object.assign(document.createElement("p"), { className: "empty", textContent: "No checks recorded yet." })]));
}

async function refresh() {
  try {
    const [status, history] = await Promise.all([
      fetchJson("/api/status"),
      fetchJson(`/api/history?hours=${$("range").value}`),
      refreshLogs(),
    ]);
    renderStatus(status);
    renderHistory(history);
  } catch (error) {
    $("updated").textContent = error.message;
  }
}

let refreshTimer;
function queueRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 250);
}

function connectEvents() {
  const source = new EventSource(withToken("/api/events"));
  source.onopen = () => {
    $("live").textContent = "Live";
    $("live").className = "live";
  };
  source.onerror = () => {
    $("live").textContent = "Reconnecting";
    $("live").className = "live disconnected";
  };
  ["connectivity", "speed_test", "test_state"].forEach((name) => {
    source.addEventListener(name, queueRefresh);
  });
}

$("run-test").addEventListener("click", async () => {
  setTesting(true);
  try {
    await fetchJson("/api/run-test", { method: "POST" });
  } catch (error) {
    setTesting(false);
    $("updated").textContent = error.message;
  }
});
const logPageSize = 100;
let logLimit = logPageSize;

function logDetail(item) {
  if (item.error) return item.error;
  if (item.kind === "speed") {
    return `${number(item.download_mbps, 2)} down / ${number(item.upload_mbps, 2)} up Mbps`;
  }
  const probes = item.failed_probes ? `, ${item.failed_probes} failed probe(s)` : "";
  const loss = item.packet_loss_percent == null ? "" : `, ${number(item.packet_loss_percent, 0)}% packet loss`;
  return `${number(item.latency_ms)} ms latency${probes}${loss}`;
}

function renderLogs(logs) {
  const items = logs.items || [];
  const shown = logs.offset + items.length;
  $("log-summary").textContent = logs.total
    ? `Showing ${shown} of ${logs.total}`
    : "No matching logs in this range.";
  $("load-more").hidden = shown >= logs.total;
  $("logs").replaceChildren(...(items.length ? items.map((item) => {
    const row = document.createElement("div");
    row.className = `log-row${item.failed ? " failed" : ""}`;
    const time = document.createElement("time");
    time.textContent = dateText(item.logged_at);
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = item.kind === "speed" ? "Speed test" : "Connection check";
    const summary = document.createElement("span");
    summary.className = "summary";
    const detail = logDetail(item);
    summary.textContent = item.kind === "speed" && !item.error
      ? detail
      : item.error
        ? `${item.summary}: ${item.error}`
        : `${item.summary} · ${detail}`;
    row.append(time, kind, summary);
    return row;
  }) : [Object.assign(document.createElement("p"), {
    className: "empty",
    textContent: $("log-filter").value === "failures"
      ? "No failures in this range."
      : "No checks recorded yet.",
  })]));
}

async function refreshLogs() {
  const failures = $("log-filter").value === "failures" ? "1" : "0";
  const logs = await fetchJson(
    `/api/logs?hours=${$("log-range").value}&failures=${failures}&limit=${logLimit}`
  );
  renderLogs(logs);
}

$("log-filter").addEventListener("change", () => {
  logLimit = logPageSize;
  refreshLogs().catch((error) => { $("log-summary").textContent = error.message; });
});
$("log-range").addEventListener("change", () => {
  logLimit = logPageSize;
  refreshLogs().catch((error) => { $("log-summary").textContent = error.message; });
});
$("load-more").addEventListener("click", () => {
  logLimit += logPageSize;
  refreshLogs().catch((error) => { $("log-summary").textContent = error.message; });
});

$("range").addEventListener("change", refresh);
window.addEventListener("resize", queueRefresh);

refresh();
connectEvents();
setInterval(() => fetchJson("/api/status").then(renderStatus).catch(() => {}), 30000);
