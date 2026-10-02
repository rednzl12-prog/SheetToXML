# 🎼 SheetXML — AI Sheet Music to MusicXML Transcriber

An Optical Music Recognition (OMR) system powered by Gemini multimodal AI. It transcribes PDF scores and images of sheet music into validated **MusicXML 4.0** format.

---

## ✨ Features

- **Playable Mandolin Tablature & Ergonomics**: Built-in Viterbi Dynamic Programming optimizer strictly adhering to `MANDOLIN_TAB_FINGERING_SPEC.md`. Transcribes to 4-line Mandolin TAB (G3 D4 A4 E5), prioritizing first position (frets 0–7), idiomatic open strings, compact hand windows, and chord reachability.
- **Multi-provider OMR**: Uses Gemini or Qwen for PDF/image transcription, and DeepSeek Flash for image transcription.
- **W3C MusicXML 4.0 Compliance**: Validates against the official MusicXML 4.0 XSD schema included in `musicxml-4.0/schema/`.
- **Interactive Visual Score Preview**: Powered by [OpenSheetMusicDisplay](https://github.com/opensheetmusicdisplay/opensheetmusicdisplay) (`osmd.min.js`, 100% offline-capable).
- **Real-Time Audio Synthesizer**: Built-in Web Audio engine to listen to the transcribed score with tempo slider (BPM) and switchable instruments (Grand Piano, Ambient Bells, Wood Marimba).
- **Multiple Run Modes**:
  1. **Desktop Shortcut** (`SheetXML.lnk` on Desktop)
  2. **Interactive Web GUI** (`run.bat`, `SheetXML.exe`, or `python app.py`)
  3. **Explorer Drag-and-Drop** (`transcribe.bat`)
  4. **Command Line (CLI)** (`python app.py input.pdf -o output.musicxml`)
- **Universal DAW & Notation Compatibility**: Output `.musicxml` files open directly in **MuseScore**, **Sibelius**, **Finale**, **Dorico**, **Guitar Pro**, and any modern DAW.

---

## 🔑 Connecting an AI Provider

Create an API key for the provider and enter it in the Settings dialog after selecting its model.

To connect SheetXML to your Gemini subscription:

1. Go to **[Google AI Studio](https://aistudio.google.com/app/apikey)**.
2. Sign in with the Google Account that holds your Pro subscription.
3. Click **"Create API Key"** and copy your key.
4. Set your key using any of these methods:
   - **In the Web UI**: Select a model, click **AI Connection Settings**, paste the matching provider key, and click **Save & Apply**.
   - **In a `.env` file**: Create or edit `.env` in this directory:
     ```env
     GEMINI_API_KEY="AIzaSy..."
     QWEN_API_KEY="..."
     DEEPSEEK_API_KEY="..."
     ```
   - **Via Windows Environment Variable**:
     ```powershell
     [System.Environment]::SetEnvironmentVariable('GEMINI_API_KEY', 'your-key-here', 'User')
     ```
   - **Via CLI flag**:
     ```bash
     python app.py sheet.pdf --key "AIzaSy..."
     ```

---

## 🚀 Quickstart

### 1. Desktop Shortcut (Fastest)
Double-click the **SheetXML** shortcut on your **Desktop** (`SheetXML.lnk`) or double-click **`SheetXML.exe`** in this folder.
- Launches silently without an intrusive console window.
- Automatically opens your default web browser to `http://localhost:5050`.

### 2. Interactive Web GUI
Double-click `run.bat` or run in terminal:
```bash
python app.py
```
This automatically launches the server on `http://localhost:5050` and opens your browser.

- **Drag and drop** any PDF or sheet music image (`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tiff`).
- Choose a provider model. Gemini and Qwen support PDFs; DeepSeek Flash supports images.
- Click **"Transcribe to MusicXML"**.
- View the rendered sheet music, play it with the synth, or click **"Download .musicxml"**.
- Click **"Try Sample Score"** anytime to preview the built-in example score (`music-xml-example.xml`).

### 2. Explorer Drag-and-Drop
Drag any sheet music PDF or image file and drop it directly onto **`transcribe.bat`** in Windows Explorer. It will transcribe the score and output the `.musicxml` file right alongside your original file!

### 3. Command Line Interface (CLI)
```bash
# Basic transcription
python app.py myscore.pdf

# Specify output filename and model
python app.py myscore.png -o myscore.musicxml --model gemini-3.8-flash

# Transcribe specific pages of a multi-page PDF (e.g. pages 1 to 2)
python app.py full_book.pdf -p "1-2" -o excerpt.musicxml
```

---

## 📁 Project Structure

```
sheettoxml/
├── SheetXML.exe               # 1-click standalone desktop executable (with custom icon)
├── run.bat                    # 1-click Windows batch runner for Web GUI
├── transcribe.bat             # Drag-and-drop batch runner for Windows Explorer
├── app.py                     # Main application (Engine, CLI, Schema Validator, Web Server)
├── mandolin_optimizer.py      # Spec-compliant Mandolin Tab Viterbi optimizer
├── requirements.txt           # Python dependencies
├── .env                       # Local provider keys (not committed)
├── README.md                  # Complete documentation
├── static/                    # Frontend Web UI (HTML5, CSS3, JS)
│   ├── index.html             # Dark glassmorphic layout with Mandolin Tab controls
│   ├── style.css              # Custom styling system with micro-interactions
│   ├── app.js                 # UI logic, OSMD rendering & Web Audio synthesizer
│   └── osmd.min.js            # Bundled OpenSheetMusicDisplay library (offline)
├── schemas/                   # Official W3C MusicXML 4.0 XSD schemas & catalog
├── resources/                 # Reference docs, specs, and source assets
│   ├── MANDOLIN_TAB_FINGERING_SPEC.md   # Official Mandolin Tab specification
│   ├── HowToReadSheetMusic.pdf          # Notation reference guide
│   ├── music-xml-example.xml            # Reference score (Maid Behind the Bar)
│   ├── launcher.cs                      # C# source for SheetXML.exe
│   └── icon.ico                         # High-res musical note icon
└── transcriptions/            # Output folder for transcribed MusicXML scores
```

---

## 🛠️ Requirements & Dependencies

- **Python**: 3.10, 3.11, 3.12, 3.13, or 3.14
- **Libraries**:
  - `google-genai`: Official Google GenAI SDK
  - `lxml`: High-performance XML parser & XSD validator
  - `pypdf`: Multi-page PDF document inspection
  - `pillow`: Image processing & thumbnailing
