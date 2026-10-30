"use strict";
const $ = (id) => document.getElementById(id);
const history = [];
let maxHold = Array(81).fill(-127);
let paused = false;
let latest = null;
let lastReceived = 0;
let dirty = true;
let timingInitialized = false;
const palette = [[8, 10, 36], [22, 69, 160], [19, 181, 172], [239, 229, 87], [255, 82, 59]];

function limits() {
  const floor = Number($("floor").value);
  return [floor, Math.max(floor + 1, Number($("ceiling").value))];
}

function color(value) {
  const [floor, ceiling] = limits();
  const t = Math.max(0, Math.min(1, (value - floor) / (ceiling - floor))) * 4;
  const i = Math.min(3, Math.floor(t));
  const f = t - i;
  return `rgb(${palette[i].map((v, j) => Math.round(v + f * (palette[i + 1][j] - v))).join(",")})`;
}

function canvas(id) {
  const element = $(id);
  const ratio = window.devicePixelRatio || 1;
  const width = element.clientWidth;
  const height = element.clientHeight;
  if (element.width !== Math.round(width * ratio) || element.height !== Math.round(height * ratio)) {
    element.width = Math.round(width * ratio);
    element.height = Math.round(height * ratio);
  }
  const ctx = element.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.fillStyle = "#0a111d";
  ctx.fillRect(0, 0, width, height);
  ctx.font = "12px system-ui";
  return {ctx, width, height, left: 52, right: width - 15, top: 25, bottom: height - 28};
}

function markers(p) {
  const {ctx, left, right, top, bottom} = p;
  const binWidth = (right - left) / 81;
  for (const center of [2412, 2437, 2462]) {
    ctx.fillStyle = "#8298b518";
    ctx.fillRect(left + (center - 10 - 2399.5) * binWidth, top, 20 * binWidth, bottom - top);
  }
  ctx.strokeStyle = "#e3e9fa80";
  ctx.fillStyle = "#a6b5ca";
  ctx.setLineDash([3, 4]);
  for (const [mhz, label] of [[2402, "37"], [2426, "38"], [2480, "39"]]) {
    const x = left + (mhz - 2399.5) * binWidth;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
    ctx.fillText(label, x - 7, 15);
  }
  ctx.setLineDash([]);
  for (let mhz = 2400; mhz <= 2480; mhz += 10) {
    const x = left + (mhz - 2399.5) * binWidth;
    ctx.fillText(`${mhz}`, x - 15, bottom + 20);
  }
}

function drawSpectrum() {
  const p = canvas("spectrum");
  const {ctx, left, right, top, bottom} = p;
  const [floor, ceiling] = limits();
  const y = (value) => bottom - Math.max(0, Math.min(1, (value - floor) / (ceiling - floor))) * (bottom - top);
  for (let value = Math.ceil(floor / 10) * 10; value <= ceiling; value += 10) {
    ctx.fillStyle = "#a6b5ca"; ctx.fillText(`${value}`, 7, y(value) + 4);
    ctx.strokeStyle = "#25354b"; ctx.beginPath(); ctx.moveTo(left, y(value)); ctx.lineTo(right, y(value)); ctx.stroke();
  }
  if (latest) {
    for (const [values, stroke] of [[maxHold, "#efba6380"], [latest[$("mode").value], "#59d9ec"]]) {
      ctx.strokeStyle = stroke;
      ctx.beginPath();
      values.forEach((value, i) => {
        const x = left + (i + 0.5) * (right - left) / 81;
        if (i) ctx.lineTo(x, y(value)); else ctx.moveTo(x, y(value));
      });
      ctx.stroke();
    }
  }
  markers(p);
}

function drawWaterfall() {
  const p = canvas("waterfall");
  const {ctx, left, right, top, bottom} = p;
  const binWidth = (right - left) / 81;
  const rowHeight = 3;
  const count = Math.min(history.length, Math.floor((bottom - top) / rowHeight));
  for (let row = 0; row < count; row++) {
    history[row][$("mode").value].forEach((value, bin) => {
      ctx.fillStyle = color(value);
      ctx.fillRect(left + bin * binWidth, top + row * rowHeight, binWidth + 0.5, rowHeight);
    });
  }
  ctx.fillStyle = "#a6b5ca";
  for (let row = 0; row < count; row += 25) {
    const age = (history[0].host_time - history[row].host_time).toFixed(1);
    ctx.fillText(`−${age}s`, 0, top + row * rowHeight + 10);
  }
  markers(p);
}

function animate() {
  if (dirty) {
    drawSpectrum(); drawWaterfall();
    const [floor, ceiling] = limits();
    $("scale").textContent = `${floor} … ${ceiling} dBm`;
    dirty = false;
  }
  requestAnimationFrame(animate);
}

const events = new EventSource("/events");
events.onmessage = (event) => {
  const frame = JSON.parse(event.data);
  lastReceived = Date.now();
  if (paused) return;
  latest = frame;
  history.unshift(frame);
  if (history.length > 600) history.pop();
  frame[$("mode").value].forEach((value, i) => {maxHold[i] = Math.max(maxHold[i], value);});
  const highest = Math.max(...frame.peak);
  const frequency = 2400 + frame.peak.indexOf(highest);
  $("stats").textContent = `Sweep #${frame.seq} · ${(frame.sweep_us / 1000).toFixed(1)} ms radio sweep · 81 bins · ${frame.samples} samples/bin · strongest peak ${highest} dBm at ${frequency} MHz · ${new Date(frame.host_time * 1000).toLocaleTimeString()}`;
  dirty = true;
};
events.addEventListener("status", (event) => {
  const state = JSON.parse(event.data);
  const c = state.requested_config || state.applied_config;
  const expected = c && c.samples ? c.samples * 81 * ((c.sample_interval_us || 0) + 30) / 1000 + (c.pause_ms || 0) : 0;
  const stale = state.status === "Receiving" && Date.now() - lastReceived > Math.max(5000, expected * 2 + 2000);
  $("status").textContent = `${paused ? "Display paused · " : ""}${stale ? "No recent measurements" : state.status}${state.port ? ` · ${state.port}` : ""} · ${state.invalid_frames} invalid`;
  if (!timingInitialized && state.applied_config.sample_interval_us !== null) {
    const initial = state.requested_config || state.applied_config;
    $("samples").value = initial.samples;
    $("sample-gap").value = initial.sample_interval_us;
    $("sweep-pause").value = initial.pause_ms;
    timingInitialized = true;
  }
  if (state.config_pending) {
    $("timing-status").textContent = "Requested — waiting for a sweep with the new timing…";
  } else if (state.applied_config.sample_interval_us !== null) {
    const applied = state.applied_config;
    $("timing-status").textContent = `Device reports: ${applied.samples} samples, ${applied.sample_interval_us} µs gap, ${applied.pause_ms} ms pause`;
  }
});
events.onerror = () => {$("status").textContent = "Local server disconnected — reconnecting…";};
$("timing").onsubmit = async (event) => {
  event.preventDefault();
  timingInitialized = true;
  const config = {samples: Number($("samples").value), sample_interval_us: Number($("sample-gap").value), pause_ms: Number($("sweep-pause").value)};
  try {
    const response = await fetch("/api/config", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(config)});
    if (!response.ok) throw new Error(`Server rejected timing (${response.status})`);
    $("timing-status").textContent = "Requested — waiting for device confirmation…";
  } catch (error) {
    $("timing-status").textContent = error.message;
  }
};
$("timing-defaults").onclick = () => {
  $("samples").value = 16; $("sample-gap").value = 128; $("sweep-pause").value = 0;
  $("timing").requestSubmit();
};
$("pause").onclick = () => {paused = !paused; $("pause").textContent = paused ? "Resume display" : "Pause display";};
$("clear").onclick = () => {history.length = 0; maxHold.fill(-127); latest = null; dirty = true;};
for (const id of ["mode", "floor", "ceiling"]) {
  $(id).onchange = () => {maxHold.fill(-127); dirty = true;};
}
window.addEventListener("resize", () => {dirty = true;});
$("waterfall").onmousemove = (event) => {
  const bounds = $("waterfall").getBoundingClientRect();
  const bin = Math.floor((event.clientX - bounds.left - 52) / (bounds.width - 67) * 81);
  const row = Math.floor((event.clientY - bounds.top - 25) / 3);
  if (bin < 0 || bin >= 81 || row < 0 || row >= history.length || event.clientY - bounds.top > bounds.height - 28) return;
  const frame = history[row];
  const mhz = 2400 + bin;
  let ble = "";
  if (mhz % 2 === 0 && mhz >= 2402) {
    const channel = mhz === 2402 ? 37 : mhz === 2426 ? 38 : mhz === 2480 ? 39 : mhz < 2426 ? (mhz - 2404) / 2 : (mhz - 2428) / 2 + 11;
    ble = ` · BLE channel ${channel}`;
  }
  $("hover").textContent = `${mhz} MHz${ble} · mean ${frame.mean[bin]} dBm · peak ${frame.peak[bin]} dBm · ${new Date(frame.host_time * 1000).toLocaleTimeString()} · sweep #${frame.seq}`;
};
animate();
