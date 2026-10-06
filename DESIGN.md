---
name: SheetXML
description: Sheet music in, mandolin tab and MusicXML out
colors:
  burst-hot: "#E8A845"
  burst-centre: "#D8902B"
  burst-warm: "#B8661C"
  burst-mid: "#7A3E12"
  burst-deep: "#4A2A12"
  burst-dark: "#2E1B0C"
  burst-edge: "#24160B"
  on-lacquer: "#F7EFDD"
  on-lacquer-2: "#E6D7B8"
  ivoroid: "#EDE0BF"
  purfling-dark: "#17140F"
  purfling-light: "#FBF8F0"
  edge: "#2A1F14"
  ebony: "#17140F"
  ebony-2: "#221D17"
  nickel: "#C9CCC9"
  pearl: "#F2EEE6"
  pearl-dim: "#B5AC9F"
  fret-empty: "#77726A"
  ground: "#DADBD6"
  panel: "#FAF8F3"
  paper: "#FFFFFF"
  ink: "#1D1813"
  ink-2: "#5A5048"
  line: "#BDB5A8"
  line-strong: "#8F8578"
  amber: "#E8A845"
  inspector: "#B3261E"
  inspector-wash: "#FBEDEA"
  tobacco: "#7A3E12"
typography:
  display:
    fontFamily: "Big Shoulders Display, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "2.6rem"
    fontWeight: 800
    lineHeight: 0.95
    letterSpacing: "0.005em"
  headline:
    fontFamily: "Big Shoulders Display, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "2.3rem"
    fontWeight: 800
    lineHeight: 1.02
    letterSpacing: "0.005em"
  title-panel:
    fontFamily: "Big Shoulders Display, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "1.9rem"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "0.005em"
  title:
    fontFamily: "Big Shoulders Display, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "1.6rem"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "0.01em"
  body:
    fontFamily: "system-ui, Segoe UI Variable Text, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  label:
    fontFamily: "Big Shoulders Display, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "0.98rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.04em"
  numeral:
    fontFamily: "Consolas, Cascadia Mono, ui-monospace, monospace"
    fontSize: "1.35rem"
    fontWeight: 600
    lineHeight: 1.3
    fontFeature: "tnum"
  tab:
    fontFamily: "Consolas, Cascadia Mono, ui-monospace, monospace"
    fontSize: "0.9rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  xs: "4px"
  sm: "6px"
  md: "12px"
spacing:
  xs: "6px"
  sm: "12px"
  md: "18px"
  lg: "28px"
components:
  button-primary:
    backgroundColor: "{colors.ebony}"
    textColor: "{colors.pearl}"
    rounded: "{rounded.sm}"
    padding: "0 16px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "#2E2820"
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0 16px"
    height: "40px"
  button-secondary-hover:
    backgroundColor: "#F1ECE2"
  button-lacquer:
    backgroundColor: "#1A110A"
    textColor: "{colors.on-lacquer}"
    rounded: "{rounded.sm}"
    padding: "0 16px"
    height: "40px"
  button-lacquer-hover:
    backgroundColor: "#33210F"
  button-convert:
    backgroundColor: "{colors.ebony}"
    textColor: "{colors.pearl}"
    typography: "{typography.title}"
    rounded: "{rounded.sm}"
    padding: "0 22px"
    height: "48px"
    width: "100%"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
    height: "40px"
  panel-bound:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "26px 28px 28px"
  step-number:
    backgroundColor: "{colors.ebony}"
    textColor: "{colors.pearl}"
    rounded: "{rounded.xs}"
    size: "30px"
  tab-sheet:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "10px 14px 9px"
  tab-sheet-active:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  badge:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.xs}"
    padding: "3px 8px"
  ebony-text:
    backgroundColor: "{colors.ebony}"
    textColor: "{colors.pearl}"
    typography: "{typography.tab}"
    rounded: "{rounded.xs}"
    padding: "16px 18px"
  notice:
    backgroundColor: "{colors.inspector-wash}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "12px 14px"
---

# Design System: SheetXML

## Overview

**Creative North Star: "Sunburst & Pearl"**

The app wears the instrument: a Loar-era F-5 mandolin. Sunburst lacquer carries the chrome (the masthead band, the job band head, the settings dialog head). Ivoroid binding with black/ivoroid/black purfling edges every working panel, and the masthead's foot carries the same binding strip. The tab lives on an ebony fingerboard with nickel frets and pearl dots; the score reads on clean white paper. Everything else is a quiet spruce-grey ground that lets both ivoroid and white sheets read.

Density is a desk tool's density: one centred column (1180px), bound panels stacked in the order the job happens, hairline structure inside them. Lettering is condensed and heavy (Big Shoulders Display); running text is the system UI face; every number a player reads (tab, fret counts, readouts, file sizes) is Consolas with tabular figures. Motion has one authored moment: progress ticks stamp in, one per staff system. Everything else is instant or a 120ms colour change.

The world rejects the dark-glass AI dashboard and the plain white card stack.

**Key Characteristics:**
- Sunburst lacquer chrome built from one seven-stop burst sequence.
- Ivoroid binding with black/ivoroid/black purfling on every bound surface.
- Ebony fingerboard for the tab: nickel frets, pearl dots, amber hand window.
- Condensed heavy display lettering, system body, monospace numerals.
- Inspector red marks only what the player must check.

## Colors

A warm instrument palette: lacquer browns and ambers for chrome, ivoroid and pearl for binding and inlay, ebony for the playing surfaces, and a cool grey ground under white paper.

### Primary
- **Sunburst Lacquer** (burst-hot through burst-edge): a seven-stop radial burst, hot amber centre to near-black edge. Chrome only: masthead, job band head, dialog head. Text on it is **Lacquer Cream** (on-lacquer) or **Aged Cream** (on-lacquer-2) for secondary lines.
- **Ebony** (ebony, ebony-2): the fingerboard, the text tab, primary buttons, step numbers, the active sheet number, the toast. Text on ebony is **Pearl**, with **Dim Pearl** (pearl-dim) for labels and older log lines.

### Secondary
- **Ivoroid** (ivoroid): the binding band, the nut on the rail, the brand mark, text selection.
- **Purfling** (purfling-dark, purfling-light): the black/white/black lines inside every binding.
- **Nickel** (nickel): fret wires on the rail and the progress meter fill.

### Tertiary
- **Hand-Window Amber** (amber): the active hand position on the fingerboard rail (a 22% wash with 2.5px rails). Nowhere else.
- **Tobacco** (tobacco): links, focus rings on light surfaces, the active sheet tab's top edge, disclosure triangles, the drop-target outline, "confirmed" stamps and ticks.
- **Inspector Red** (inspector, on inspector-wash): measures to check, warnings, invalid validation, error states. Never decorative.

### Neutral
- **Spruce Grey** (ground): page background.
- **Panel Cream** (panel): inside bound panels and the dialog.
- **Paper White** (paper): score sheets, inputs, dropzone, readout, active tab.
- **Ink** (ink) and **Second Ink** (ink-2): primary and secondary text.
- **Hairline** (line) and **Control Edge** (line-strong): dividers, then input and dropzone borders.

### Named Rules
**The One Amber Rule.** Amber as a flat ink appears only as the hand window on the fingerboard rail. The lacquer's hot centre shares the hex but lives only inside the burst gradient; it never becomes a button, badge, or highlight.

**The Inspector Only Rule.** Red appears only where the player must check something. A surface that is not a warning or error carries no red.

**The Paper Is White Rule.** Notation renders black on pure white (paper). Never tint, darken, or texture the score sheet.

## Typography

**Display Font:** Big Shoulders Display, self-hosted variable (100-900) (with Bahnschrift, Arial Narrow)
**Body Font:** system-ui (with Segoe UI Variable Text, Segoe UI)
**Label/Mono Font:** Consolas (with Cascadia Mono, ui-monospace)

**Character:** Condensed heavy lettering like a headstock decal, set over a plain system body; numerals in a workshop monospace so tab columns and counts align.

### Hierarchy
- **Display** (800, 2.6rem, 0.95): the SheetXML wordmark only; "XML" drops to weight 450 in Aged Cream. 2.1rem below 640px.
- **Headline** (800, 2.3rem, 1.02): the piece title on the result panel. 1.85rem below 640px.
- **Panel Title** (800, 1.9rem, 1): bound-panel headings, led by an ebony step number. 1.6rem below 640px.
- **Title** (800, 1.6rem, 1.1): job band heading, dialog heading, dropzone prompt. Smaller section heads step to 1.45rem and 1.25rem at the same weight.
- **Body** (400, 15px, 1.5): running text, tabular figures on. Hints at 0.82rem in Second Ink, capped at 62ch; prose capped at 70ch.
- **Label** (700, 0.98rem, 0.04em, uppercase): field labels, readout terms (0.78rem, 0.06em), tick-row labels, meta labels. Display face, never the body face.
- **Numeral** (Consolas 600, 1.35rem): readout values; Consolas also sets the tab (0.9rem/1.5), log, file size, step and sheet numbers.

### Named Rules
**The Numbers Are Consolas Rule.** Any figure a player compares or plays (frets, counts, sizes, readouts, tab) sets in the monospace face with tabular figures.

**The Display Never Runs Rule.** Big Shoulders sets headings, labels, and stamps only; paragraphs and buttons use the body face (the full-width Convert button is the one display-set button).

## Layout

One centred column, max 1180px, padding 28px 24px 64px, with 28px between bound sections; below 640px it tightens to 18px 16px 48px with 20px gaps and panels pad 18px 16px 20px. The masthead uses the same 1180px inner width, wordmark left and actions right, wrapping on narrow screens.

Controls lay out in auto-fit grids (minmax 210-220px, gaps 18px 20px). The arrangement foot splits 3fr/2fr above 860px and stacks below. Readouts run four cells across, two below 640px. Tick rows are a 9.5rem label column plus ticks, stacking below 640px. The fingerboard rail scrolls horizontally inside its figure (min-width 620px, 470px compact) rather than shrinking unreadably.

Rhythm: 6px inside fields, 12px between related items, 18-22px between control groups, 28px between sections.

## Elevation & Depth

Flat. Depth comes from material, not shadow: lacquer against ground, inset purfling lines inside the binding, and the ebony fingerboard against cream. The primary button carries an inset second ring (a fret-edge bevel). The only cast shadow is the toast; the dialog sits on a 60% ebony scrim.

### Shadow Vocabulary
- **Purfling** (`box-shadow: inset 0 0 0 1px #17140F, inset 0 0 0 2px #FBF8F0, inset 0 0 0 3px #17140F`): inside every bound surface's 5px ivoroid border.
- **Ebony bevel** (`box-shadow: inset 0 0 0 2px #17140F, inset 0 0 0 3px #4B4237`): primary buttons.
- **Toast lift** (`box-shadow: 0 10px 28px -6px rgba(23, 20, 15, 0.45)`): the toast only.

### Named Rules
**The Binding Not Shadow Rule.** A surface earns separation with binding or a hairline, never a drop shadow. Floating, transient elements (the toast) are the only exception.

## Shapes

Gently rounded and instrument-like. Bound panels, the job band, and the dialog round at 12px with a 5px ivoroid band, a 1px dark outer edge, and three inset purfling lines. Controls, buttons, notices, and tab sheets round at 6px. Small inlays (step numbers, badges, the text tab, chord shapes) round at 4px; ticks and stamps at 2-3px. The toggle track is a full pill and its thumb a circle. Stamps tilt -2deg like a rubber stamp. Disclosure markers are clipped tobacco triangles that rotate 90deg when open.

## Components

### Buttons
Solid and plain; the colour comes from the material they sit on.
- **Shape:** gently curved (6px), 40px tall, 16px side padding, body face 600 at 0.9rem; small variant 34px.
- **Primary:** ebony with pearl text and the ebony bevel; hover lightens to #2E2820.
- **Secondary:** paper with an ink border; hover warms to #F1ECE2.
- **Lacquer:** for use on sunburst only. Near-black #1A110A with a brass-tone #A8916A border and cream text; hover deepens to #33210F with an ivoroid border.
- **Convert:** full-width primary at 48px, display face 700 at 1.25rem.
- **Icon:** 34px square, transparent until hover.
- **Focus:** 2px outline offset 2px, tobacco on light surfaces, cream on lacquer, pearl on ebony.
- **Disabled:** 45% opacity.

### Chips
- **Badge:** paper, 1px hairline border, 4px radius, 0.78rem. The confirmed badge takes an ink border and weight 600.
- **Stamp:** display face 800, 0.8rem, 0.1em uppercase, 1.5px border, -2deg tilt. Confirmed in tobacco, repaired with a 3px double border, check in inspector red on its wash.

### Cards / Containers
- **Corner Style:** 12px (bound panels), 6px (inner containers).
- **Background:** panel cream; inner work surfaces on paper.
- **Shadow Strategy:** binding and purfling only (see Elevation).
- **Border:** the binding; inside it, 1px ink or hairline borders.
- **Internal Padding:** 26px 28px 28px; 18px 16px 20px on phones.

### Inputs / Fields
- **Style:** paper, 1px control-edge border, 6px radius, 40px tall, 8px 12px padding. Selects use an inline chevron.
- **Hover:** border goes to ink.
- **Focus:** the global 2px tobacco outline.
- **Disabled:** #EFECE5 fill with second ink.
- **Toggle:** a 42x24 pill outlined in ink; on, it turns ebony with a pearl thumb.

### Navigation
- **Sheet index (result views):** numbered folder tabs in display face 700 at 1.08rem over an ink baseline. Each has a monospace number box. The active tab is paper with an ink border, a 3px tobacco top edge, and an ebony/pearl number. Inactive tabs are second ink and warm on hover.
- **Masthead:** a sunburst band with the brand mark and wordmark left, lacquer buttons right, and a 6px black/ivoroid/black binding strip along its foot.

### Fingerboard Rail (signature)
An SVG ebony board with nickel fret wires, an ivoroid nut, string lines in two warm-grey tones, and pearl-gradient fret dots with ebony numerals. The amber hand window brackets the active position. Hovering or playing a tab note lights it; arrangement changes redraw it live. Labels are in Dim Pearl Consolas.

### Job Band
A bound ebony band: a lacquer head (heading, cancel) above a fingerboard board. Its tick rows are 12x24 outlined frets that fill pearl and stamp in (520ms, scale 1.9 and -8deg to rest). A 3px nickel meter and a Consolas log follow, with the newest line in full pearl.

### Text Tab
Monospace tab on ebony at 0.9rem/1.5, 4px radius, scrolling within 70vh. The current bar lights #3A3127; the current note inverts to pearl with ebony text.

## Do's and Don'ts

### Do:
- **Do** build every lacquer surface from the seven burst tokens; prefer the shared lacquer gradient (ellipse 58% 260% at 50% 125%).
- **Do** edge every working panel with the binding: 5px ivoroid, 1px dark outer edge, black/ivoroid/black inset purfling, 12px radius.
- **Do** put tab and fret numbers on ebony in pearl Consolas, and score notation on pure white.
- **Do** set labels in uppercase Big Shoulders 700 with 0.04-0.06em tracking.
- **Do** switch the focus colour by material: tobacco on light, cream on lacquer, pearl on ebony.
- **Do** keep motion to the tick stamp and short 120-140ms state changes, and drop all of it under reduced motion.

### Don't:
- **Don't** use amber as a flat ink anywhere but the fingerboard hand window.
- **Don't** use inspector red for anything but what the player must check.
- **Don't** add drop shadows to panels or buttons; separation comes from binding and hairlines.
- **Don't** tint, darken, or texture the score sheet.
- **Don't** set body paragraphs or ordinary buttons in the display face.
- **Don't** load fonts from a CDN; the display face is self-hosted so the app works offline.
