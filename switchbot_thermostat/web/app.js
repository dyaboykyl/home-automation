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
  $("badges").innerHTML = badges.join("");

  // Control states
  $("pause").textContent = s.paused ? "Resume" : "Pause";
  $("pause").classList.toggle("active", s.paused);
  $("mode").textContent = s.action === "cool" ? "Mode: Cool" : "Mode: Heat";
  $("out-on").classList.toggle("active", s.believed === true);
  $("out-off").classList.toggle("active", s.believed === false);
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

// --- service worker (best-effort; only works over https/localhost) ---
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

load();
setInterval(load, POLL_MS);
