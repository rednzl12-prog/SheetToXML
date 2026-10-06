/**
 * SheetXML frontend: file drop, background transcription jobs with live progress,
 * OpenSheetMusicDisplay rendering, ABC editing and Web Audio playback.
 */

// Global State
let currentFile = null;
let currentXml = "";
let currentFilename = "score.musicxml";
let currentMetadata = {};
let osmdInstance = null;
let currentZoom = 1.0;
let appConfig = {};
let currentJobId = null;
let pollTimer = null;

// Audio Synthesizer State
let audioCtx = null;
let isPlaying = false;
let playbackTimeout = null;
let parsedNotes = [];
let playbackIndex = 0;

const $ = (id) => document.getElementById(id);

// DOM Elements
const dropzone = $("dropzone");
const fileInput = $("fileInput");
const dropzoneIdle = $("dropzoneIdle");
const dropzonePreview = $("dropzonePreview");
const browseBtn = $("browseBtn");
const clearFileBtn = $("clearFileBtn");
const fileNameEl = $("fileName");
const fileSizeEl = $("fileSize");
const thumbnailWrapper = $("thumbnailWrapper");
const imageThumbnail = $("imageThumbnail");
const engineSelect = $("engineSelect");
const engineHint = $("engineHint");
const modelGroup = $("modelGroup");
const modelSelect = $("modelSelect");
const modelHint = $("modelHint");
const votesGroup = $("votesGroup");
const votesSelect = $("votesSelect");
const pageRangeInput = $("pageRange");
const mandolinTabToggle = $("mandolinTabToggle");
const skillLevelSelect = $("skillLevelSelect");
const transcribeBtn = $("transcribeBtn");
const progressSection = $("progressSection");
const progressTitle = $("progressTitle");
const progressSubtitle = $("progressSubtitle");
const progressBar = $("progressBar");
const progressLog = $("progressLog");
const cancelBtn = $("cancelBtn");
const resultSection = $("resultSection");
const keyStatusDot = $("keyStatusDot");
const keyStatusText = $("keyStatusText");
const settingsBtn = $("settingsBtn");
const settingsModal = $("settingsModal");
const closeSettingsBtn = $("closeSettingsBtn");
const geminiKeyInput = $("geminiKeyInput");
const qwenKeyInput = $("qwenKeyInput");
const toggleGeminiKeyVisibility = $("toggleGeminiKeyVisibility");
const toggleQwenKeyVisibility = $("toggleQwenKeyVisibility");
const testKeyBtn = $("testKeyBtn");
const saveKeyBtn = $("saveKeyBtn");
const verifyStatusBox = $("verifyStatusBox");
const loadSampleBtn = $("loadSampleBtn");

// Tab Buttons & Panels
const tabButtons = document.querySelectorAll(".tab-btn");
const tabContents = document.querySelectorAll(".tab-content");

// Score Display & Playback Elements
const playBtn = $("playBtn");
const stopBtn = $("stopBtn");
const tempoSlider = $("tempoSlider");
const tempoValue = $("tempoValue");
const synthSound = $("synthSound");
const zoomInBtn = $("zoomInBtn");
const zoomOutBtn = $("zoomOutBtn");
const zoomResetBtn = $("zoomResetBtn");
const zoomLevelText = $("zoomLevelText");

// Actions, XML and ABC
const copyXmlBtn = $("copyXmlBtn");
const downloadXmlBtn = $("downloadXmlBtn");
const saveLocalBtn = $("saveLocalBtn");
const xmlCodeContent = $("xmlCodeContent");
const xmlFilenameDisplay = $("xmlFilenameDisplay");
const xmlLinesCount = $("xmlLinesCount");
const abcEditor = $("abcEditor");
const applyAbcBtn = $("applyAbcBtn");

// Metadata Display
const scoreTitleDisplay = $("scoreTitleDisplay");
const scoreComposerBadge = $("scoreComposerBadge");
const scoreKeyBadge = $("scoreKeyBadge");
const scoreTimeBadge = $("scoreTimeBadge");
const scoreMeasuresBadge = $("scoreMeasuresBadge");
const engineBadge = $("engineBadge");
const schemaBadge = $("schemaBadge");
const resultNotices = $("resultNotices");
const reviewBarsText = $("reviewBarsText");
const warningsList = $("warningsList");
const metaTitle = $("metaTitle");
const metaComposer = $("metaComposer");
const metaKey = $("metaKey");
const metaTime = $("metaTime");
const metaClef = $("metaClef");
const metaMeasures = $("metaMeasures");
const metaTempo = $("metaTempo");
const metaParts = $("metaParts");
const mandolinAuditCard = $("mandolinAuditCard");
const mFirstPosRatio = $("mFirstPosRatio");
const mOpenCount = $("mOpenCount");
const mHighestFret = $("mHighestFret");
const mCourseDist = $("mCourseDist");
const validationStatusBox = $("validationStatusBox");
const validationMessage = $("validationMessage");
const validationErrorsList = $("validationErrorsList");
const toast = $("toast");

const PROVIDER_LABELS = { gemini: "Google Gemini", qwen: "Qwen Token Plan (Qwen + DeepSeek)", deepseek: "DeepSeek" };
const ENGINE_HINTS = {
  auto: "PDFs exported from notation software are read exactly (no AI). Scans and photos go to the AI model, or to Audiveris when no key is set.",
  vector: "Exact and free, for PDFs exported from MuseScore, Sibelius, Finale or Dorico. Does not work on scans or photos.",
  ai: "A vision model reads one staff system at a time; the code checks every bar. Uses API credits.",
  audiveris: "Free offline recognition for scans and photos (can take a few minutes).",
};

// =============================================================================
// Initialization
// =============================================================================

document.addEventListener("DOMContentLoaded", () => {
  checkApiConfig();
  setupEventListeners();
  updateEngineUi();
  initOSMD();
});

function checkApiConfig() {
  fetch("/api/config")
    .then(res => res.json())
    .then(data => {
      appConfig = data || {};
      const previous = modelSelect.value;
      modelSelect.innerHTML = "";
      const groups = {};
      (data.availableModels || []).forEach(m => {
        if (!groups[m.provider]) {
          groups[m.provider] = document.createElement("optgroup");
          groups[m.provider].label = PROVIDER_LABELS[m.provider] || m.provider;
          modelSelect.appendChild(groups[m.provider]);
        }
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.name;
        opt.title = m.description || "";
        opt.selected = m.id === (previous || data.defaultModel);
        groups[m.provider].appendChild(opt);
      });

      const audOpt = engineSelect.querySelector('option[value="audiveris"]');
      const hasAudiveris = Boolean(data.engines && data.engines.audiveris);
      audOpt.disabled = !hasAudiveris;
      audOpt.textContent = hasAudiveris ? "Audiveris offline" : "Audiveris offline (not installed)";
      if (!hasAudiveris && engineSelect.value === "audiveris") engineSelect.value = "auto";

      geminiKeyInput.placeholder = data.maskedGeminiKey ? `Configured (${data.maskedGeminiKey})` : "AIzaSy... or AQ....";
      qwenKeyInput.placeholder = data.maskedQwenKey ? `Configured (${data.maskedQwenKey})` : "sk-sp-... or sk-...";
      updateEngineUi();
    })
    .catch(() => keyStatusDot.classList.remove("active"));
}

function selectedModelInfo() {
  return (appConfig.availableModels || []).find(m => m.id === modelSelect.value) || null;
}

function selectedProvider() {
  const m = selectedModelInfo();
  return m ? m.provider : "gemini";
}

function providerHasKey(provider) {
  return provider === "gemini" ? Boolean(appConfig.hasGeminiKey) : Boolean(appConfig.hasQwenKey);
}

function updateEngineUi() {
  const engine = engineSelect.value;
  const usesAi = engine === "auto" || engine === "ai";
  modelGroup.classList.toggle("hidden", !usesAi);
  votesGroup.classList.toggle("hidden", !usesAi);
  let hint = ENGINE_HINTS[engine] || "";
  if (engine === "audiveris" && !(appConfig.engines && appConfig.engines.audiveris)) {
    hint = "Not installed. Get Audiveris 5.11 (free) from github.com/Audiveris/audiveris/releases.";
  }
  engineHint.textContent = hint;
  const m = selectedModelInfo();
  modelHint.textContent = m ? m.description : "";
  updateKeyUi();
}

function updateKeyUi() {
  const provider = selectedProvider();
  const hasKey = providerHasKey(provider);
  const label = provider === "gemini" ? "Gemini" : "Qwen";
  const usesAi = engineSelect.value === "auto" || engineSelect.value === "ai";
  keyStatusDot.classList.toggle("active", hasKey || !usesAi);
  keyStatusText.textContent = !usesAi ? "AI Settings" : hasKey ? `${label} Key Set` : `${label} Key Needed`;
}

function initOSMD() {
  if (window.opensheetmusicdisplay && window.opensheetmusicdisplay.OpenSheetMusicDisplay) {
    osmdInstance = new opensheetmusicdisplay.OpenSheetMusicDisplay("osmdCanvas", {
      autoResize: true,
      backend: "svg",
      drawTitle: true,
      drawComposer: true,
      drawCredits: true,
      drawPartNames: true,
      drawingParameters: "compacttight"
    });
  }
}

// =============================================================================
// Event Listeners
// =============================================================================

function toggleVisibility(input, button) {
  const isPass = input.type === "password";
  input.type = isPass ? "text" : "password";
  button.textContent = isPass ? "Hide" : "Show";
}

function setupEventListeners() {
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files.length > 0) handleFileSelected(e.dataTransfer.files[0]);
  });

  browseBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) handleFileSelected(e.target.files[0]);
  });
  clearFileBtn.addEventListener("click", resetFileInput);

  transcribeBtn.addEventListener("click", startTranscription);
  cancelBtn.addEventListener("click", cancelTranscription);
  engineSelect.addEventListener("change", updateEngineUi);
  modelSelect.addEventListener("change", updateEngineUi);
  loadSampleBtn.addEventListener("click", loadSampleScore);
  applyAbcBtn.addEventListener("click", applyAbcEdits);

  settingsBtn.addEventListener("click", () => {
    verifyStatusBox.classList.add("hidden");
    settingsModal.classList.remove("hidden");
  });
  closeSettingsBtn.addEventListener("click", () => settingsModal.classList.add("hidden"));
  settingsModal.addEventListener("click", (e) => {
    if (e.target === settingsModal) settingsModal.classList.add("hidden");
  });
  toggleGeminiKeyVisibility.addEventListener("click", () => toggleVisibility(geminiKeyInput, toggleGeminiKeyVisibility));
  toggleQwenKeyVisibility.addEventListener("click", () => toggleVisibility(qwenKeyInput, toggleQwenKeyVisibility));
  testKeyBtn.addEventListener("click", testConnection);
  saveKeyBtn.addEventListener("click", saveApiKey);

  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      tabButtons.forEach(b => b.classList.remove("active"));
      tabContents.forEach(c => c.classList.remove("active"));
      btn.classList.add("active");
      const targetId = btn.getAttribute("data-tab");
      $(targetId).classList.add("active");
      if (targetId === "scoreTab" && osmdInstance && currentXml) {
        setTimeout(() => osmdInstance.render(), 50);
      }
    });
  });

  playBtn.addEventListener("click", togglePlayback);
  stopBtn.addEventListener("click", stopPlayback);
  tempoSlider.addEventListener("input", (e) => {
    tempoValue.textContent = e.target.value;
  });

  zoomInBtn.addEventListener("click", () => updateZoom(0.15));
  zoomOutBtn.addEventListener("click", () => updateZoom(-0.15));
  zoomResetBtn.addEventListener("click", () => resetZoom());

  copyXmlBtn.addEventListener("click", copyXmlToClipboard);
  downloadXmlBtn.addEventListener("click", downloadXmlFile);
  saveLocalBtn.addEventListener("click", saveXmlToLocalDisk);
}

// =============================================================================
// File Handling
// =============================================================================

const VALID_EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
  ".musicxml", ".xml", ".mxl", ".abc"];

function handleFileSelected(file) {
  const ext = "." + file.name.split(".").pop().toLowerCase();
  if (!VALID_EXTENSIONS.includes(ext)) {
    showToast("Please choose a PDF, an image, or a MusicXML / ABC file.");
    return;
  }

  currentFile = file;
  fileNameEl.textContent = file.name;
  fileSizeEl.textContent = formatBytes(file.size);
  dropzoneIdle.classList.add("hidden");
  dropzonePreview.classList.remove("hidden");
  transcribeBtn.removeAttribute("disabled");

  if (file.type.startsWith("image/")) {
    const reader = new FileReader();
    reader.onload = (e) => {
      imageThumbnail.src = e.target.result;
      thumbnailWrapper.classList.remove("hidden");
    };
    reader.readAsDataURL(file);
  } else {
    thumbnailWrapper.classList.add("hidden");
  }
}

function resetFileInput() {
  currentFile = null;
  fileInput.value = "";
  dropzonePreview.classList.add("hidden");
  dropzoneIdle.classList.remove("hidden");
  thumbnailWrapper.classList.add("hidden");
  transcribeBtn.setAttribute("disabled", "true");
}

function formatBytes(bytes) {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

// =============================================================================
// API Keys
// =============================================================================

async function testConnection() {
  const typed = { gemini: geminiKeyInput.value.trim(), qwen: qwenKeyInput.value.trim() };
  const providers = ["gemini", "qwen"].filter(p => typed[p] || providerHasKey(p));
  verifyStatusBox.classList.remove("hidden", "success", "error");
  if (providers.length === 0) {
    verifyStatusBox.className = "verify-status error";
    verifyStatusBox.textContent = "Enter a Gemini or Qwen key first.";
    return;
  }
  verifyStatusBox.className = "verify-status";
  verifyStatusBox.textContent = "Checking keys (no tokens are used)...";
  const lines = [];
  let allOk = true;
  for (const provider of providers) {
    try {
      const res = await fetch("/api/verify-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, apiKey: typed[provider] })
      });
      const data = await res.json();
      allOk = allOk && Boolean(data.success);
      lines.push(data.success ? `✓ ${data.message}` : `✗ ${data.error}`);
    } catch (err) {
      allOk = false;
      lines.push(`✗ ${provider}: network error ${err.message}`);
    }
  }
  verifyStatusBox.className = "verify-status " + (allOk ? "success" : "error");
  verifyStatusBox.textContent = lines.join("\n");
  verifyStatusBox.style.whiteSpace = "pre-line";
}

function saveApiKey() {
  const geminiKey = geminiKeyInput.value.trim();
  const qwenKey = qwenKeyInput.value.trim();
  if (!geminiKey && !qwenKey) {
    showToast("Please enter at least one API key to save.");
    return;
  }
  fetch("/api/save-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ geminiKey, qwenKey })
  })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        showToast(data.message || "API key(s) saved.");
        geminiKeyInput.value = "";
        qwenKeyInput.value = "";
        settingsModal.classList.add("hidden");
        checkApiConfig();
      } else {
        showToast("Error saving key: " + data.error);
      }
    })
    .catch(err => showToast("Error saving key: " + err.message));
}

// =============================================================================
// Transcription jobs
// =============================================================================

function setBusy(busy) {
  progressSection.classList.toggle("hidden", !busy);
  if (busy) {
    transcribeBtn.setAttribute("disabled", "true");
  } else if (currentFile) {
    transcribeBtn.removeAttribute("disabled");
  }
  cancelBtn.disabled = false;
  cancelBtn.textContent = "Cancel";
}

function startTranscription() {
  if (!currentFile) {
    showToast("Please choose or drop a sheet music file first.");
    return;
  }
  const formData = new FormData();
  formData.append("file", currentFile);
  formData.append("engine", engineSelect.value);
  formData.append("model", modelSelect.value);
  formData.append("votes", votesSelect.value);
  formData.append("mandolinTab", mandolinTabToggle.checked ? "true" : "false");
  formData.append("skillLevel", skillLevelSelect.value);
  formData.append("pageRange", pageRangeInput.value.trim());

  resultSection.classList.add("hidden");
  progressTitle.textContent = `Transcribing ${currentFile.name}`;
  progressSubtitle.textContent = "Uploading...";
  progressBar.style.width = "0%";
  progressLog.innerHTML = "";
  setBusy(true);

  fetch("/api/transcribe", { method: "POST", body: formData })
    .then(res => res.json())
    .then(data => {
      if (!data.jobId) throw new Error(data.error || "The server did not start a job.");
      currentJobId = data.jobId;
      pollJob();
    })
    .catch(err => {
      setBusy(false);
      showToast("Transcription failed: " + err.message, 9000);
    });
}

function pollJob() {
  const jobId = currentJobId;
  fetch(`/api/job?id=${encodeURIComponent(jobId)}`)
    .then(res => res.json())
    .then(job => {
      if (jobId !== currentJobId) return;
      if (job.error && !job.status) throw new Error(job.error);
      progressBar.style.width = `${Math.round((job.progress || 0) * 100)}%`;
      const log = job.log || [];
      if (log.length) progressSubtitle.textContent = log[log.length - 1];
      progressLog.innerHTML = log.slice(-8).map(line => `<li>${escapeHtml(line)}</li>`).join("");
      progressLog.scrollTop = progressLog.scrollHeight;

      if (job.status === "running") {
        pollTimer = setTimeout(pollJob, 1000);
        return;
      }
      currentJobId = null;
      setBusy(false);
      if (job.status === "done") {
        displayTranscriptionResult(job.result);
      } else if (job.status === "cancelled") {
        showToast("Transcription cancelled.");
      } else {
        showToast(job.error || "Transcription failed.", 12000);
        if ((job.error || "").includes("Settings")) settingsModal.classList.remove("hidden");
      }
    })
    .catch(err => {
      if (jobId !== currentJobId) return;
      // the server may be busy; keep polling a little slower
      progressSubtitle.textContent = "Waiting for the server... " + err.message;
      pollTimer = setTimeout(pollJob, 2500);
    });
}

function cancelTranscription() {
  if (!currentJobId) {
    setBusy(false);
    return;
  }
  cancelBtn.disabled = true;
  cancelBtn.textContent = "Cancelling...";
  fetch("/api/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jobId: currentJobId })
  }).catch(err => showToast("Cancel failed: " + err.message));
}

function applyAbcEdits() {
  const abc = abcEditor.value.trim();
  if (!abc) {
    showToast("The ABC text is empty.");
    return;
  }
  applyAbcBtn.disabled = true;
  fetch("/api/render", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      abc,
      mandolinTab: mandolinTabToggle.checked,
      skillLevel: skillLevelSelect.value,
      filename: currentFilename
    })
  })
    .then(res => res.json())
    .then(data => {
      if (!data.musicxml) throw new Error(data.error || "Could not render the ABC.");
      displayTranscriptionResult(data, { keepTab: true });
      showToast("Edits applied.");
    })
    .catch(err => showToast("Apply failed: " + err.message, 8000))
    .finally(() => { applyAbcBtn.disabled = false; });
}

function loadSampleScore() {
  fetch("/api/sample")
    .then(res => res.json())
    .then(data => {
      if (!data.musicxml) throw new Error(data.error || "no sample");
      displayTranscriptionResult(data);
      showToast("Loaded sample score: " + (data.filename || "music-xml-example.xml"));
    })
    .catch(err => showToast("Could not load sample: " + err.message));
}

// =============================================================================
// Results Presentation & OSMD Rendering
// =============================================================================

function engineLabel(data) {
  switch (data.engine) {
    case "vector": return "Engine: exact PDF reader (no AI)";
    case "audiveris": return "Engine: Audiveris (offline)";
    case "import": return "Engine: imported file";
    case "abc": return "Engine: edited ABC";
    case "ai": {
      const used = data.modelUsed || data.requestedModel || "?";
      return data.requestedModel && used !== data.requestedModel
        ? `AI: ${used} (fallback from ${data.requestedModel})` : `AI: ${used}`;
    }
    default: return "Engine: —";
  }
}

function displayTranscriptionResult(data, opts = {}) {
  stopPlayback();
  currentXml = data.musicxml;
  currentFilename = (data.filename || "score").replace(/\.[^/.]+$/, "") + ".musicxml";
  currentMetadata = data.metadata || {};

  scoreTitleDisplay.textContent = currentMetadata.title || "Transcribed Score";
  scoreComposerBadge.textContent = currentMetadata.composer || "Composer unknown";
  scoreKeyBadge.textContent = currentMetadata.keySignature || "Key";
  scoreTimeBadge.textContent = currentMetadata.timeSignature || "4/4";
  scoreMeasuresBadge.textContent = `${currentMetadata.measureCount || 0} Measures`;
  engineBadge.textContent = engineLabel(data);

  if (data.isValid) {
    schemaBadge.className = "badge badge-success";
    schemaBadge.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg> MusicXML 4.0 Validated`;
  } else {
    schemaBadge.className = "badge badge-meta";
    schemaBadge.textContent = `MusicXML 4.0 Notice (${(data.validationErrors || []).length})`;
  }

  // Bars to check and engine warnings, shown above the tabs
  const review = data.reviewBars || [];
  const warnings = data.warnings || [];
  reviewBarsText.textContent = review.length
    ? `Check these measures (marked "check" in the score): ${review.join(", ")}`
    : (warnings.length ? "Notes from the engine:" : "");
  warningsList.innerHTML = warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("");
  resultNotices.classList.toggle("hidden", !review.length && !warnings.length);

  xmlFilenameDisplay.textContent = currentFilename;
  xmlLinesCount.textContent = `${currentXml.split("\n").length} lines`;
  xmlCodeContent.textContent = currentXml;
  abcEditor.value = data.abc || "";

  metaTitle.textContent = currentMetadata.title || "—";
  metaComposer.textContent = currentMetadata.composer || "—";
  metaKey.textContent = currentMetadata.keySignature || "—";
  metaTime.textContent = currentMetadata.timeSignature || "—";
  metaClef.textContent = currentMetadata.clef ? currentMetadata.clef + " Clef" : "—";
  metaMeasures.textContent = currentMetadata.measureCount || "—";
  metaTempo.textContent = currentMetadata.tempo ? `${currentMetadata.tempo} BPM` : "not marked";
  metaParts.textContent = (currentMetadata.parts && currentMetadata.parts.length) ? currentMetadata.parts.join(", ") : "Part 1";

  if (data.mandolinStats) {
    mandolinAuditCard.classList.remove("hidden");
    const ms = data.mandolinStats;
    const firstPosPercent = ms.totalNotes > 0 ? Math.round((ms.firstPositionCount / ms.totalNotes) * 100) : 100;
    mFirstPosRatio.textContent = `${firstPosPercent}% (${ms.firstPositionCount}/${ms.totalNotes})`;
    mOpenCount.textContent = `${ms.openStringsCount} notes`;
    mHighestFret.textContent = `Fret ${ms.highestFret}`;
    const su = ms.stringUsage || {};
    mCourseDist.textContent = `E: ${su[1] || 0} | A: ${su[2] || 0} | D: ${su[3] || 0} | G: ${su[4] || 0}` +
      (ms.transposedOctaves ? ` | transposed ${ms.transposedOctaves > 0 ? "up" : "down"} ${Math.abs(ms.transposedOctaves)} octave(s) to fit the mandolin` : "");
  } else {
    mandolinAuditCard.classList.add("hidden");
  }

  if (data.isValid) {
    validationStatusBox.className = "validation-status-box valid";
    validationStatusBox.querySelector(".status-icon").textContent = "✓";
    validationStatusBox.querySelector("strong").textContent = "Strict MusicXML 4.0 Schema Conformance";
    validationMessage.textContent = "All staves, measures, notes and attributes conform to the official MusicXML 4.0 schema.";
    validationErrorsList.innerHTML = "";
  } else {
    validationStatusBox.className = "validation-status-box invalid";
    validationStatusBox.querySelector(".status-icon").textContent = "!";
    validationStatusBox.querySelector("strong").textContent = "Validation Observations";
    validationMessage.textContent = "The file is functional XML with the following schema notes:";
    validationErrorsList.innerHTML = (data.validationErrors || []).map(e => `<li>${escapeHtml(e)}</li>`).join("");
  }

  const bpm = parseInt(currentMetadata.tempo, 10);
  if (!isNaN(bpm) && bpm >= 50 && bpm <= 220) {
    tempoSlider.value = bpm;
    tempoValue.textContent = bpm;
  }

  parseNotesForPlayback(currentXml);
  resultSection.classList.remove("hidden");
  renderScore(currentXml);
  if (!opts.keepTab) resultSection.scrollIntoView({ behavior: "smooth" });
}

function renderScore(xmlString) {
  if (!osmdInstance) initOSMD();
  if (!osmdInstance) return;
  try {
    osmdInstance.load(xmlString)
      .then(() => {
        osmdInstance.zoom = currentZoom;
        osmdInstance.render();
        if (osmdInstance.cursor) osmdInstance.cursor.hide();
      })
      .catch(err => console.error("OSMD render error:", err));
  } catch (e) {
    console.error("OSMD invocation error:", e);
  }
}

function updateZoom(delta) {
  currentZoom = Math.max(0.4, Math.min(2.5, currentZoom + delta));
  zoomLevelText.textContent = `${Math.round(currentZoom * 100)}%`;
  if (osmdInstance) {
    osmdInstance.zoom = currentZoom;
    osmdInstance.render();
  }
}

function resetZoom() {
  currentZoom = 1.0;
  zoomLevelText.textContent = "100%";
  if (osmdInstance) {
    osmdInstance.zoom = currentZoom;
    osmdInstance.render();
  }
}

// =============================================================================
// Audio Playback
// =============================================================================

/**
 * Onsets of the notation staff only: staff 1 / voice 1 of the first part. The TAB staff
 * (staff 2) repeats every note after a <backup>, so we follow the time cursor through
 * <backup>/<forward>, and <chord/> notes share the previous note's start.
 * parsedNotes = [{ start, beats, freqs: [] }] in quarter notes, sorted by start.
 */
function parseNotesForPlayback(xml) {
  parsedNotes = [];
  try {
    const doc = new DOMParser().parseFromString(xml, "text/xml");
    const part = doc.querySelector("part");
    if (!part) return;
    const onsets = new Map();
    let divisions = 1;
    let measureStart = 0;
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
          if (!isChord) {
            lastStart = cursor;
            cursor += beats;
          }
          measureLen = Math.max(measureLen, cursor);
          const staff = el.querySelector("staff")?.textContent.trim() || "1";
          const voice = el.querySelector("voice")?.textContent.trim() || "1";
          const pitch = el.querySelector("pitch");
          if (staff !== "1" || voice !== "1" || !pitch) continue;
          const tieStop = el.querySelector('tie[type="stop"]') !== null;
          const t = measureStart + start;
          if (tieStop) {
            // extend the tied note instead of re-striking it
            const prev = parsedNotes.length ? parsedNotes[parsedNotes.length - 1] : null;
            if (prev) prev.beats = Math.max(prev.beats, t + beats - prev.start);
            continue;
          }
          const freq = noteToFrequency(
            pitch.querySelector("step")?.textContent.trim() || "C",
            parseInt(pitch.querySelector("alter")?.textContent || "0", 10),
            parseInt(pitch.querySelector("octave")?.textContent || "4", 10));
          let onset = onsets.get(t);
          if (!onset) {
            onset = { start: t, beats, freqs: [] };
            onsets.set(t, onset);
            parsedNotes.push(onset);
          }
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
  const semitones = { "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11 };
  const midiNote = 12 + (octave * 12) + (semitones[step] || 0) + (alter || 0);
  return 440 * Math.pow(2, (midiNote - 69) / 12);
}

function togglePlayback() {
  if (isPlaying) {
    pausePlayback();
  } else {
    startAudioPlayback();
  }
}

function startAudioPlayback() {
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    audioCtx = new AudioContextClass();
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
  if (parsedNotes.length === 0) {
    showToast("No playable notes detected in score.");
    return;
  }
  isPlaying = true;
  playBtn.classList.add("playing");
  playBtn.querySelector("span").textContent = "Pause";
  playNextNote();
}

function pausePlayback() {
  isPlaying = false;
  playBtn.classList.remove("playing");
  playBtn.querySelector("span").textContent = "Play";
  if (playbackTimeout) {
    clearTimeout(playbackTimeout);
    playbackTimeout = null;
  }
}

function stopPlayback() {
  pausePlayback();
  playbackIndex = 0;
}

function playNextNote() {
  if (!isPlaying || playbackIndex >= parsedNotes.length) {
    stopPlayback();
    return;
  }
  const note = parsedNotes[playbackIndex];
  const next = parsedNotes[playbackIndex + 1];
  const beatSec = 60 / (parseInt(tempoSlider.value, 10) || 120);
  const soundSec = Math.max(0.08, note.beats * beatSec);
  note.freqs.forEach(f => playSynthesizedTone(f, soundSec, synthSound.value));
  const waitSec = next ? Math.max(0.02, (next.start - note.start) * beatSec) : soundSec;
  playbackIndex++;
  playbackTimeout = setTimeout(playNextNote, waitSec * 1000);
}

function playSynthesizedTone(frequency, duration, instrument) {
  if (!audioCtx) return;

  const now = audioCtx.currentTime;
  const osc1 = audioCtx.createOscillator();
  const osc2 = audioCtx.createOscillator();
  const gainNode = audioCtx.createGain();

  if (instrument === "piano") {
    // Warm Acoustic Piano synthesis (fundamental + harmonic)
    osc1.type = "triangle";
    osc2.type = "sine";
    osc1.frequency.setValueAtTime(frequency, now);
    osc2.frequency.setValueAtTime(frequency * 2, now);

    gainNode.gain.setValueAtTime(0.001, now);
    gainNode.gain.linearRampToValueAtTime(0.28, now + 0.015);
    gainNode.gain.exponentialRampToValueAtTime(0.001, now + Math.min(duration * 1.5, 1.8));

    osc1.connect(gainNode);
    osc2.connect(gainNode);
    gainNode.connect(audioCtx.destination);

    osc1.start(now);
    osc2.start(now);
    osc1.stop(now + 1.8);
    osc2.stop(now + 1.8);

  } else if (instrument === "synth") {
    // Ambient Bell / Chime
    osc1.type = "sine";
    osc2.type = "sine";
    osc1.frequency.setValueAtTime(frequency, now);
    osc2.frequency.setValueAtTime(frequency * 3.01, now);

    gainNode.gain.setValueAtTime(0.001, now);
    gainNode.gain.linearRampToValueAtTime(0.22, now + 0.02);
    gainNode.gain.exponentialRampToValueAtTime(0.001, now + duration * 1.2);

    osc1.connect(gainNode);
    osc2.connect(gainNode);
    gainNode.connect(audioCtx.destination);

    osc1.start(now);
    osc2.start(now);
    osc1.stop(now + duration * 1.2);
    osc2.stop(now + duration * 1.2);

  } else {
    // Wood Marimba
    osc1.type = "sine";
    osc1.frequency.setValueAtTime(frequency, now);

    gainNode.gain.setValueAtTime(0.35, now);
    gainNode.gain.exponentialRampToValueAtTime(0.001, now + Math.min(duration, 0.4));

    osc1.connect(gainNode);
    gainNode.connect(audioCtx.destination);

    osc1.start(now);
    osc1.stop(now + 0.4);
  }
}

// =============================================================================
// Export & Utilities
// =============================================================================

function copyXmlToClipboard() {
  if (!currentXml) return;
  navigator.clipboard.writeText(currentXml).then(() => {
    const originalText = copyXmlBtn.querySelector("span").textContent;
    copyXmlBtn.querySelector("span").textContent = "Copied!";
    showToast("MusicXML copied to clipboard.");
    setTimeout(() => {
      copyXmlBtn.querySelector("span").textContent = originalText;
    }, 2000);
  });
}

function downloadXmlFile() {
  if (!currentXml) return;
  const blob = new Blob([currentXml], { type: "application/vnd.recordare.musicxml+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = currentFilename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast(`Downloaded ${currentFilename}`);
}

function saveXmlToLocalDisk() {
  if (!currentXml) return;
  fetch("/api/save-file", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename: currentFilename,
      musicxml: currentXml
    })
  })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        showToast(`Saved to project: ${data.filename}`);
      } else {
        showToast("Error saving to disk: " + data.error);
      }
    })
    .catch(err => {
      showToast("Save failed: " + err.message);
    });
}

let toastTimer = null;
function showToast(message, ms = 3500) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), ms);
}

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
