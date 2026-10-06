# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Mandolin-family players in general, from beginners to experienced players (confirmed: public release, not a private tool). They sit at a desk or laptop, turn a piece of sheet music they already have (a PDF, a scan, a photo, or an existing MusicXML/ABC file) into tab they can play, check it, then export or print it.

## Product Purpose

SheetXML converts sheet music into playable mandolin tablature plus standard MusicXML 4.0. Success is a tab the player can trust and play without re-checking every note: correct pitches and rhythms, and fingerings a real player would choose for their instrument, tuning and skill.

## Positioning

- PDFs exported from notation software are read exactly from the file itself, with no AI and no internet.
- Scans and photos go through free offline recognition (Audiveris), and an AI model only verifies or fixes the measures that disagree. Every measure is checked against the time signature, so the app knows and shows where it is unsure instead of silently guessing.
- The fingering engine plans a whole phrase for the hand: instrument (mandolin, mandola, octave mandolin, mandocello), tuning (including cross tunings), capo, and open / closed / position playing styles.

## Operating Context

- Runs locally on Windows: a Python server serving a browser UI at localhost, packaged as a per-user installer. A command-line variant handles batch and drag-and-drop use.
- Typical session: drop a file, wait seconds to a few minutes, review the score and tab, fix any flagged measure (in ABC text), adjust the arrangement, then download MusicXML (for MuseScore, Guitar Pro, Sibelius, Finale, Dorico) or plain-text tab.
- AI is optional. Keys for Gemini, Anthropic, OpenAI, Qwen, DeepSeek, xAI, Mistral, OpenRouter, or a local OpenAI-compatible server are stored locally.

## Capabilities and Constraints

- Engines: exact PDF reader, Audiveris (offline OMR), AI, and the Audiveris+AI hybrid; "Auto" picks the right one.
- Results:
  - score preview (OpenSheetMusicDisplay) with playback
  - plain-text tab
  - "About this piece": key/mode, range, form, difficulty, tips, chord shapes
  - optional AI note
  - editable ABC
  - MusicXML view
  - a "how it was read" provenance view
- Long jobs run in the background with real progress and cancel.
- Must work fully offline when no AI is used. No accounts and no cloud storage.

## Brand Commitments

- Name: SheetXML. Icon: resources/icon.ico (musical-note icon).
- Constraint from the user: the interface must not feel plain or generic ("too plain" is the failure).

## Evidence on Hand

- Measured accuracy (exact bars):
  - Pachelbel Canon PDF: 57/57, exact reader.
  - Test corpus, hybrid engine: 98% on clean pages, 96% on degraded scans.
  - Audiveris alone: 95% clean, 70% scans.
- Sample score: resources/music-xml-example.xml. Reference PDF: resources/Pachelbel_Canon.pdf.
- No testimonials, users, download counts or press exist; none may be invented.

## Product Principles

1. Show certainty honestly: every measure is checked, and doubtful ones are pointed out rather than hidden.
2. Offline first, AI optional: the free, local path is the default, and AI is a second opinion.
3. Playable beats merely valid: fingering, position and tuning choices are first-class.
4. Speak musician, not engineer: plain musical language up front, technical detail available on request.

## Accessibility & Inclusion

Tab and notation must stay highly legible (contrast and size). The UI is fully keyboard-operable with labelled controls and polite live progress updates. Phone-width layout must not break, though phones are not the primary setting.
