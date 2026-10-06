# SheetXML

Turns sheet music (PDF, scan or photo) into **MusicXML 4.0** with a notation staff plus a playable
**mandolin TAB** staff (G3 D4 A4 E5, first position and open strings preferred, see
`resources/MANDOLIN_TAB_FINGERING_SPEC.md`). It can also add TAB to an existing `.musicxml`/`.mxl`/`.abc` file.
Runs locally: a small web app at `http://localhost:5050` plus a command line.

## How it works

Every engine produces the same internal score; SheetXML then writes the MusicXML (with TAB), checks it
against the official MusicXML 4.0 schema, and flags measures whose length does not add up ("check").

| Engine | Use it for | Cost | Accuracy |
|---|---|---|---|
| **Exact PDF reader** (no AI) | PDFs exported from MuseScore, Sibelius, Finale, Dorico | free, offline, ~0.1 s | exact: Pachelbel's Canon, 57/57 bars, 100% of notes |
| **AI vision model** | scans, photos, PDFs of scans | API credits | code finds the staves, bars and noteheads; the model reads one staff system at a time and every answer is checked and repaired. Qwen 3.8 Flash: 98% of notes on the hardest test page |
| **Audiveris** (optional) | scans and photos without any AI | free, offline, ~10-15 s/page | classic OMR |
| **Auto** (default) | everything | | exact reader for born-digital PDFs, otherwise Audiveris if installed, otherwise AI if a key is set (AI also steps in when Audiveris fails or leaves more than 1 in 8 measures inconsistent) |

Systems the AI could not read are listed as warnings (never silently dropped). Fix any misread note in the
**ABC (edit)** tab and press **Apply edits** to re-render.

## Install

Python 3.11+ on Windows, then:

```bash
pip install -r requirements.txt
```

Start it with `SheetXML.exe`, `run.bat`, or `python app.py`. Drop a file onto `transcribe.bat` to convert it directly.

### No-AI options
- **Born-digital PDFs** need nothing else: the exact reader handles them.
- **Scans/photos without AI**: install [Audiveris 5.11](https://github.com/Audiveris/audiveris/releases)
  (free, the Windows installer bundles Java, installs to `C:\Program Files\Audiveris`). SheetXML finds it
  automatically, or set `AUDIVERIS` to its folder or `Audiveris.exe`.
  Measured here: 8-16 s per page; 15-16 of 17 bars exact on simple tunes (clean or scanned), but only
  13 of 30 on a dense 16th/32nd scan - that is where the AI engine earns its keep.

### AI models and keys
Enter keys in **AI Settings** in the web app (stored in `.env` next to `app.py`, never uploaded anywhere
except to the provider), or set `GEMINI_API_KEY` / `QWEN_API_KEY` as environment variables.

| Model | Key | Notes |
|---|---|---|
| Gemini 3.8 Flash (default with a Gemini key) | Gemini, from [Google AI Studio](https://aistudio.google.com/app/apikey) | often busy ("high demand"): falls back to 3.7 / 3.5 Flash. Free tier = small daily quota |
| Gemini 3.1 Pro Preview, Gemini Pro Latest | Gemini | strongest, quota-limited on the free tier |
| Qwen 3.8 Flash (default without a Gemini key) | Qwen Token Plan (`sk-sp-...`) | fast, good results |
| Qwen 3.7 Plus, Qwen 3.6 Flash | Qwen | |
| Qwen 3.8 Max | Qwen | very slow (~2.5 min per staff system) |
| DeepSeek V4.1 Flash | Qwen Token Plan | DeepSeek served through the Qwen key |

**Accuracy: Careful** reads every system three times and takes the majority per bar (about 3x the API cost).
"Test" in Settings checks a key without spending tokens.

A Claude Pro/Max subscription does not include API access; the Claude API is billed separately through the
Claude Console, and SheetXML does not use it.

## Command line

```bash
python app.py score.pdf                          # auto engine, writes score.musicxml
python app.py scan.jpg -o out.musicxml --engine ai --model qwen3.8-flash --votes 3
python app.py score.pdf -p 1-2 --no-tab          # pages 1-2, notation only
python app.py old.musicxml --skill advanced      # add TAB to an existing file -> old-tab.musicxml
python app.py --help
```

Options: `--engine auto|vector|ai|audiveris`, `--model ID`, `--votes 1|3`, `-p PAGES`, `--no-tab`,
`--skill beginner|advanced`, `-k KEY`, `-o OUT`. With no file, `python app.py [--port N] [--no-browser]` starts the web app.
The CLI prints progress, warnings and the measures to check.

## Files

`app.py` web server + CLI · `vectorpdf.py` exact PDF reader · `pipeline.py`, `omr.py`, `llm.py` AI pipeline ·
`audiveris.py` Audiveris bridge · `abcxml.py` score model, ABC and MusicXML reader/writer · `mandolin_optimizer.py`
TAB fingering · `schemas/` MusicXML 4.0 XSD · `static/` web UI (OpenSheetMusicDisplay, offline).

The server only listens on 127.0.0.1 and only answers its own page (no cross-site access). Saved files go to `transcriptions/`.

License: MIT.
