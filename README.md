# NeverWrite (Prototype)

NeverWrite reproduces a person's own handwriting for wedding envelopes, thank-you notes and gift cards. It turns photographed template sheets into a personal glyph library, then writes any message onto cards and envelopes using a deterministic seeded layout with natural jitter. A message template plus a CSV of names produces a whole set of cards, each in slightly different handwriting.

---

## 🛠️ Tech Stack

- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, Zustand, Lucide React
- **Backend**: FastAPI, SQLite (via sync SQLAlchemy), Python Background Tasks
- **Workers**: OpenCV (extraction pipeline) & ReportLab (PDF renderer)
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
│   │   └── routers/          # /api/profiles, /scans, /glyphs, /renders, /render-batches, /jobs, /templates
│   ├── workers/
│   │   ├── extraction.py     # OpenCV cell extraction & glyph measurement pipeline
│   │   ├── renderer.py       # Layout (a port of layout.ts) and ReportLab PDF renderer
│   │   └── vectorizer.py     # Potrace/SVG converter stub
│   ├── tests/                # Layout parity test: preview vs PDF
│   ├── requirements.txt
│   ├── requirements-dev.txt  # adds pytest
│   └── run.py                # Server runner
├── frontend/
│   ├── src/
│   │   ├── lib/
│   │   │   ├── api.ts        # Typed API client
│   │   │   ├── prng.ts       # Mulberry32 seeded RNG
│   │   │   ├── layout.ts     # Layout engine & canvas drawing
│   │   │   └── merge.ts      # CSV parsing and {{placeholder}} filling
│   │   ├── store/            # Zustand store, saved per profile in localStorage
│   │   ├── pages/            # Landing, Profiles, Template, Capture, Processing, GlyphReview, Composer, Export
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

---

## 🔄 User Workflow in Prototype

1. **Profiles**: Create a new handwriting profile from `/profiles`.
2. **Template**: Review writing guidelines & download printable sheets.
3. **Scan/Capture**: Upload a photo or scan of your template sheet.
4. **Processing**: OpenCV extracts each character cell, trims margins, and measures baseline/bearing metrics.
5. **Review**: Inspect extracted glyphs, adjust baseline offsets or bearings, and accept/reject variants.
6. **Composer**: Write a card with a live preview:
   - Sizes: A6 card, A7 card, DL envelope (landscape), A4 page, or a custom size
   - Left or centred lines; words too long for a line break between letters
   - Slant, size, and wobble jitter; margins, ink colour and line spacing
   - Cards must fit on one page: text that runs over is flagged and cannot be exported
   - Characters with no glyph are listed before export (they would print as gaps)
   - Text, settings, seeds and mail-merge data are saved in the browser per profile
7. **Mail merge**: Switch the Composer to Mail merge, write a template with `{{column}}` placeholders, and upload a CSV (try `samples/guests.csv`). Every card appears in a preview grid with its own seed; reroll any card on its own.
8. **Export**: One card exports as a PDF. A mail-merged set exports as one PDF with a card per page, plus a zip with a PDF per card. The PDF is ink only, for printing on card stock, and matches the preview exactly.
