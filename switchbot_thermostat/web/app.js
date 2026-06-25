"use strict";

const TARGET_STEP = 0.5;
const POLL_MS = 10000;

const $ = (id) => document.getElementById(id);
const token = () => localStorage.getItem("token");

let current = null; // last status payload

async function api(path, method = "GET", body) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const t = token();
  if (t) headers["X-Auth-Token"] = t;
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

function fmtAge(seconds) {
  if (seconds == null) return "no reading";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  return `${Math.round(seconds / 60)}m ago`;
}

function fmtClock(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function fmtDuration(seconds) {
  if (seconds <= 0) return "now";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function render(s) {
  current = s;
  const sym = s.unit === "fahrenheit" ? "F" : "C";
  $("temp").textContent = s.temperature == null ? "--" : s.temperature.toFixed(1);
  $("unit").textContent = `°${sym}`;
  $("humidity").textContent = s.humidity == null ? "--% RH" : `${s.humidity}% RH`;
  $("battery").textContent = s.battery == null ? "--% batt" : `${s.battery}% batt`;
  $("age").textContent = fmtAge(s.reading_age);
  $("target").textContent = s.target == null ? "--" : s.target.toFixed(1);
  $("unit-sm") && ($("unit-sm").textContent = `°${sym}`);

  const verb = s.action === "cool" ? "cooling" : "heating";
  $("target-meta").textContent =
    `±${s.hysteresis}°${sym} deadband · from ${s.target_source}`;

  // Badges
  const badges = [];
  const onLabel = s.believed ? `ON (${verb})` : "OFF";
  badges.push(`<span class="badge ${s.believed ? "on" : "off"}">${onLabel}</span>`);
  badges.push(`<span class="badge mode">${s.action}</span>`);
  badges.push(`<span class="badge ${s.paused ? "paused" : ""}">${s.paused ? "Paused" : "Auto"}</span>`);
  if (s.dry_run) badges.push(`<span class="badge dry">dry-run</span>`);
  if (s.off_timer_at) badges.push(`<span class="badge timer" id="badge-timer"></span>`);
  $("badges").innerHTML = badges.join("");

  // Control states
  $("pause").textContent = s.paused ? "Resume" : "Pause";
  $("pause").classList.toggle("active", s.paused);
  $("mode").textContent = s.action === "cool" ? "Mode: Cool" : "Mode: Heat";
  $("out-on").classList.toggle("active", s.believed === true);
  $("out-off").classList.toggle("active", s.believed === false);

  // Auto-off timer card: only relevant while the thermostat is on.
  const timerCard = $("timer-card");
  timerCard.hidden = !s.believed;
  const active = !!s.off_timer_at;
  $("timer-idle").hidden = active;
  $("timer-active").hidden = !active;
  if (active) $("timer-when").textContent = fmtClock(s.off_timer_at);
  tickTimer(); // refresh countdown immediately
}

// Live countdown, recomputed from the absolute off time each second.
function tickTimer() {
  if (!current || !current.off_timer_at) return;
  const remaining = Math.round(current.off_timer_at - Date.now() / 1000);
  const text = remaining <= 0 ? "turning off…" : `in ${fmtDuration(remaining)}`;
  const el = $("timer-remaining");
  if (el) el.textContent = text;
  const badge = $("badge-timer");
  if (badge) badge.textContent = remaining <= 0 ? "⏱ off" : `⏱ ${fmtDuration(remaining)}`;
  if (remaining <= 0) load(); // the daemon should have just turned it off; refresh
}

function msg(text, kind = "") {
  const el = $("msg");
  el.textContent = text;
  el.className = "msg " + kind;
  if (text) setTimeout(() => { if (el.textContent === text) { el.textContent = ""; el.className = "msg"; } }, 4000);
}

async function act(fn, okText) {
  try {
    const s = await fn();
    render(s);
    if (okText) msg(okText, "ok");
  } catch (e) {
    msg(e.message, "error");
  }
}

async function load() {
  try { render(await api("/api/status")); }
  catch (e) { msg(e.message, "error"); }
}

// --- wire up controls ---
$("refresh").addEventListener("click", async () => {
  const btn = $("refresh");
  btn.classList.add("spin");
  await act(() => api("/api/refresh", "POST"), "Updated");
  btn.classList.remove("spin");
});

$("t-up").addEventListener("click", () =>
  act(() => api("/api/target", "POST", { value: (current.target || 0) + TARGET_STEP })));
$("t-down").addEventListener("click", () =>
  act(() => api("/api/target", "POST", { value: (current.target || 0) - TARGET_STEP })));

$("pause").addEventListener("click", () =>
  act(() => api("/api/pause", "POST", { paused: !current.paused })));

$("mode").addEventListener("click", () =>
  act(() => api("/api/mode", "POST", { action: current.action === "cool" ? "heat" : "cool" })));

$("out-on").addEventListener("click", () =>
  act(async () => {
    const r = await api("/api/output", "POST", { on: true, force: true });
    if (r.action_result && r.action_result.dry_run) msg("Dry-run is on — sent anyway (forced).", "ok");
    return r;
  }, "Turning on"));
$("out-off").addEventListener("click", () =>
  act(() => api("/api/output", "POST", { on: false, force: true }), "Turning off"));

$("state-on").addEventListener("click", () =>
  act(() => api("/api/state", "POST", { on: true }), "Marked ON"));
$("state-off").addEventListener("click", () =>
  act(() => api("/api/state", "POST", { on: false }), "Marked OFF"));

// Auto-off timer
document.querySelectorAll(".chip[data-min]").forEach((btn) =>
  btn.addEventListener("click", () =>
    act(() => api("/api/timer", "POST", { minutes: Number(btn.dataset.min) }),
      `Auto-off in ${btn.textContent}`)));
$("timer-at-set").addEventListener("click", () => {
  const t = $("timer-time").value; // "HH:MM"
  if (!t) { msg("Pick a time first.", "error"); return; }
  act(() => api("/api/timer", "POST", { at: t }), `Auto-off at ${t}`);
});
$("timer-cancel").addEventListener("click", () =>
  act(() => api("/api/timer", "POST", { clear: true }), "Timer cancelled"));

// --- service worker (best-effort; only works over https/localhost) ---
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

load();
setInterval(load, POLL_MS);
setInterval(tickTimer, 1000); // smooth per-second countdown between polls
