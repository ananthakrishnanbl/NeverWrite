# NeverWrite (Prototype)

NeverWrite reproduces a person's own handwriting for wedding envelopes, thank-you notes and gift cards. It turns photographed template sheets (or letters drawn in the browser) into a personal glyph library, then writes any message onto cards and envelopes using a deterministic seeded layout with natural jitter. A message template plus a CSV of names produces a whole set of cards, each in slightly different handwriting. Cards export as a PDF for printing, or as SVG, G-code or HPGL for a pen plotter.

---

## 🛠️ Tech Stack

- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, Zustand, Lucide React
- **Backend**: FastAPI, SQLite (via sync SQLAlchemy), Python Background Tasks
- **Workers**: OpenCV (extraction pipeline), ReportLab (PDF renderer) & a pen plotter writer (SVG, G-code, HPGL)
- **Deterministic RNG**: Mulberry32 PRNG (the preview and the PDF place every glyph identically for a given seed)

---

## 📂 Project Structure

```
NeverWrite/
├── backend/
│   ├── app/
│   │   ├── config.py         # App directories, SQLite DB path, CORS
│   │   ├── database.py       # SQLAlchemy engine, session factory, column migrations
│   │   ├── jobs.py           # Thread-safe in-memory job tracker
│   │   ├── storage.py        # Local file management under data/
│   │   ├── main.py           # FastAPI entry point & static file mount
│   │   ├── models/           # Profile, Glyph, RenderJob, RenderBatch ORM models
│   │   ├── schemas/          # Pydantic schemas
│   │   └── routers/          # /api/profiles, /scans, /glyphs, /drawn-glyphs, /renders, /render-batches, /plot, /jobs, /templates
│   ├── workers/
│   │   ├── extraction.py     # OpenCV cell extraction & glyph measurement pipeline
│   │   ├── renderer.py       # Layout (a port of layout.ts) and ReportLab PDF renderer
│   │   ├── strokes.py        # Letters drawn in the browser: clean-up, crop, mask
│   │   ├── tracing.py        # Pen paths for scanned letters, traced through the middle of the ink
│   │   ├── plotter.py        # Plot files from pen paths: SVG, G-code, HPGL, plot time
│   │   ├── plot_sim.py       # Replays G-code or HPGL onto an image, to check a file without a plotter
│   │   └── vectorizer.py     # Potrace/SVG converter stub (unused)
│   ├── tests/                # Layout parity, template capture, drawn letters, tracing and plot files
│   ├── requirements.txt
│   ├── requirements-dev.txt  # adds pytest
│   └── run.py                # Server runner
├── frontend/
│   ├── src/
│   │   ├── lib/
│   │   │   ├── api.ts        # Typed API client
│   │   │   ├── prng.ts       # Mulberry32 seeded RNG
│   │   │   ├── layout.ts     # Layout engine & canvas drawing
│   │   │   ├── merge.ts      # CSV parsing and {{placeholder}} filling
│   │   │   ├── draw.ts       # The Draw page's writing box
│   │   │   └── plot.ts       # Plot formats and machine settings
│   │   ├── components/       # Layout, PlotPanel (plot export on the Export pages)
│   │   ├── store/            # Zustand store, saved per profile in localStorage
│   │   ├── pages/            # Landing, Profiles, Template, Capture, Draw, Processing, GlyphReview, Composer, Export
│   │   └── App.tsx           # Route tree & navigation
│   └── vite.config.ts        # Vite dev server with proxy to /api & /files
└── samples/
    └── guests.csv            # 10 sample names for trying mail merge
```

---

## 🚀 Getting Started

### 1. Backend Setup

1. Open a terminal in `backend`:
   ```powershell
   cd NeverWrite\backend
   ```
2. Install Python dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
   *(Note: SQLite is built into Python; no external database server is required)*
3. Run the backend server:
   ```powershell
   python run.py
   ```
   The backend API will run at **`http://localhost:8000`** (Swagger docs at `http://localhost:8000/docs`).

### 2. Frontend Setup

1. Open a second terminal in `frontend`:
   ```powershell
   cd NeverWrite\frontend
   ```
2. Install dependencies and start the Vite dev server:
   ```powershell
   npm install
   npm run dev
   ```
3. Open **`http://localhost:5173`** in your browser.

### 3. Tests

The PDF renderer must place every glyph exactly where the browser preview does. `backend/tests/test_layout_parity.py` runs `layoutText` from `layout.ts` (through Node 22's `--experimental-strip-types`) and `layout_text` from `renderer.py` on the same glyphs, settings and seeds, and fails on any difference. Run it after touching either layout:

```powershell
cd NeverWrite\backend
pip install -r requirements-dev.txt
python -m pytest tests
```

`backend/tests/test_plotter.py` checks the plot files: only unfilled single-line paths inside the card, G-code that starts and ends with the pen up and has no heater or extruder commands, the same bytes for the same seed, and a G-code replay that draws exactly the planned lines. `backend/tests/test_tracing.py` draws a test alphabet into glyph masks, traces them, and requires the traced paths to stay on the ink and within a pixel, on average, of the strokes that made them; it also covers faint joins, pinholes, ragged edges, crossings and dots.

---

## 🔄 User Workflow in Prototype

1. **Profiles**: Create a new handwriting profile from `/profiles`.
2. **Template**: Review writing guidelines & download printable sheets.
3. **Scan/Capture**: Upload a photo or scan of your template sheet. **Or draw**: on the Draw page, write each character three times in the browser (a stylus on a tablet looks closest to pen on paper). A drawn letter gives a plotter the exact path your pen took; a scanned one is traced.
4. **Processing**: OpenCV extracts each character cell, trims margins, and measures baseline/bearing metrics.
5. **Review**: Inspect extracted glyphs, adjust baseline offsets or bearings, and accept/reject variants. **Show pen paths** draws the line a plotter will follow over every glyph.
6. **Composer**: Write a card with a live preview:
   - Sizes: A6 card, A7 card, DL envelope (landscape), A4 page, or a custom size
   - Left or centred lines; words too long for a line break between letters
   - Slant, size, and wobble jitter; margins, ink colour and line spacing
   - Cards must fit on one page: text that runs over is flagged and cannot be exported
   - Characters with no glyph are listed before export (they would print as gaps)
   - Text, settings, seeds and mail-merge data are saved in the browser per profile
7. **Mail merge**: Switch the Composer to Mail merge, write a template with `{{column}}` placeholders, and upload a CSV (try `samples/guests.csv`). Every card appears in a preview grid with its own seed; reroll any card on its own.
8. **Export**: One card exports as a PDF. A mail-merged set exports as one PDF with a card per page, plus a zip with a PDF per card. The PDF is ink only, for printing on card stock, and matches the preview exactly.
9. **Plot**: The Export pages also give plot files for a pen plotter, laid out exactly like the PDF (see below).

---

## 🖊️ Pen plotter files

A plotter can only drag a pen along lines, so plot files follow each letter's pen path, never its image:

- **Drawn letters** (Draw page) use the exact path the pen took.
- **Scanned letters** are traced when the sheet is processed (older scans the first time they are plotted). `workers/tracing.py` thins the ink to its centreline, prunes the branches that ragged edges and blunt stroke ends leave, keeps sharp points sharp (the bottom of a v, the tops of an M), passes crossings straight through, joins strokes across faint gaps, and turns dots into a touch of the pen (or a small spiral for a big blob). The path follows the middle of the ink, so where strokes cross or loop the pen may take a slightly different route than the writer did. Glyph Review's **Show pen paths** shows every path; draw any letter whose path looks wrong.

| Format | For | Notes |
|---|---|---|
| SVG (`.svg`) | Inkscape, sign-shop software, most plotters | Millimetres, one unfilled path per pen stroke |
| G-code (`.gcode`) | A 3D printer with a pen fixed beside the nozzle | Marlin style. Lower the pen until it just touches the card, then start the file: it calls that height pen-down Z, lifts the pen and homes X and Y only. No heater, fan or extruder commands. |
| HPGL (`.plt`) | Vinyl cutters and older pen plotters | 40 units per mm. On a cutter, turn blade offset compensation off. |

Strokes are ordered so the pen travels as little as possible with the pen up, and anything past the card edge is cut. Each file comes with an estimated plot time: drawing distance over drawing speed, plus travel over travel speed, plus pen lifts. The default speeds are guesses: time one card on the real machine and set them on the Export page (they are remembered in the browser).

To see what a G-code or HPGL file would draw without a plotter:

```powershell
cd NeverWrite\backend
python -m workers.plot_sim card.gcode card.png --travel
```
