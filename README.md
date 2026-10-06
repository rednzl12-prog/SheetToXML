# SheetXML

Turns sheet music (PDF, scan or photo) into **MusicXML 4.0** with a notation staff plus a playable
**tab staff for the mandolin family** (mandolin, mandola, octave mandolin, mandocello; any tuning, capo and
fingering style). It can also add tab to an existing `.musicxml` / `.mxl` / `.abc` file, writes a plain-text
tab, and describes the piece (key, form, difficulty, practice tips, chords that fit).
Everything runs on your computer: a small web app at `http://localhost:5050` plus a command line.

## How it reads music

Every engine produces the same internal score; SheetXML then writes the MusicXML and tab, checks it against
the official MusicXML 4.0 schema, and marks measures whose notes don't add up ("check").

| Engine | Used for | Needs | Measured accuracy |
|---|---|---|---|
| **Exact PDF reader** | PDFs exported from MuseScore, Sibelius, Finale, Dorico | nothing (no AI, ~1 s) | exact: Pachelbel's Canon, 57/57 bars |
| **Audiveris** | scans and photos, offline | [Audiveris 5.11](https://github.com/Audiveris/audiveris/releases) (free) | 95% of bars exact on clean pages, 70% on degraded scans |
| **Audiveris + AI check** (hybrid) | scans and photos | Audiveris and an AI key | 98% of bars exact on clean pages, 96% on degraded scans |
| **AI only** | scans and photos without Audiveris | an AI key | the model reads one staff system at a time; SheetXML finds staves, bars and noteheads and checks every answer |

**Auto** (the default) picks for you: MusicXML/ABC files are imported; PDFs exported from notation software
go to the exact reader; anything else goes to Audiveris, and when an AI key is set every staff system is then
checked by the AI model (hybrid). Systems where Audiveris and SheetXML's own notehead reader agree cost no AI
call at all. Without Audiveris, Auto uses the AI alone; without a key, Audiveris alone.

**How it was read** (in the results) lists the engine, the AI calls made, the systems confirmed without AI and
the status of every system. Fix any misread note in **Fix notes (ABC)** and press **Apply**.

## Your instrument and tab

Pick the instrument, tuning (presets such as GDAE, AEAE, GDGD, ADAE, open G, or four notes of your own), capo,
fingering style (open position, closed position, or a fixed position such as 5th or 7th), highest fret and
skill level. Changes redraw the score and tab at once, before or after converting. Notes the arrangement can't
reach are left off the tab and counted; octave instruments sound an octave below the written notes.
The **Tab (text)** view gives the same fingering as plain text to copy or download.

**About this piece** shows the key (and the key the notes actually point to), meter, tempo and length, range,
form, difficulty with reasons, practice tips, and suggested chords with mandolin chord shapes for your tuning
(suggestions from the melody, not chords printed in the original). **Write an AI note** adds a short write-up
from those facts (one small AI call).

## AI services and keys

Keys are optional: exported PDFs and Audiveris need none. Add keys in **AI keys** in the web app, or paste
any key into the "paste any key" box and SheetXML works out which service it belongs to. **Test** checks a key
without using tokens and lists the models on your account. Keys are stored in `.env` in SheetXML's data folder
and are only sent to that service.

| Service | Key from | Recommended model |
|---|---|---|
| Google Gemini | [Google AI Studio](https://aistudio.google.com/app/apikey) (free tier, small daily quota) | Gemini 3.8 Flash |
| Anthropic Claude | [Claude Console](https://platform.claude.com/settings/keys) | Claude Sonnet 5.5 |
| OpenAI | [OpenAI platform](https://platform.openai.com/api-keys) | GPT-6.1 Sol |
| Qwen (Token Plan `sk-sp-...`) | [Qwen Cloud](https://home.qwencloud.com/) | Qwen 3.8 Flash (98% of notes on the hardest test page); the plan also serves DeepSeek V4.1 Flash |
| DeepSeek | [DeepSeek platform](https://platform.deepseek.com/api_keys) | DeepSeek V4.1 Flash |
| xAI Grok | [xAI console](https://console.x.ai/) | Grok 4.7 |
| Mistral | [Mistral console](https://console.mistral.ai/api-keys) | Mistral Medium |
| OpenRouter | [OpenRouter](https://openrouter.ai/keys) | many vendors behind one key |
| Custom / local | any OpenAI-compatible server: Ollama `http://localhost:11434/v1`, LM Studio `http://localhost:1234/v1`, Groq, Together... | discovered from the server, or type the model id |

A Claude Pro or Max subscription does not include API access: the Claude API is billed separately in the
Claude Console. **Accuracy: Careful** reads every system three times and keeps the majority (about 3x the API
cost). When Gemini's free daily quota runs out, SheetXML says so and you can pick another service.

## Offline use

The exact PDF reader, Audiveris, the tab, the text tab, the analysis and the web app itself (OpenSheetMusicDisplay
is bundled) all work without internet. Only the AI engines and the AI note need a connection.

## Install

- **Windows installer**: run the SheetXML installer, then start SheetXML from the Start menu. Your keys, log and
  saved files live in `%LOCALAPPDATA%\SheetXML`. Starting it again while it runs just opens the browser.
- **From source**: Python 3.11+, then `pip install -r requirements.txt` and start `run.bat` or `python app.py`.
  Data files stay in the project folder. Drop a file onto `transcribe.bat` to convert it directly.

For scans without AI, install [Audiveris 5.11](https://github.com/Audiveris/audiveris/releases) (its Windows
installer bundles Java); SheetXML finds it in `C:\Program Files\Audiveris`, or set `AUDIVERIS` to its folder.

## Command line

```bash
python app.py score.pdf                                   # auto engine, writes score.musicxml
python app.py scan.jpg --engine hybrid --provider qwen --model qwen3.8-flash
python app.py scan.jpg --engine ai --provider anthropic --votes 3
python app.py tune.pdf --tuning gdgd --capo 2 --style closed --ascii --details
python app.py tune.abc --instrument mandola --style position --position 5 --max-fret 12
python app.py old.musicxml --skill advanced               # add tab to an existing file -> old-tab.musicxml
```

Options: `--engine auto|vector|audiveris|hybrid|ai`, `--provider ID`, `-m/--model ID`, `--votes 1|3`,
`-p PAGES`, `--no-tab`, `--instrument`, `--tuning` (preset id or four notes low to high, e.g. `"G3 D4 G4 D5"`),
`--capo`, `--style open|closed|position`, `--position`, `--max-fret`, `--skill beginner|advanced`,
`--ascii` (also write a `.txt` tab), `--details` (print the analysis), `-k KEY`, `-o OUT`.
With no file, `python app.py [--port N] [--no-browser]` starts the web app.

## Files

`app.py` web server + CLI · `vectorpdf.py` exact PDF reader · `audiveris.py` Audiveris bridge · `pipeline.py`,
`omr.py` AI and hybrid pipeline · `llm.py` AI services · `abcxml.py` score model, ABC/MusicXML reader and writer,
text tab · `mandolin_optimizer.py` fingering · `analysis.py` piece analysis · `schemas/` MusicXML 4.0 XSD ·
`static/` web UI.

The server only listens on 127.0.0.1 and only answers its own page (no cross-site access). Saved files go to
`transcriptions/` in the data folder.

License: MIT.
