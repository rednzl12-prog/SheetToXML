/**
 * SheetXML frontend: file drop, background conversion jobs with live progress, AI provider settings,
 * arrangement (instrument / tuning / fingering) with instant re-render, OpenSheetMusicDisplay, text tab,
 * piece analysis, ABC editing and Web Audio playback.
 */

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- state
let appConfig = { providers: [], engines: {}, presets: { instruments: [], tunings: [], styles: [] } };
const discovered = {};             // provider id -> model ids found by "Test"
let currentFile = null;
let current = null;                // the result on screen (transcription or re-render)
let provenance = null;             // the transcription / import result: engine, models, per-system info
let currentAbc = "";               // the ABC the current view was rendered from
let currentFilename = "score.musicxml";
let currentJobId = null;
let renderTimer = null;
let renderSeq = 0;
let osmd = null;
let zoom = 1.0;
let lastFocus = null;

// audio
let audioCtx = null, isPlaying = false, playbackTimeout = null, parsedNotes = [], playbackIndex = 0;

const OCTAVE_LOWER = new Set(["octave-mandolin", "mandocello"]);   // written an octave above sounding pitch
const ENGINE_HINTS = {
  auto: "Best choice. Exported PDFs are read exactly; scans and photos go to Audiveris and, with an AI key, every staff is checked by the AI (staffs that Audiveris and SheetXML agree on cost nothing).",
  vector: "Exact and free, for PDFs exported from MuseScore, Sibelius, Finale or Dorico. Not for scans or photos.",
  audiveris: "Free and offline, for scans and photos. Takes a minute or two per page; dense music may need fixing.",
  hybrid: "Audiveris reads it, then the AI checks each staff against the image. The most accurate option for scans.",
  ai: "The AI model reads every staff on its own, without Audiveris. Uses more API calls.",
};
const ENGINE_LABELS = {
  vector: "Exact PDF reader (no AI)", audiveris: "Audiveris (offline)", hybrid: "Audiveris + AI check",
  ai: "AI only", import: "Imported file", abc: "Edited ABC",
};
const STORE_KEY = "sheetxml.prefs";

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || "{}") || {}; } catch (e) { return {}; }
}
function savePrefs(patch) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify({ ...loadPrefs(), ...patch })); } catch (e) { /* private mode */ }
}

// ---------------------------------------------------------------- init

document.addEventListener("DOMContentLoaded", () => {
  setupEvents();
  initOSMD();
  loadConfig(true);
});

async function loadConfig(first = false) {
  try {
    const res = await fetch("/api/config");
    appConfig = await res.json();
  } catch (err) {
    showToast("Could not reach the SheetXML server: " + err.message, 8000);
    return;
  }
  const prefs = loadPrefs();
  if (first) {
    initArrangement(prefs.arrangement || {});
    if (prefs.engine) $("engineSelect").value = prefs.engine;
    if (prefs.votes) $("votesSelect").value = prefs.votes;
  }
  fillProviders(first ? prefs.provider : $("providerSelect").value);
  fillModels(first ? prefs.model : currentModel());
  buildProviderRows();
  updateEngineUi();
}

function provider(id) {
  return appConfig.providers.find(p => p.id === id) || null;
}

function fillProviders(want) {
  const sel = $("providerSelect");
  sel.innerHTML = "";
  appConfig.providers.forEach(p => {
    const opt = new Option(p.ready ? p.name : `${p.name} (${p.keyOptional ? "not set up" : "no key yet"})`, p.id);
    sel.appendChild(opt);
  });
  const ok = id => id && provider(id);
  sel.value = ok(want) ? want : appConfig.defaultProvider;
}

function fillModels(want) {
  const p = provider($("providerSelect").value);
  const sel = $("modelSelect");
  sel.innerHTML = "";
  if (!p) return;
  const curated = p.models || [];
  if (curated.length) {
    const g = document.createElement("optgroup");
    g.label = "Recommended";
    curated.forEach(m => {
      const o = new Option((m.name || m.id) + (m.vision === false ? " - text only, for the AI note" : ""), m.id);
      o.title = m.description || "";
      g.appendChild(o);
    });
    sel.appendChild(g);
  }
  const ids = new Set(curated.map(m => m.id));
  const extra = (discovered[p.id] || []).filter(id => !ids.has(id));
  if (want && want !== "__other" && !ids.has(want) && !extra.includes(want)) extra.unshift(want);
  if (extra.length) {
    const g = document.createElement("optgroup");
    g.label = "Other models on your account";
    extra.forEach(id => g.appendChild(new Option(id, id)));
    sel.appendChild(g);
  }
  sel.appendChild(new Option("Other model (type its id)...", "__other"));
  const all = [...ids, ...extra];
  sel.value = want && all.includes(want) ? want : (all[0] || "__other");
  updateModelUi();
}

function currentModel() {
  const v = $("modelSelect").value;
  return v === "__other" ? $("modelCustom").value.trim() : v;
}

function updateModelUi() {
  const p = provider($("providerSelect").value);
  const other = $("modelSelect").value === "__other";
  $("modelCustom").classList.toggle("hidden", !other);
  const m = p && (p.models || []).find(x => x.id === $("modelSelect").value);
  $("modelHint").textContent = other ? "Any model id your service offers; it must read images."
    : m ? (m.description || "") : "Found on your account by Test in Settings.";
  $("providerHint").textContent = !p ? "" : p.ready
    ? (p.hasKey ? `Key saved (${p.maskedKey}).` : "Local endpoint, no key needed.")
    : "No key yet - add one in AI keys, or use Audiveris / the exact PDF reader.";
}

function updateEngineUi() {
  const eng = appConfig.engines || {};
  const p = provider($("providerSelect").value);
  const aiReady = Boolean(p && p.ready);
  const sel = $("engineSelect");
  const reasons = {
    audiveris: eng.audiveris ? "" : "Audiveris is not installed",
    hybrid: !eng.audiveris ? "Audiveris is not installed" : !aiReady ? "needs a key for the chosen AI service" : "",
    ai: aiReady ? "" : "needs a key for the chosen AI service",
  };
  [...sel.options].forEach(o => {
    const why = reasons[o.value] || "";
    o.disabled = Boolean(why);
    o.textContent = ENGINE_LABELS[o.value] ? ENGINE_LABELS[o.value] + (why ? ` - ${why}` : "") : o.textContent;
  });
  if (sel.selectedOptions[0] && sel.selectedOptions[0].disabled) sel.value = "auto";
  const engine = sel.value;
  let hint = ENGINE_HINTS[engine] || "";
  if (engine === "auto" && !eng.audiveris && !aiReady) {
    hint += " Right now only exported PDFs can be read: install Audiveris (free) or add an AI key for scans.";
  }
  if (!eng.audiveris && (engine === "auto")) hint += " Audiveris (free offline reader): github.com/Audiveris/audiveris/releases";
  $("engineHint").textContent = hint;
  const usesAi = engine === "auto" || engine === "ai" || engine === "hybrid";
  ["providerGroup", "modelGroup", "votesGroup"].forEach(id => $(id).classList.toggle("hidden", !usesAi));
  $("votesGroup").classList.toggle("hidden", !usesAi || !aiReady);
  const ready = appConfig.providers.filter(x => x.ready).map(x => x.name);
  $("keyStatusText").textContent = ready.length ? `AI keys: ${ready.length === 1 ? ready[0] : ready.length + " services"} ready` : "AI keys (none set)";
  updateModelUi();
}

// ---------------------------------------------------------------- arrangement

function initArrangement(saved) {
  const pr = appConfig.presets;
  const inst = $("instrumentSelect");
  inst.innerHTML = "";
  pr.instruments.forEach(i => inst.appendChild(new Option(i.name, i.id)));
  const style = $("styleSelect");
  style.innerHTML = "";
  pr.styles.forEach(s => style.appendChild(new Option(s.name, s.id)));
  if (saved.instrument && pr.instruments.some(i => i.id === saved.instrument)) inst.value = saved.instrument;
  fillTunings(saved.tuning);
  if (saved.style) style.value = saved.style;
  if (saved.capo !== undefined) $("capoInput").value = saved.capo;
  if (saved.position !== undefined) $("positionInput").value = saved.position;
  if (saved.maxFret !== undefined) $("maxFretInput").value = saved.maxFret;
  if (saved.skill) $("skillLevelSelect").value = saved.skill;
  if (saved.tab === false) $("mandolinTabToggle").checked = false;
  updateArrangementUi();
}

function fillTunings(want) {
  const id = $("instrumentSelect").value;
  const sel = $("tuningSelect");
  sel.innerHTML = "";
  appConfig.presets.tunings.filter(t => t.instrument === id).forEach(t => {
    const o = new Option(`${t.name} (${t.tuning.join(" ")})`, t.id);
    o.title = t.description;
    sel.appendChild(o);
  });
  sel.appendChild(new Option("Custom tuning...", "__custom"));
  const custom = Array.isArray(want) || (typeof want === "string" && want.includes(" "));
  if (custom) {
    sel.value = "__custom";
    const notes = Array.isArray(want) ? want : want.split(/\s+/);
    notes.forEach((n, i) => { if ($("tune" + (i + 1))) $("tune" + (i + 1)).value = n; });
  } else if (want && [...sel.options].some(o => o.value === want)) {
    sel.value = want;
  }
  if (sel.value !== "__custom") sel.dataset.prev = sel.value;
}

function getArrangement() {
  const tuning = $("tuningSelect").value === "__custom"
    ? [1, 2, 3, 4].map(i => $("tune" + i).value.trim()).join(" ")
    : $("tuningSelect").value;
  const arr = {
    instrument: $("instrumentSelect").value,
    tuning,
    capo: $("capoInput").value,
    style: $("styleSelect").value,
    maxFret: $("maxFretInput").value,
    skill: $("skillLevelSelect").value,
  };
  if (arr.style === "position") arr.position = $("positionInput").value;
  return arr;
}

function updateArrangementUi() {
  const style = appConfig.presets.styles.find(s => s.id === $("styleSelect").value);
  $("styleHint").textContent = style ? style.description : "";
  $("positionGroup").classList.toggle("hidden", $("styleSelect").value !== "position");
  $("customTuning").classList.toggle("hidden", $("tuningSelect").value !== "__custom");
  const tabOn = $("mandolinTabToggle").checked;
  document.querySelectorAll(".arr-input").forEach(el => { el.disabled = !tabOn; });
}

function onArrangementChange(e) {
  const tsel = $("tuningSelect");
  if (e && e.target === tsel && tsel.value === "__custom") {     // start the note boxes from the last preset
    const t = appConfig.presets.tunings.find(x => x.id === tsel.dataset.prev);
    if (t) t.tuning.forEach((n, i) => { $("tune" + (i + 1)).value = n; });
  }
  if (tsel.value !== "__custom") tsel.dataset.prev = tsel.value;
  if (e && e.target === $("instrumentSelect")) {
    fillTunings();
    tsel.dataset.prev = tsel.value;
    const inst = appConfig.presets.instruments.find(i => i.id === $("instrumentSelect").value);
    if (inst) $("maxFretInput").value = inst.frets;
  }
  updateArrangementUi();
  savePrefs({ arrangement: { ...getArrangement(), tab: $("mandolinTabToggle").checked } });
  if (currentAbc) {
    clearTimeout(renderTimer);
    $("arrSummaryText").textContent = "Updating the tab...";
    renderTimer = setTimeout(() => rerender(currentAbc), 350);
  }
}

function showArrangementSummary(data, error) {
  const text = $("arrSummaryText"), list = $("arrSummaryList"), box = $("arrSummary");
  box.classList.toggle("is-error", Boolean(error));
  list.innerHTML = "";
  if (error) {
    text.textContent = "This arrangement can't be used: " + error;
    return;
  }
  const a = data && data.arrangement;
  if (!a) {
    text.textContent = data ? "Tab is off: the score has notation only." : "These settings apply to the next conversion, and you can change them afterwards.";
    return;
  }
  text.textContent = a.arrangement;
  const facts = [];
  facts.push(`Tuning, lowest course first: ${a.tuning.join(" ")}` + (a.capo ? ` - capo at fret ${a.capo} (tab frets count from the capo)` : ""));
  if (a.positionsUsed && a.positionsUsed.length) facts.push(`Hand positions used: ${a.positionsUsed.join(", ")}; ${a.positionShifts} position shift(s)`);
  facts.push(`Highest fret: ${a.highestFret}`);
  if (a.transposedOctaves) facts.push(`Moved ${a.transposedOctaves > 0 ? "up" : "down"} ${Math.abs(a.transposedOctaves)} octave(s) to fit the instrument`);
  if (OCTAVE_LOWER.has(a.instrument)) facts.push("Sounds an octave lower than written (treble clef 8vb)");
  if (a.unplayable) facts.push(`Warning: ${a.unplayable} note(s) are out of reach in this arrangement and were left off the tab`);
  list.innerHTML = facts.map(f => `<li${f.startsWith("Warning") ? ' class="warn"' : ""}>${escapeHtml(f)}</li>`).join("");
}

async function rerender(abc, { fromEditor = false } = {}) {
  const seq = ++renderSeq;
  try {
    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ abc, arrangement: getArrangement(), mandolinTab: $("mandolinTabToggle").checked, filename: currentFilename }),
    });
    const data = await res.json();
    if (seq !== renderSeq) return false;
    if (!data.musicxml) throw new Error(data.error || "Could not render.");
    currentAbc = abc;
    showResult(data, { fresh: false, edited: fromEditor });
    return true;
  } catch (err) {
    if (seq !== renderSeq) return false;
    if (fromEditor) showToast("Apply failed: " + err.message, 8000);
    else showArrangementSummary(null, err.message);
    return false;
  }
}

// ---------------------------------------------------------------- events

function setupEvents() {
  const dz = $("dropzone");
  dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("dragover"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
  dz.addEventListener("drop", e => {
    e.preventDefault();
    dz.classList.remove("dragover");
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
  });
  dz.addEventListener("click", e => { if (e.target === dz || e.target.closest("#dropzoneIdle") && !e.target.closest("button")) $("fileInput").click(); });
  $("browseBtn").addEventListener("click", () => $("fileInput").click());
  $("fileInput").addEventListener("change", e => { if (e.target.files.length) handleFile(e.target.files[0]); });
  $("clearFileBtn").addEventListener("click", resetFile);

  $("transcribeBtn").addEventListener("click", startTranscription);
  $("cancelBtn").addEventListener("click", cancelTranscription);
  $("engineSelect").addEventListener("change", () => { savePrefs({ engine: $("engineSelect").value }); updateEngineUi(); });
  $("providerSelect").addEventListener("change", () => { fillModels(); savePrefs({ provider: $("providerSelect").value, model: currentModel() }); updateEngineUi(); });
  $("modelSelect").addEventListener("change", () => { updateModelUi(); if ($("modelSelect").value === "__other") $("modelCustom").focus(); savePrefs({ model: currentModel() }); });
  $("modelCustom").addEventListener("change", () => savePrefs({ model: currentModel() }));
  $("votesSelect").addEventListener("change", () => savePrefs({ votes: $("votesSelect").value }));
  $("loadSampleBtn").addEventListener("click", loadSample);
  $("applyAbcBtn").addEventListener("click", applyAbc);

  document.querySelectorAll(".arr-input").forEach(el => el.addEventListener("change", onArrangementChange));
  $("mandolinTabToggle").addEventListener("change", onArrangementChange);

  // settings dialog
  $("settingsBtn").addEventListener("click", openSettings);
  $("closeSettingsBtn").addEventListener("click", closeSettings);
  $("closeSettingsBtn2").addEventListener("click", closeSettings);
  $("settingsModal").addEventListener("click", e => { if (e.target === $("settingsModal")) closeSettings(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("settingsModal").classList.contains("hidden")) closeSettings(); });
  $("saveKeyBtn").addEventListener("click", saveKeys);
  $("anyKeyBtn").addEventListener("click", () => saveAnyKey());
  $("anyKeyInput").addEventListener("keydown", e => { if (e.key === "Enter") saveAnyKey(); });
  $("anyKeyPickBtn").addEventListener("click", () => saveAnyKey($("anyKeyProvider").value));

  // result tabs (arrow keys move between them)
  const tabs = [...document.querySelectorAll(".tab-btn")];
  tabs.forEach((btn, i) => {
    btn.addEventListener("click", () => selectTab(btn));
    btn.addEventListener("keydown", e => {
      const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      const next = tabs[(i + step + tabs.length) % tabs.length];
      selectTab(next);
      next.focus();
    });
  });

  $("playBtn").addEventListener("click", () => (isPlaying ? pausePlayback() : startPlayback()));
  $("stopBtn").addEventListener("click", stopPlayback);
  $("tempoSlider").addEventListener("input", e => { $("tempoValue").textContent = e.target.value; });
  $("zoomInBtn").addEventListener("click", () => setZoom(zoom + 0.15));
  $("zoomOutBtn").addEventListener("click", () => setZoom(zoom - 0.15));
  $("zoomResetBtn").addEventListener("click", () => setZoom(1));

  $("copyXmlBtn").addEventListener("click", () => copyText(current && current.musicxml, "MusicXML copied."));
  $("downloadXmlBtn").addEventListener("click", () => download(current.musicxml, currentFilename, "application/vnd.recordare.musicxml+xml"));
  $("saveLocalBtn").addEventListener("click", saveToDisk);
  $("copyTabBtn").addEventListener("click", () => copyText(current && current.asciiTab, "Tab copied."));
  $("downloadTabBtn").addEventListener("click", () => download(current.asciiTab, currentFilename.replace(/\.musicxml$/, "") + "-tab.txt", "text/plain"));
  $("aiNoteBtn").addEventListener("click", writeAiNote);
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawScore, 250); });
}

function selectTab(btn) {
  document.querySelectorAll(".tab-btn").forEach(b => {
    const on = b === btn;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
    $(b.dataset.tab).classList.toggle("active", on);
  });
  if (btn.dataset.tab === "scoreTab") setTimeout(drawScore, 50);
}

// ---------------------------------------------------------------- file

const VALID_EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".musicxml", ".xml", ".mxl", ".abc"];

function handleFile(file) {
  const ext = "." + file.name.split(".").pop().toLowerCase();
  if (!VALID_EXTENSIONS.includes(ext)) {
    showToast("Please choose a PDF, an image, or a MusicXML / ABC file.");
    return;
  }
  currentFile = file;
  $("fileName").textContent = file.name;
  $("fileSize").textContent = formatBytes(file.size);
  $("dropzoneIdle").classList.add("hidden");
  $("dropzonePreview").classList.remove("hidden");
  $("transcribeBtn").disabled = false;
  $("thumbnailWrapper").classList.add("hidden");
  if (file.type.startsWith("image/")) {
    const reader = new FileReader();
    reader.onload = e => { $("imageThumbnail").src = e.target.result; $("thumbnailWrapper").classList.remove("hidden"); };
    reader.readAsDataURL(file);
  }
}

function resetFile() {
  currentFile = null;
  $("fileInput").value = "";
  $("dropzonePreview").classList.add("hidden");
  $("dropzoneIdle").classList.remove("hidden");
  $("thumbnailWrapper").classList.add("hidden");
  $("transcribeBtn").disabled = true;
  $("browseBtn").focus();
}

function formatBytes(bytes) {
  if (!bytes) return "0 bytes";
  const i = Math.min(3, Math.floor(Math.log(bytes) / Math.log(1024)));
  return parseFloat((bytes / Math.pow(1024, i)).toFixed(1)) + " " + ["bytes", "KB", "MB", "GB"][i];
}

// ---------------------------------------------------------------- settings / keys

function openSettings() {
  lastFocus = document.activeElement;
  $("verifyStatusBox").classList.add("hidden");
  $("anyKeyPick").classList.add("hidden");
  $("settingsModal").classList.remove("hidden");
  $("anyKeyInput").focus();
}

function closeSettings() {
  $("settingsModal").classList.add("hidden");
  if (lastFocus) lastFocus.focus();
}

function buildProviderRows() {
  const box = $("providerRows");
  box.innerHTML = "";
  appConfig.providers.forEach(p => {
    const row = document.createElement("div");
    row.className = "provider-row";
    const status = p.hasKey ? `Key saved (${p.maskedKey})` : p.keyOptional ? "Key optional" : "No key";
    row.innerHTML = `
      <div class="provider-head">
        <label for="key-${p.id}">${escapeHtml(p.name)}</label>
        <span class="provider-status ${p.ready ? "ok" : ""}">${p.ready ? "&#10003; " : ""}${escapeHtml(status)}</span>
        ${p.keyUrl ? `<a class="banner-link" href="${escapeAttr(p.keyUrl)}" target="_blank" rel="noopener noreferrer">Get a key<span class="visually-hidden"> for ${escapeHtml(p.name)} (opens a new tab)</span></a>` : ""}
      </div>
      ${p.id === "custom" ? `<label class="sub-label" for="customBaseUrl">Base URL (Ollama, LM Studio, Groq, Together...)</label>
        <input type="url" id="customBaseUrl" class="form-input" placeholder="http://localhost:11434/v1" value="${escapeAttr(p.baseUrl || "")}">` : ""}
      <div class="input-with-button">
        <input type="password" id="key-${p.id}" class="form-input" autocomplete="off" placeholder="${escapeAttr(p.hasKey ? "Saved - type to replace" : (p.keyHint || "API key"))}">
        <button type="button" class="btn btn-outline btn-sm" data-show="${p.id}" aria-pressed="false" aria-label="Show ${escapeAttr(p.name)} key">Show</button>
        <button type="button" class="btn btn-secondary btn-sm" data-test="${p.id}" aria-label="Test ${escapeAttr(p.name)} key">Test</button>
      </div>
      <p class="row-result" id="result-${p.id}" aria-live="polite"></p>`;
    box.appendChild(row);
  });
  box.querySelectorAll("[data-show]").forEach(b => b.addEventListener("click", () => {
    const input = $("key-" + b.dataset.show);
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    b.textContent = show ? "Hide" : "Show";
    b.setAttribute("aria-pressed", String(show));
  }));
  box.querySelectorAll("[data-test]").forEach(b => b.addEventListener("click", () => testKey(b.dataset.test, b)));
  const pick = $("anyKeyProvider");
  pick.innerHTML = "";
  appConfig.providers.forEach(p => pick.appendChild(new Option(p.name, p.id)));
}

async function testKey(pid, btn) {
  const out = $("result-" + pid);
  const baseUrl = pid === "custom" ? $("customBaseUrl").value.trim() : "";
  out.className = "row-result";
  out.textContent = "Checking (no tokens are used)...";
  btn.disabled = true;
  try {
    const res = await fetch("/api/verify-key", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: pid, apiKey: $("key-" + pid).value.trim(), baseUrl }),
    });
    const data = await res.json();
    if (data.success) {
      discovered[pid] = data.models || [];
      out.className = "row-result ok";
      out.textContent = "✓ " + data.message;
      if ($("providerSelect").value === pid) fillModels(currentModel());
    } else {
      out.className = "row-result bad";
      out.textContent = "Problem: " + (data.error || "the key was not accepted.");
    }
  } catch (err) {
    out.className = "row-result bad";
    out.textContent = "Problem: " + err.message;
  } finally {
    btn.disabled = false;
    btn.focus();
  }
}

async function postConfig(body) {
  const res = await fetch("/api/save-config", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return res.json();
}

function settingsMessage(text, ok) {
  const box = $("verifyStatusBox");
  box.className = "verify-status " + (ok ? "success" : "error");
  box.textContent = text;
}

async function saveKeys() {
  const keys = {};
  appConfig.providers.forEach(p => { const v = $("key-" + p.id).value.trim(); if (v) keys[p.id] = v; });
  const customBaseUrl = $("customBaseUrl") ? $("customBaseUrl").value.trim() : "";
  if (!Object.keys(keys).length && !customBaseUrl) {
    settingsMessage("Type a key in one of the boxes first.", false);
    return;
  }
  try {
    const data = await postConfig({ keys, customBaseUrl });
    if (!data.success) throw new Error(data.error);
    settingsMessage(data.message, true);
    const pick = Object.keys(keys)[0] || (customBaseUrl ? "custom" : "");
    await loadConfig();
    if (pick && provider(pick) && provider(pick).ready) { $("providerSelect").value = pick; fillModels(); updateEngineUi(); }
  } catch (err) {
    settingsMessage("Not saved: " + err.message, false);
  }
}

async function saveAnyKey(chosen) {
  const key = $("anyKeyInput").value.trim();
  if (!key) { settingsMessage("Paste a key first.", false); return; }
  try {
    const data = await postConfig(chosen ? { keys: { [chosen]: key } } : { anyKey: key });
    if (data.needsProvider) {
      $("anyKeyPick").classList.remove("hidden");
      settingsMessage(data.error, false);
      $("anyKeyProvider").focus();
      return;
    }
    if (!data.success) throw new Error(data.error);
    $("anyKeyInput").value = "";
    $("anyKeyPick").classList.add("hidden");
    settingsMessage(data.message, true);
    await loadConfig();
    if (data.provider && provider(data.provider)) { $("providerSelect").value = data.provider; fillModels(); updateEngineUi(); savePrefs({ provider: data.provider }); }
  } catch (err) {
    settingsMessage("Not saved: " + err.message, false);
  }
}

// ---------------------------------------------------------------- jobs

function setBusy(busy) {
  $("progressSection").classList.toggle("hidden", !busy);
  $("transcribeBtn").disabled = busy || !currentFile;
  $("cancelBtn").disabled = false;
  $("cancelBtn").textContent = "Cancel";
}

function setProgress(frac) {
  const pct = Math.round((frac || 0) * 100);
  $("progressBar").style.width = pct + "%";
  $("progressBarBox").setAttribute("aria-valuenow", String(pct));
}

async function startTranscription() {
  if (!currentFile) { showToast("Choose or drop a sheet music file first."); return; }
  if ($("modelSelect").value === "__other" && !currentModel() && $("engineSelect").value !== "vector" && $("engineSelect").value !== "audiveris") {
    showToast("Type the model id, or pick a model from the list.");
    $("modelCustom").focus();
    return;
  }
  const arr = getArrangement();
  const form = new FormData();
  form.append("file", currentFile);
  form.append("engine", $("engineSelect").value);
  form.append("provider", $("providerSelect").value);
  form.append("model", currentModel());
  form.append("votes", $("votesSelect").value);
  form.append("pageRange", $("pageRange").value.trim());
  form.append("mandolinTab", $("mandolinTabToggle").checked ? "true" : "false");
  form.append("skillLevel", arr.skill);
  form.append("arrangement", JSON.stringify(arr));

  $("resultSection").classList.add("hidden");
  $("progressTitle").textContent = `Converting ${currentFile.name}`;
  $("progressSubtitle").textContent = "Uploading...";
  setProgress(0);
  $("progressLog").innerHTML = "";
  setBusy(true);
  try {
    const res = await fetch("/api/transcribe", { method: "POST", body: form });
    const data = await res.json();
    if (!data.jobId) throw new Error(data.error || "The server did not start a job.");
    currentJobId = data.jobId;
    pollJob();
  } catch (err) {
    setBusy(false);
    showToast("Could not start: " + err.message, 9000);
  }
}

async function pollJob() {
  const jobId = currentJobId;
  let job;
  try {
    const res = await fetch(`/api/job?id=${encodeURIComponent(jobId)}`);
    job = await res.json();
  } catch (err) {
    if (jobId !== currentJobId) return;
    $("progressSubtitle").textContent = "Waiting for the server... " + err.message;
    setTimeout(pollJob, 2500);
    return;
  }
  if (jobId !== currentJobId) return;
  if (!job.status) {
    currentJobId = null;
    setBusy(false);
    showToast(job.error || "The job was lost - please try again.", 9000);
    return;
  }
  setProgress(job.progress);
  const log = job.log || [];
  if (log.length) $("progressSubtitle").textContent = log[log.length - 1];
  $("progressLog").innerHTML = log.slice(-8).map(line => `<li>${escapeHtml(line)}</li>`).join("");
  $("progressLog").scrollTop = $("progressLog").scrollHeight;
  if (job.status === "running") { setTimeout(pollJob, 1000); return; }
  currentJobId = null;
  setBusy(false);
  if (job.status === "done") {
    showResult(job.result, { fresh: true });
  } else if (job.status === "cancelled") {
    showToast("Conversion cancelled.");
  } else {
    showToast(job.error || "Conversion failed.", 14000);
    if (/Settings/.test(job.error || "")) openSettings();
  }
}

function cancelTranscription() {
  if (!currentJobId) { setBusy(false); return; }
  $("cancelBtn").disabled = true;
  $("cancelBtn").textContent = "Cancelling...";
  fetch("/api/cancel", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ jobId: currentJobId }),
  }).catch(err => showToast("Cancel failed: " + err.message));
}

async function applyAbc() {
  const abc = $("abcEditor").value.trim();
  if (!abc) { showToast("The ABC text is empty."); return; }
  $("applyAbcBtn").disabled = true;
  if (await rerender(abc, { fromEditor: true })) showToast("Edits applied.");
  $("applyAbcBtn").disabled = false;
}

async function loadSample() {
  try {
    // render the sample with the chosen arrangement: fetch it, then re-render through /api/render
    const res = await fetch("/api/sample");
    const data = await res.json();
    if (!data.musicxml) throw new Error(data.error || "no sample");
    currentFilename = (data.filename || "sample").replace(/\.[^/.]+$/, "") + ".musicxml";
    showResult(data, { fresh: true });
    rerender(data.abc);
    showToast("Loaded the sample score.");
  } catch (err) {
    showToast("Could not load the sample: " + err.message);
  }
}

// ---------------------------------------------------------------- results

function engineLabel(p) {
  if (!p) return "";
  const base = ENGINE_LABELS[p.engine] || p.engine || "";
  const models = Object.keys(p.modelsUsed || {});
  return models.length ? `${base}: ${models.join(", ")}` : base;
}

function showResult(data, { fresh = false, edited = false } = {}) {
  stopPlayback();
  current = data;
  if (fresh) {
    provenance = data;
    currentAbc = data.abc || "";
    $("abcEditor").value = currentAbc;
    currentFilename = (data.filename || "score").replace(/\.[^/.]+$/, "") + ".musicxml";
    $("aiNoteContent").innerHTML = "";
  } else if (edited && provenance) {
    provenance = { ...provenance, edited: true };
  }
  const meta = data.metadata || {};
  $("scoreTitleDisplay").textContent = meta.title || "Score";
  $("scoreComposerBadge").textContent = meta.composer || "Composer unknown";
  $("scoreKeyBadge").textContent = meta.keySignature || "";
  $("scoreTimeBadge").textContent = meta.timeSignature || "";
  $("scoreMeasuresBadge").textContent = `${meta.measureCount || 0} measures`;
  $("engineBadge").textContent = engineLabel(provenance) + (provenance && provenance.edited ? " (edited)" : "");
  $("schemaBadge").className = "badge " + (data.isValid ? "badge-success" : "badge-meta");
  $("schemaBadge").textContent = data.isValid ? "✓ Valid MusicXML 4.0" : `MusicXML notes: ${(data.validationErrors || []).length}`;

  // warnings come from the reading; the bars to check from this rendering
  const review = data.reviewBars || [];
  const warnings = [...new Set([...(provenance && provenance.warnings) || [], ...(data.warnings || [])])];
  $("reviewBarsText").textContent = review.length
    ? `Please check measure${review.length > 1 ? "s" : ""} ${review.join(", ")} (marked "check" in the score): the notes don't add up to a full bar.`
    : warnings.length ? "Notes from the reader:" : "";
  $("warningsList").innerHTML = warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("");
  $("resultNotices").classList.toggle("hidden", !review.length && !warnings.length);

  $("xmlFilenameDisplay").textContent = currentFilename;
  $("xmlLinesCount").textContent = `${data.musicxml.split("\n").length} lines`;
  $("xmlCodeContent").textContent = data.musicxml;
  $("asciiTabContent").textContent = data.asciiTab || "Tab is switched off (step 2).";
  $("copyTabBtn").disabled = $("downloadTabBtn").disabled = !data.asciiTab;

  renderAbout(data.details);
  renderInfo(data);
  showArrangementSummary(data);

  const bpm = parseInt(meta.tempo, 10);
  if (bpm >= 40 && bpm <= 220) { $("tempoSlider").value = bpm; $("tempoValue").textContent = bpm; }
  parseNotesForPlayback(data.musicxml);
  $("resultSection").classList.remove("hidden");
  renderScore(data.musicxml);
  if (fresh) $("resultSection").scrollIntoView({ behavior: "smooth" });
}

function fmtTime(s) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function li(items) {
  return `<ul class="facts">${items.filter(Boolean).map(x => `<li>${x}</li>`).join("")}</ul>`;
}

function renderAbout(d) {
  const box = $("aboutContent");
  if (!d) { box.innerHTML = `<p class="field-hint">No analysis for this score.</p>`; $("aiNoteBtn").disabled = true; return; }
  $("aiNoteBtn").disabled = false;
  const e = escapeHtml;
  const k = d.key || {};
  let keyLine = `<strong>${e(k.name || "?")}</strong>`;
  if (k.detected && k.agrees === false) keyLine += ` - the notes point to <strong>${e(k.detected)}</strong>`;
  else if (k.detected) keyLine += ` - the notes agree (${Math.round((k.confidence || 0) * 100)}% match)`;
  const cards = [];
  cards.push(card("Key", keyLine + (k.note ? `<p class="small">${e(k.note)}</p>` : "") +
    ((k.changes || []).length ? `<p class="small">Changes: ${k.changes.map(c => `bar ${c.bar} to ${e(c.key)}`).join(", ")}</p>` : "")));
  cards.push(card("Meter and tempo", `${e(d.meter)} <span class="small">(${e(d.timeSignatureFeel || "")})</span><br>` +
    (d.tempo ? `${d.tempo} bpm` : `no tempo marked${d.tempoAssumed ? ` (timings assume ${d.tempoAssumed} bpm)` : ""}`) +
    (d.durationSeconds ? `<br>about ${fmtTime(d.durationSeconds)} long${d.durationIncludesRepeats ? " with repeats" : ""}` : "")));
  cards.push(card("Length", `${d.measures} bars${d.pickup ? " plus a pickup" : ""}, ${d.noteCount} notes`));
  if (d.range) cards.push(card("Range", `${e(d.range.lowest)} to ${e(d.range.highest)} <span class="small">(${d.range.semitones} semitones)</span>`));
  if (d.difficulty) {
    cards.push(card("Difficulty", `<strong>${e(d.difficulty.level)}</strong> (${d.difficulty.score}/10)` +
      li(d.difficulty.reasons.map(e))));
  }
  if (d.rhythm) {
    const r = d.rhythm;
    cards.push(card("Rhythm", li([
      d.shortestNote ? `shortest note: ${e(d.shortestNote)}` : "",
      r.dottedNotes ? `${r.dottedNotes} dotted notes` : "",
      r.tupletNotes ? `${r.tupletNotes} tuplet notes` : "",
      r.tiedNotes ? `${r.tiedNotes} ties` : "",
      `syncopation: ${e(r.syncopation)}`,
      r.doubleStops ? `${r.doubleStops} double stops` : "",
    ])));
  }
  if (d.accidentals) {
    const a = d.accidentals;
    cards.push(card("Accidentals", a.count ? `${a.count} (${e(a.notes.join(", "))})` : "none - everything is in the key"));
  }
  let html = `<div class="metadata-grid about-grid">${cards.join("")}</div>`;
  const f = d.form || {};
  if (f.pattern) {
    html += section("Form", `<p>${e(f.pattern)}</p><p class="small">${e(f.shape || "")}` +
      (f.repeats ? ` - ${f.repeats} repeat sign(s)${f.endings ? `, ${f.endings} 1st/2nd endings` : ""}, ${f.playedBars} bars played in all` : "") + "</p>");
  }
  if ((d.tips || []).length) html += section("Practice tips", li(d.tips.map(e)));
  const m = d.mandolin;
  if (m) {
    html += section("On the fretboard", li([
      m.arrangement ? e(m.arrangement) : "",
      `${m.openStringsPercent}% open strings, ${m.firstPositionPercent}% in frets 0-7, highest fret ${m.highestFret}`,
      m.stringUsage ? "notes per course: " + Object.entries(m.stringUsage).map(([c, n]) => `${e(c)} ${n}`).join(", ") : "",
      m.unplayable ? `Warning: ${m.unplayable} note(s) left off the tab (out of reach)` : "",
    ]));
  }
  const h = d.harmony;
  if (h) {
    html += section(`Chords that fit (${e(h.key)})`,
      `<p class="small">Suggestions worked out from the melody's strong-beat notes - not chords printed in the original.</p>` +
      `<div class="chord-shapes" aria-label="Mandolin chord shapes">${(h.mandolinChords || []).map(c =>
        `<div class="chord-shape"><strong>${e(c.chord)}</strong> <span class="small">${e(c.roman)}</span><code>${e(c.frets)}</code></div>`).join("")}</div>` +
      `<p class="small">Frets from the lowest course to the highest (${e(h.chordTuning || "")}); 0 = open, x = not played.</p>` +
      ((h.chordsPerBar || []).length ? `<details><summary>Suggested chord for each bar</summary><div class="chord-bars">${h.chordsPerBar.map(c =>
        `<span><span class="small">bar ${c.bar}</span> ${e(c.chord)}</span>`).join("")}</div></details>` : ""));
  }
  box.innerHTML = html;
}

function card(label, body) {
  return `<div class="meta-card"><span class="meta-label">${escapeHtml(label)}</span><div class="meta-value small-value">${body}</div></div>`;
}

function section(title, body) {
  return `<section class="about-section"><h4>${title}</h4>${body}</section>`;
}

const STATUS_TEXT = {
  confirmed: "confirmed by Audiveris + SheetXML's notehead reader - no AI needed",
  model: "read by the AI",
  repaired: "read by the AI, then re-asked to fix a problem",
  fallback: "the AI could not read it - Audiveris / SheetXML's own reading used",
};

function fifthsText(n) {
  if (n === undefined || n === null) return "?";
  return n === 0 ? "no sharps or flats" : `${Math.abs(n)} ${n > 0 ? "sharp" : "flat"}${Math.abs(n) > 1 ? "s" : ""}`;
}

function renderInfo(data) {
  const e = escapeHtml;
  const p = provenance || data;
  const info = p.info || {};
  const parts = [`<h4>How it was read</h4><p><strong>${e(ENGINE_LABELS[p.engine] || p.engine || "")}</strong>${p.edited ? " - then edited by you" : ""}</p>`];
  const used = Object.entries(p.modelsUsed || {});
  if (used.length) parts.push(`<p>AI calls: ${used.map(([m, n]) => `${e(m)} (${n} call${n > 1 ? "s" : ""})`).join(", ")}</p>`);
  const systems = info.systems || [];
  if (info.skippedSystems) {
    parts.push(`<p class="good">&#10003; ${info.skippedSystems} of ${systems.length || "?"} staff system(s) confirmed by Audiveris + code - no AI needed.</p>`);
  }
  const hd = info.header;
  if (hd) {
    const SRC = { model: "AI", pixels: "image check", omr: "Audiveris", reask: "AI asked again", codeBarLength: "notes per bar" };
    const votes = (o, fmt) => Object.entries(o || {}).filter(([k]) => k !== "chosen")
      .map(([k, v]) => `${e(SRC[k] || k)}: ${e(k === "codeBarLength" ? v : fmt(v))}`).join(", ");
    parts.push(`<details><summary>Key, time and clef decisions</summary>${li([
      hd.key ? `Key signature: <strong>${e(fifthsText(hd.key.chosen))}</strong> (${votes(hd.key, fifthsText)})` : "",
      hd.meter ? `Time signature: <strong>${e(hd.meter.chosen)}</strong> (${votes(hd.meter, String)})` : "",
      hd.clef ? `Clef: <strong>${e(hd.clef.chosen)}</strong> (${votes(hd.clef, String)})` : "",
      hd.reasked ? `asked the AI again about: ${e(hd.reasked.join(", "))}` : "",
    ])}</details>`);
  }
  if (systems.length) {
    parts.push(`<details${systems.some(s => s.status === "fallback" || (s.problems || []).length) ? " open" : ""}><summary>Each staff system</summary><ol class="system-list">${systems.map(s =>
      `<li><strong>Page ${s.page}, bars ${s.measures[0]}-${s.measures[1]}</strong>: ${e(STATUS_TEXT[s.status] || s.status)}` +
      (s.model && s.status !== "confirmed" ? ` (${e(s.model)})` : "") +
      (s.fromOmr ? `; ${s.fromOmr} bar(s) taken from Audiveris` : "") +
      ((s.problems || []).length ? `<br><span class="small warn">Check: ${e(s.problems.join(" | "))}</span>` : "") + "</li>").join("")}</ol></details>`);
  }
  if ((data.reviewBars || []).length) parts.push(`<p class="warn">Measures to check: ${data.reviewBars.join(", ")}</p>`);
  $("provenanceContent").innerHTML = parts.join("");

  const ok = data.isValid;
  $("validationStatusBox").className = "validation-status-box " + (ok ? "valid" : "invalid");
  $("validationStatusBox").querySelector(".status-icon").textContent = ok ? "✓" : "!";
  $("validationHeadline").textContent = ok ? "Valid MusicXML 4.0" : "Schema notes";
  $("validationMessage").textContent = ok ? "Checked against the official MusicXML 4.0 schema: any notation program can open it."
    : "The file is well-formed XML; the schema reported:";
  $("validationErrorsList").innerHTML = (ok ? [] : data.validationErrors || []).map(x => `<li>${e(x)}</li>`).join("");

  const meta = data.metadata || {};
  $("metaGrid").innerHTML = [
    ["Title", meta.title], ["Composer", meta.composer || "-"], ["Key signature", meta.keySignature],
    ["Time signature", meta.timeSignature], ["Clef", meta.clef], ["Measures", meta.measureCount],
    ["Tempo", meta.tempo ? `${meta.tempo} bpm` : "not marked"], ["Part", (meta.parts || []).join(", ")],
  ].map(([l, v]) => card(l, e(v === undefined || v === null ? "-" : v))).join("");
}

// ---------------------------------------------------------------- AI note

async function writeAiNote() {
  if (!current || !current.details) return;
  const pid = $("providerSelect").value;
  const btn = $("aiNoteBtn"), out = $("aiNoteContent");
  btn.disabled = true;
  out.innerHTML = `<p class="small">Writing (one short call to ${escapeHtml(provider(pid) ? provider(pid).name : pid)})...</p>`;
  try {
    const res = await fetch("/api/about", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ details: current.details, provider: pid, model: currentModel() }),
    });
    const data = await res.json();
    if (!data.text) throw new Error(data.error || "No text came back.");
    out.innerHTML = markdown(data.text) + `<p class="small">Written by ${escapeHtml(data.model || "the AI")} from the facts above - check anything surprising.</p>`;
  } catch (err) {
    out.innerHTML = `<p class="warn">Could not write the note: ${escapeHtml(err.message)}</p>`;
  } finally {
    btn.disabled = false;
  }
}

/** Tiny Markdown subset (paragraphs, lists, headings, bold, italic, code); HTML is escaped first. */
function markdown(text) {
  const inline = s => escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const item = line.match(/^(?:[-*+]|\d+\.)\s+(.*)$/);
    if (item) {
      if (!list) { list = []; out.push(list); }
      list.push(`<li>${inline(item[1])}</li>`);
      continue;
    }
    list = null;
    if (!line) { out.push(""); continue; }
    const h = line.match(/^#{1,6}\s+(.*)$/);
    out.push(h ? `<h5>${inline(h[1])}</h5>` : `<p>${inline(line)}</p>`);
  }
  return out.map(x => Array.isArray(x) ? `<ul>${x.join("")}</ul>` : x).join("");
}

// ---------------------------------------------------------------- OSMD

function initOSMD() {
  if (!(window.opensheetmusicdisplay && window.opensheetmusicdisplay.OpenSheetMusicDisplay)) return;
  osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay("osmdCanvas", {
    autoResize: false, backend: "svg", drawTitle: true, drawComposer: true, drawCredits: true,
    drawPartNames: true, drawingParameters: "compacttight",
  });
}

function renderScore(xml) {
  if (!osmd) initOSMD();
  if (!osmd) return;
  osmd.load(xml)
    .then(() => { osmd.zoom = zoom; drawScore(); })
    .catch(err => console.error("OSMD render error:", err));
}

/** OSMD lays out against the container width, so a hidden Score tab is drawn when it is shown (selectTab). */
function drawScore() {
  if (!osmd || !current || !$("osmdCanvas").offsetWidth) return;
  osmd.render();
  if (osmd.cursor) osmd.cursor.hide();
}

function setZoom(z) {
  zoom = Math.max(0.4, Math.min(2.5, z));
  $("zoomLevelText").textContent = `${Math.round(zoom * 100)}%`;
  if (osmd) { osmd.zoom = zoom; drawScore(); }
}

// ---------------------------------------------------------------- playback

/**
 * Onsets of the notation staff only: staff 1 / voice 1 of the first part. The TAB staff (staff 2) repeats
 * every note after a <backup>, so the time cursor follows <backup>/<forward>, and <chord/> notes share the
 * previous note's start. parsedNotes = [{ start, beats, freqs: [] }] in quarter notes, sorted by start.
 */
function parseNotesForPlayback(xml) {
  parsedNotes = [];
  try {
    const doc = new DOMParser().parseFromString(xml, "text/xml");
    const part = doc.querySelector("part");
    if (!part) return;
    const onsets = new Map();
    let divisions = 1, measureStart = 0;
    for (const measure of part.children) {
      if (measure.tagName !== "measure") continue;
      let cursor = 0, lastStart = 0, measureLen = 0;
      for (const el of measure.children) {
        if (el.tagName === "attributes") {
          const d = parseInt(el.querySelector("divisions")?.textContent, 10);
          if (d > 0) divisions = d;
        } else if (el.tagName === "backup" || el.tagName === "forward") {
          const d = (parseInt(el.querySelector("duration")?.textContent, 10) || 0) / divisions;
          cursor += el.tagName === "backup" ? -d : d;
        } else if (el.tagName === "note") {
          if (el.querySelector("grace") || el.querySelector("cue")) continue;
          const beats = (parseInt(el.querySelector("duration")?.textContent, 10) || 0) / divisions;
          const isChord = el.querySelector("chord") !== null;
          const start = isChord ? lastStart : cursor;
          if (!isChord) { lastStart = cursor; cursor += beats; }
          measureLen = Math.max(measureLen, cursor);
          const staff = el.querySelector("staff")?.textContent.trim() || "1";
          const voice = el.querySelector("voice")?.textContent.trim() || "1";
          const pitch = el.querySelector("pitch");
          if (staff !== "1" || voice !== "1" || !pitch) continue;
          const t = measureStart + start;
          if (el.querySelector('tie[type="stop"]')) {
            const prev = parsedNotes[parsedNotes.length - 1];
            if (prev) prev.beats = Math.max(prev.beats, t + beats - prev.start);
            continue;
          }
          const freq = noteToFrequency(pitch.querySelector("step")?.textContent.trim() || "C",
            parseInt(pitch.querySelector("alter")?.textContent || "0", 10),
            parseInt(pitch.querySelector("octave")?.textContent || "4", 10));
          let onset = onsets.get(t);
          if (!onset) { onset = { start: t, beats, freqs: [] }; onsets.set(t, onset); parsedNotes.push(onset); }
          onset.beats = Math.max(onset.beats, beats);
          onset.freqs.push(freq);
        }
      }
      measureStart += measureLen;
    }
    parsedNotes.sort((a, b) => a.start - b.start);
  } catch (err) {
    console.warn("Could not parse notes for playback:", err);
  }
}

function noteToFrequency(step, alter, octave) {
  const semis = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  return 440 * Math.pow(2, (12 + octave * 12 + (semis[step] || 0) + (alter || 0) - 69) / 12);
}

function startPlayback() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();
  if (!parsedNotes.length) { showToast("No playable notes in this score."); return; }
  isPlaying = true;
  $("playBtn").classList.add("playing");
  $("playBtn").querySelector("span").textContent = "Pause";
  playNext();
}

function pausePlayback() {
  isPlaying = false;
  $("playBtn").classList.remove("playing");
  $("playBtn").querySelector("span").textContent = "Play";
  clearTimeout(playbackTimeout);
  playbackTimeout = null;
}

function stopPlayback() {
  pausePlayback();
  playbackIndex = 0;
}

function playNext() {
  if (!isPlaying || playbackIndex >= parsedNotes.length) { stopPlayback(); return; }
  const note = parsedNotes[playbackIndex], next = parsedNotes[playbackIndex + 1];
  const beatSec = 60 / (parseInt($("tempoSlider").value, 10) || 120);
  const soundSec = Math.max(0.08, note.beats * beatSec);
  note.freqs.forEach(f => tone(f, soundSec, $("synthSound").value));
  playbackIndex++;
  playbackTimeout = setTimeout(playNext, (next ? Math.max(0.02, (next.start - note.start) * beatSec) : soundSec) * 1000);
}

function tone(freq, dur, sound) {
  if (!audioCtx) return;
  const now = audioCtx.currentTime;
  const gain = audioCtx.createGain();
  gain.connect(audioCtx.destination);
  const oscs = [];
  const add = (type, f) => { const o = audioCtx.createOscillator(); o.type = type; o.frequency.setValueAtTime(f, now); o.connect(gain); oscs.push(o); };
  let end;
  if (sound === "piano") {
    add("triangle", freq); add("sine", freq * 2);
    end = now + Math.min(dur * 1.5, 1.8);
    gain.gain.setValueAtTime(0.001, now);
    gain.gain.linearRampToValueAtTime(0.28, now + 0.015);
  } else if (sound === "marimba") {
    add("sine", freq);
    end = now + Math.min(dur, 0.4);
    gain.gain.setValueAtTime(0.35, now);
  } else {                                     // plucked: bright attack, quick decay, a doubled course
    add("sawtooth", freq); add("triangle", freq * 1.003);
    end = now + Math.min(Math.max(dur, 0.25) * 1.2, 1.2);
    gain.gain.setValueAtTime(0.001, now);
    gain.gain.linearRampToValueAtTime(0.16, now + 0.005);
  }
  gain.gain.exponentialRampToValueAtTime(0.001, end);
  oscs.forEach(o => { o.start(now); o.stop(end + 0.05); });
}

// ---------------------------------------------------------------- utilities

function copyText(text, msg) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => showToast(msg), () => showToast("Copy failed - select the text and copy it by hand."));
}

function download(text, name, type) {
  if (!text) return;
  const url = URL.createObjectURL(new Blob([text], { type: type + ";charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  showToast(`Downloaded ${name}`);
}

async function saveToDisk() {
  if (!current) return;
  try {
    const res = await fetch("/api/save-file", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: currentFilename, musicxml: current.musicxml }),
    });
    const data = await res.json();
    if (!data.success) throw new Error(data.error);
    showToast(`Saved ${data.savedPath}`, 6000);
  } catch (err) {
    showToast("Save failed: " + err.message);
  }
}

let toastTimer = null;
function showToast(message, ms = 3500) {
  const t = $("toast");
  t.textContent = message;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
}

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
const escapeAttr = escapeHtml;
