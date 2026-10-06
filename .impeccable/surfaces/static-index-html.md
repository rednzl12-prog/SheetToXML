---
version: 1
slug: "static-index-html"
primary_target: "static/index.html"
related_targets: ["static/style.css","static/app.js"]
---

# SheetXML app (static/index.html)

Scope: the single-page local web app (upload, engine/AI choice, arrangement, results views, settings). Visitor mode: Operate. Audience and job: mandolin-family players at a desk turning sheet music into a tab they trust (see PRODUCT.md). Constraints: offline (self-hosted fonts only), keep every element id and behaviour in static/app.js, notation (OSMD) renders black on white.

## Direction contract

THESIS: The app wears the instrument. A Loar-era F-5's sunburst, binding and pearl frame the work. It refuses both the dark-glass AI dashboard and the plain white card stack.
OWN-WORLD: A tobacco-to-amber sunburst lacquer field carries the chrome (header band, job band). Ivoroid binding with black-white-black purfling edges every panel. The tab sits on an ebony fingerboard with nickel frets and pearl dots, and score sheets read on clean white. Big Shoulders Display sets the lettering, the system UI font the body, Consolas the tab numerals. Amber is reserved for the active hand position.
STORY: The player drops their music, watches each staff line get read and stamped, and leaves with a tab they trust, re-cut live for their instrument.
FIRST VIEWPORT: A sunburst header band (wordmark left, AI status right) over one bound drop panel. Engine and arrangement controls form a row beneath. A fingerboard rail along the panel's foot previews the arrangement.
FORM: Sunburst & Pearl, my #1 grounded candidate, chosen as the pick card; seed d5da7230. Raises kept: per-system progress ticks, never-disappearing stamps, numbered result sheets, one reserved amber ink, live re-cut readout, hairline structure.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

Signature interaction: the fingerboard rail. Hovering or playing a tab note lights its fret in pearl, with the hand position in amber; arrangement changes re-cut the rail and tab live. Motion grammar: one authored moment, progress ticks stamping in per staff system; everything else is instant.
