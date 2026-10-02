/**
 * SheetXML Frontend Application
 * Handles file drop, Gemini API communication, OpenSheetMusicDisplay rendering,
 * and Web Audio playback synthesis.
 */

// Global State
let currentFile = null;
let currentXml = "";
let currentFilename = "score.musicxml";
let currentMetadata = {};
let osmdInstance = null;
let currentZoom = 1.0;

// Audio Synthesizer State
let audioCtx = null;
let isPlaying = false;
let playbackTimeout = null;
let parsedNotes = [];
let playbackIndex = 0;

// DOM Elements
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const dropzoneIdle = document.getElementById("dropzoneIdle");
const dropzonePreview = document.getElementById("dropzonePreview");
const browseBtn = document.getElementById("browseBtn");
const clearFileBtn = document.getElementById("clearFileBtn");
const fileNameEl = document.getElementById("fileName");
const fileSizeEl = document.getElementById("fileSize");
const thumbnailWrapper = document.getElementById("thumbnailWrapper");
const imageThumbnail = document.getElementById("imageThumbnail");
const modelSelect = document.getElementById("modelSelect");
const pageRangeInput = document.getElementById("pageRange");
const mandolinTabToggle = document.getElementById("mandolinTabToggle");
const skillLevelSelect = document.getElementById("skillLevelSelect");
const transcribeBtn = document.getElementById("transcribeBtn");
const progressSection = document.getElementById("progressSection");
const progressBar = document.getElementById("progressBar");
const resultSection = document.getElementById("resultSection");
const keyStatusDot = document.getElementById("keyStatusDot");
const keyStatusText = document.getElementById("keyStatusText");
const settingsBtn = document.getElementById("settingsBtn");
const settingsModal = document.getElementById("settingsModal");
const closeSettingsBtn = document.getElementById("closeSettingsBtn");
const apiKeyInput = document.getElementById("apiKeyInput");
const toggleKeyVisibility = document.getElementById("toggleKeyVisibility");
const testKeyBtn = document.getElementById("testKeyBtn");
const saveKeyBtn = document.getElementById("saveKeyBtn");
const verifyStatusBox = document.getElementById("verifyStatusBox");
const loadSampleBtn = document.getElementById("loadSampleBtn");

// Tab Buttons & Panels
const tabButtons = document.querySelectorAll(".tab-btn");
const tabContents = document.querySelectorAll(".tab-content");

// Score Display & Playback Elements
const playBtn = document.getElementById("playBtn");
const stopBtn = document.getElementById("stopBtn");
const tempoSlider = document.getElementById("tempoSlider");
const tempoValue = document.getElementById("tempoValue");
const synthSound = document.getElementById("synthSound");
const zoomInBtn = document.getElementById("zoomInBtn");
const zoomOutBtn = document.getElementById("zoomOutBtn");
const zoomResetBtn = document.getElementById("zoomResetBtn");
const zoomLevelText = document.getElementById("zoomLevelText");

// Actions & XML Inspection
const copyXmlBtn = document.getElementById("copyXmlBtn");
const downloadXmlBtn = document.getElementById("downloadXmlBtn");
const saveLocalBtn = document.getElementById("saveLocalBtn");
const xmlCodeContent = document.getElementById("xmlCodeContent");
const xmlFilenameDisplay = document.getElementById("xmlFilenameDisplay");
const xmlLinesCount = document.getElementById("xmlLinesCount");

// Metadata Display
const scoreTitleDisplay = document.getElementById("scoreTitleDisplay");
const scoreComposerBadge = document.getElementById("scoreComposerBadge");
const scoreKeyBadge = document.getElementById("scoreKeyBadge");
const scoreTimeBadge = document.getElementById("scoreTimeBadge");
const scoreMeasuresBadge = document.getElementById("scoreMeasuresBadge");
const modelUsedBadge = document.getElementById("modelUsedBadge");
const schemaBadge = document.getElementById("schemaBadge");
const metaTitle = document.getElementById("metaTitle");
const metaComposer = document.getElementById("metaComposer");
const metaKey = document.getElementById("metaKey");
const metaTime = document.getElementById("metaTime");
const metaClef = document.getElementById("metaClef");
const metaMeasures = document.getElementById("metaMeasures");
const metaTempo = document.getElementById("metaTempo");
const metaParts = document.getElementById("metaParts");
const mandolinAuditCard = document.getElementById("mandolinAuditCard");
const mFirstPosRatio = document.getElementById("mFirstPosRatio");
const mOpenCount = document.getElementById("mOpenCount");
const mHighestFret = document.getElementById("mHighestFret");
const mCourseDist = document.getElementById("mCourseDist");
const validationStatusBox = document.getElementById("validationStatusBox");
const validationMessage = document.getElementById("validationMessage");
const validationErrorsList = document.getElementById("validationErrorsList");
const toast = document.getElementById("toast");

// =============================================================================
// Initialization
// =============================================================================

document.addEventListener("DOMContentLoaded", () => {
  checkApiConfig();
  setupEventListeners();
  initOSMD();
});

function checkApiConfig() {
  fetch("/api/config")
    .then(res => res.json())
    .then(data => {
      if (data.availableModels && data.availableModels.length > 0) {
        const modelSelect = document.getElementById("modelSelect");
        if (modelSelect) {
          const currentVal = modelSelect.value;
          modelSelect.innerHTML = "";
          data.availableModels.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m.id;
            opt.textContent = `${m.name}`;
            if (m.id === (currentVal || data.defaultModel)) {
              opt.selected = true;
            }
            modelSelect.appendChild(opt);
          });
        }
      }
      if (data.hasKey) {
        keyStatusDot.classList.add("active");
        keyStatusText.textContent = "Gemini Connected";
        if (data.maskedKey) {
          apiKeyInput.placeholder = `Configured (${data.maskedKey})`;
        }
      } else {
        keyStatusDot.classList.remove("active");
        keyStatusText.textContent = "API Key Needed";
      }
      updateKeyUi();
      const hasSelectedKey = selectedProvider() === "qwen" ? data.hasQwenKey : selectedProvider() === "deepseek" ? data.hasDeepSeekKey : data.hasKey;
      keyStatusDot.classList.toggle("active", Boolean(hasSelectedKey));
      keyStatusText.textContent = hasSelectedKey ? `${selectedProvider()} Connected` : "API Key Needed";
    })
    .catch(() => {
      keyStatusDot.classList.remove("active");
    });
}

function selectedProvider() {
  return modelSelect.value.startsWith("qwen") ? "qwen" : modelSelect.value.startsWith("deepseek") ? "deepseek" : "gemini";
}

function updateKeyUi() {
  const provider = selectedProvider();
  const name = provider === "qwen" ? "QwenCloud" : provider === "deepseek" ? "DeepSeek" : "Gemini";
  document.querySelector('label[for="apiKeyInput"]').textContent = `${name} API Key`;
  apiKeyInput.placeholder = provider === "gemini" ? "AIzaSy..." : `${name} API key`;
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

function setupEventListeners() {
  // Drag & Drop
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files.length > 0) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  });

  browseBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) {
      handleFileSelected(e.target.files[0]);
    }
  });

  clearFileBtn.addEventListener("click", resetFileInput);

  // Transcribe Action
  transcribeBtn.addEventListener("click", startTranscription);
  modelSelect.addEventListener("change", updateKeyUi);

  // Sample Score Loader
  loadSampleBtn.addEventListener("click", loadSampleScore);

  // Settings Modal
  settingsBtn.addEventListener("click", () => {
    verifyStatusBox.classList.add("hidden");
    settingsModal.classList.remove("hidden");
  });
  closeSettingsBtn.addEventListener("click", () => settingsModal.classList.add("hidden"));
  settingsModal.addEventListener("click", (e) => {
    if (e.target === settingsModal) settingsModal.classList.add("hidden");
  });

  toggleKeyVisibility.addEventListener("click", () => {
    if (apiKeyInput.type === "password") {
      apiKeyInput.type = "text";
      toggleKeyVisibility.textContent = "Hide";
    } else {
      apiKeyInput.type = "password";
      toggleKeyVisibility.textContent = "Show";
    }
  });

  testKeyBtn.addEventListener("click", testConnection);
  saveKeyBtn.addEventListener("click", saveApiKey);

  // Tab Navigation
  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      tabButtons.forEach(b => b.classList.remove("active"));
      tabContents.forEach(c => c.classList.remove("active"));
      btn.classList.add("active");
      const targetId = btn.getAttribute("data-tab");
      document.getElementById(targetId).classList.add("active");
      if (targetId === "scoreTab" && osmdInstance && currentXml) {
        setTimeout(() => osmdInstance.render(), 50);
      }
    });
  });

  // Playback & Zoom
  playBtn.addEventListener("click", togglePlayback);
  stopBtn.addEventListener("click", stopPlayback);
  tempoSlider.addEventListener("input", (e) => {
    tempoValue.textContent = e.target.value;
  });

  zoomInBtn.addEventListener("click", () => updateZoom(0.15));
  zoomOutBtn.addEventListener("click", () => updateZoom(-0.15));
  zoomResetBtn.addEventListener("click", () => resetZoom());

  // Export Buttons
  copyXmlBtn.addEventListener("click", copyXmlToClipboard);
  downloadXmlBtn.addEventListener("click", downloadXmlFile);
  saveLocalBtn.addEventListener("click", saveXmlToLocalDisk);
}

// =============================================================================
// File Handling
// =============================================================================

function handleFileSelected(file) {
  const validExtensions = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"];
  const ext = "." + file.name.split(".").pop().toLowerCase();
  if (!validExtensions.includes(ext)) {
    showToast("Please select a PDF or image file (PNG, JPG, WEBP, TIFF).");
    return;
  }

  currentFile = file;
  fileNameEl.textContent = file.name;
  fileSizeEl.textContent = formatBytes(file.size);

  dropzoneIdle.classList.add("hidden");
  dropzonePreview.classList.remove("hidden");
  transcribeBtn.removeAttribute("disabled");

  // Show thumbnail if image
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
// Gemini Settings & Connection Testing
// =============================================================================

function testConnection() {
  const key = apiKeyInput.value.trim();
  verifyStatusBox.classList.remove("hidden", "success", "error");
  verifyStatusBox.textContent = `Connecting to ${selectedProvider()}...`;

  fetch("/api/verify-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey: key, provider: selectedProvider() })
  })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        verifyStatusBox.className = "verify-status success";
        verifyStatusBox.textContent = `✓ Connected! ${selectedProvider()} API access verified.`;
        keyStatusDot.classList.add("active");
        keyStatusText.textContent = `${selectedProvider()} Connected`;
      } else {
        verifyStatusBox.className = "verify-status error";
        verifyStatusBox.textContent = data.error || "Connection failed. Please check your key.";
      }
    })
    .catch(err => {
      verifyStatusBox.className = "verify-status error";
      verifyStatusBox.textContent = "Network error: " + err.message;
    });
}

function saveApiKey() {
  const key = apiKeyInput.value.trim();
  if (!key) {
    showToast(`Please enter a valid ${selectedProvider()} API key.`);
    return;
  }

  fetch("/api/save-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey: key, provider: selectedProvider() })
  })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        showToast(`${selectedProvider()} API key saved successfully.`);
        settingsModal.classList.add("hidden");
        checkApiConfig();
      } else {
        showToast("Error saving key: " + data.error);
      }
    })
    .catch(err => {
      showToast("Error saving key: " + err.message);
    });
}

// =============================================================================
// Sheet Music Transcription Pipeline
// =============================================================================

function startTranscription() {
  if (!currentFile) {
    showToast("Please choose or drop a sheet music file first.");
    return;
  }

  // Show progress section
  progressSection.classList.remove("hidden");
  resultSection.classList.add("hidden");
  transcribeBtn.setAttribute("disabled", "true");
  const selectedModel = modelSelect.value;
  document.getElementById("progressSubtitle").textContent = `${selectedModel} is reading staves, clefs, notes, and dynamics...`;

  // Animate progress bar simulation
  let progress = 10;
  progressBar.style.width = progress + "%";
  const step1 = document.getElementById("step1");
  const step2 = document.getElementById("step2");
  const step3 = document.getElementById("step3");

  step1.className = "step active";
  step2.className = "step";
  step3.className = "step";

  const progressInterval = setInterval(() => {
    if (progress < 40) {
      progress += 6;
      step1.className = "step active";
    } else if (progress < 75) {
      progress += 3;
      step1.className = "step";
      step2.className = "step active";
    } else if (progress < 92) {
      progress += 1;
      step2.className = "step";
      step3.className = "step active";
    }
    progressBar.style.width = progress + "%";
  }, 400);

  const formData = new FormData();
  formData.append("file", currentFile);
  formData.append("model", modelSelect.value);
  formData.append("mandolinTab", mandolinTabToggle.checked ? "true" : "false");
  formData.append("skillLevel", skillLevelSelect.value);
  const pages = pageRangeInput.value.trim();
  if (pages) {
    formData.append("pageRange", pages);
  }

  fetch("/api/transcribe", {
    method: "POST",
    body: formData
  })
    .then(res => res.json())
    .then(data => {
      clearInterval(progressInterval);
      progressBar.style.width = "100%";

      if (!data.success) {
        throw new Error(data.error || "Transcription encountered an issue.");
      }

      setTimeout(() => {
        progressSection.classList.add("hidden");
        transcribeBtn.removeAttribute("disabled");
        displayTranscriptionResult(data);
      }, 500);
    })
    .catch(err => {
      clearInterval(progressInterval);
      progressSection.classList.add("hidden");
      transcribeBtn.removeAttribute("disabled");
      showToast("Transcription failed: " + err.message);
      if (err.message.includes("API Key")) {
        settingsModal.classList.remove("hidden");
      }
    });
}

function loadSampleScore() {
  fetch("/api/sample")
    .then(res => res.json())
    .then(data => {
      displayTranscriptionResult(data);
      showToast("Loaded sample score: " + (data.filename || "music-xml-example.xml"));
    })
    .catch(err => {
      showToast("Could not load sample: " + err.message);
    });
}

// =============================================================================
// Results Presentation & OSMD Rendering
// =============================================================================

function displayTranscriptionResult(data) {
  currentXml = data.musicxml;
  currentFilename = (data.filename || "score").replace(/\.[^/.]+$/, "") + ".musicxml";
  currentMetadata = data.metadata || {};

  // Update Score Summary Header
  scoreTitleDisplay.textContent = currentMetadata.title || "Transcribed Score";
  scoreComposerBadge.textContent = currentMetadata.composer || "Traditional";
  scoreKeyBadge.textContent = currentMetadata.keySignature || "Key Signature";
  scoreTimeBadge.textContent = currentMetadata.timeSignature || "4/4";
  scoreMeasuresBadge.textContent = `${currentMetadata.measureCount || 0} Measures`;
  const requestedModel = data.requestedModel || "—";
  const actualModel = data.modelUsed || requestedModel;
  modelUsedBadge.textContent = data.modelFallback
    ? `Model: ${actualModel} (fallback from ${requestedModel})`
    : `Model: ${actualModel}`;
  modelUsedBadge.title = data.modelFallback
    ? `The requested model was unavailable, so the transcription used ${actualModel}.`
    : `Transcription completed with ${actualModel}.`;

  if (data.isValid) {
    schemaBadge.className = "badge badge-success";
    schemaBadge.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg> MusicXML 4.0 Validated`;
  } else {
    schemaBadge.className = "badge badge-meta";
    schemaBadge.innerHTML = `MusicXML 4.0 Notice (${(data.validationErrors || []).length})`;
  }

  // Update Tab 2: XML Code Block
  xmlFilenameDisplay.textContent = currentFilename;
  const lines = currentXml.split("\n");
  xmlLinesCount.textContent = `${lines.length} lines`;
  xmlCodeContent.textContent = currentXml;

  // Update Tab 3: Detailed Metadata Cards
  metaTitle.textContent = currentMetadata.title || "—";
  metaComposer.textContent = currentMetadata.composer || "—";
  metaKey.textContent = currentMetadata.keySignature || "—";
  metaTime.textContent = currentMetadata.timeSignature || "—";
  metaClef.textContent = (currentMetadata.clef ? currentMetadata.clef + " Clef" : "—");
  metaMeasures.textContent = currentMetadata.measureCount || "—";
  metaTempo.textContent = (currentMetadata.tempo ? `${currentMetadata.tempo} BPM` : "120 BPM");
  metaParts.textContent = (currentMetadata.parts && currentMetadata.parts.length > 0) ? currentMetadata.parts.join(", ") : "Part 1";

  // Mandolin Tab Ergonomics Audit Box
  if (data.mandolinStats) {
    mandolinAuditCard.classList.remove("hidden");
    const ms = data.mandolinStats;
    const firstPosPercent = ms.totalNotes > 0 ? Math.round((ms.firstPositionCount / ms.totalNotes) * 100) : 100;
    mFirstPosRatio.textContent = `${firstPosPercent}% (${ms.firstPositionCount}/${ms.totalNotes})`;
    mOpenCount.textContent = `${ms.openStringsCount} notes`;
    mHighestFret.textContent = `Fret ${ms.highestFret}`;
    const su = ms.stringUsage || {};
    mCourseDist.textContent = `E: ${su[1] || 0} | A: ${su[2] || 0} | D: ${su[3] || 0} | G: ${su[4] || 0}`;
  } else {
    mandolinAuditCard.classList.add("hidden");
  }

  // Schema Audit Box
  if (data.isValid) {
    validationStatusBox.className = "validation-status-box valid";
    validationStatusBox.querySelector(".status-icon").textContent = "✓";
    validationStatusBox.querySelector("strong").textContent = "Strict MusicXML 4.0 W3C Schema Conformance";
    validationMessage.textContent = "All musical staves, measures, notes, accidentals, and attributes conform completely to the official W3C MusicXML schema.";
    validationErrorsList.innerHTML = "";
  } else {
    validationStatusBox.className = "validation-status-box invalid";
    validationStatusBox.querySelector(".status-icon").textContent = "!";
    validationStatusBox.querySelector("strong").textContent = "Validation Observations";
    validationMessage.textContent = "The file is functional XML with the following schema structural notes:";
    validationErrorsList.innerHTML = (data.validationErrors || []).map(e => `<li>${escapeHtml(e)}</li>`).join("");
  }

  // Update tempo slider from metadata if available
  if (currentMetadata.tempo) {
    const bpm = parseInt(currentMetadata.tempo, 10);
    if (!isNaN(bpm) && bpm >= 50 && bpm <= 220) {
      tempoSlider.value = bpm;
      tempoValue.textContent = bpm;
    }
  }

  // Parse notes for audio playback
  parseNotesForPlayback(currentXml);

  // Render Visual Score via OSMD
  resultSection.classList.remove("hidden");
  renderScore(currentXml);

  // Scroll to results
  resultSection.scrollIntoView({ behavior: "smooth" });
}

function renderScore(xmlString) {
  if (!osmdInstance) {
    initOSMD();
  }

  if (osmdInstance) {
    try {
      osmdInstance.load(xmlString)
        .then(() => {
          osmdInstance.zoom = currentZoom;
          osmdInstance.render();
          if (osmdInstance.cursor) {
            osmdInstance.cursor.hide();
          }
        })
        .catch(err => {
          console.error("OSMD render error:", err);
        });
    } catch (e) {
      console.error("OSMD invocation error:", e);
    }
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
// Audio Synthesis & Web Audio Playback
// =============================================================================

function parseNotesForPlayback(xml) {
  parsedNotes = [];
  try {
    const parser = new DOMParser();
    const xmlDoc = parser.parseFromString(xml, "text/xml");
    const notes = xmlDoc.querySelectorAll("measure note");

    let divisions = 4;
    const divElem = xmlDoc.querySelector("attributes divisions");
    if (divElem) {
      divisions = parseInt(divElem.textContent.trim(), 10) || 4;
    }

    notes.forEach(noteNode => {
      const isRest = noteNode.querySelector("rest") !== null;
      const pitchNode = noteNode.querySelector("pitch");
      const durationNode = noteNode.querySelector("duration");
      const isChord = noteNode.querySelector("chord") !== null;

      const durationUnits = durationNode ? parseInt(durationNode.textContent.trim(), 10) : divisions;
      // Duration in quarter notes
      const durationBeats = durationUnits / divisions;

      if (isRest) {
        parsedNotes.push({ isRest: true, beats: durationBeats, freq: 0, isChord: false });
      } else if (pitchNode) {
        const step = pitchNode.querySelector("step")?.textContent?.trim() || "C";
        const alter = parseInt(pitchNode.querySelector("alter")?.textContent?.trim() || "0", 10);
        const octave = parseInt(pitchNode.querySelector("octave")?.textContent?.trim() || "4", 10);
        const freq = noteToFrequency(step, alter, octave);
        parsedNotes.push({ isRest: false, beats: durationBeats, freq: freq, isChord: isChord });
      }
    });
  } catch (err) {
    console.warn("Could not parse notes for playback:", err);
  }
}

function noteToFrequency(step, alter, octave) {
  const semitones = { "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11 };
  const midiNote = 12 + (octave * 12) + (semitones[step] || 0) + alter;
  // A4 = 440Hz = MIDI 69
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
  if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }

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
  if (osmdInstance && osmdInstance.cursor) {
    osmdInstance.cursor.reset();
    osmdInstance.cursor.hide();
  }
}

function playNextNote() {
  if (!isPlaying || playbackIndex >= parsedNotes.length) {
    stopPlayback();
    return;
  }

  const note = parsedNotes[playbackIndex];
  const bpm = parseInt(tempoSlider.value, 10) || 120;
  const beatDurationSec = 60 / bpm;
  const noteDurationSec = Math.max(0.08, note.beats * beatDurationSec);

  if (!note.isRest && note.freq > 0) {
    playSynthesizedTone(note.freq, noteDurationSec, synthSound.value);
  }

  playbackIndex++;
  playbackTimeout = setTimeout(playNextNote, noteDurationSec * 1000);
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

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 3500);
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
