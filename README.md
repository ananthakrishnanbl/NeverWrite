# InkTwin (Prototype)

InkTwin digitises personal handwriting from printed template sheets into an individual glyph library, then synthesises realistic handwritten text onto A4 pages using deterministic seeded layout and geometric jitter.

---

## 🛠️ Tech Stack

- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, Zustand, Lucide React
- **Backend**: FastAPI, SQLite (via sync SQLAlchemy), Python Background Tasks
- **Workers**: OpenCV (extraction pipeline) & ReportLab (page vector typesetter)
- **Deterministic RNG**: Mulberry32 PRNG (consistent preview & export across client/server)

---

## 📂 Project Structure

```
d:\PROTO\inktwin\
├── backend\
│   ├── app\
│   │   ├── config.py         # App directories, SQLite DB path, CORS
│   │   ├── database.py       # SQLAlchemy engine & session factory
│   │   ├── jobs.py           # Thread-safe in-memory job tracker
│   │   ├── storage.py        # Local file management under data/
│   │   ├── main.py           # FastAPI entry point & static file mount
│   │   ├── models/           # Profile, Glyph, RenderJob ORM models
│   │   ├── schemas/          # Pydantic schemas
│   │   └── routers/          # API endpoints (/api/profiles, /scans, /glyphs, /renders, /jobs, /templates)
│   ├── workers/
│   │   ├── extraction.py     # OpenCV cell extraction & glyph measurement pipeline
│   │   ├── renderer.py       # Deterministic ReportLab layout & PDF renderer
│   │   └── vectorizer.py     # Potrace/SVG converter stub
│   ├── requirements.txt
│   └── run.py                # Server runner
└── frontend\
    ├── src\
    │   ├── lib/
    │   │   ├── api.ts        # Typed API client
    │   │   ├── prng.ts       # Mulberry32 seeded RNG
    │   │   └── layout.ts     # Canvas preview layout engine & drawing logic
    │   ├── store/            # Zustand global state (text, settings, seed)
    │   ├── pages/            # Landing, Profiles, Template, Capture, Processing, GlyphReview, Composer, Export
    │   └── App.tsx           # Route tree & navigation
    └── vite.config.ts        # Vite dev server with proxy to /api & /files
```

---

## 🚀 Getting Started

### 1. Backend Setup

1. Open a terminal in `backend`:
   ```powershell
   cd d:\PROTO\inktwin\backend
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
   cd d:\PROTO\inktwin\frontend
   ```
2. Dependencies are already installed (`node_modules` ready). To start the Vite dev server:
   ```powershell
   npm run dev
   ```
3. Open **`http://localhost:5173`** in your browser.

---

## 🔄 User Workflow in Prototype

1. **Profiles**: Create a new handwriting profile from `/profiles`.
2. **Template**: Review writing guidelines & download printable sheets.
3. **Scan/Capture**: Upload a photo or scan of your template sheet.
4. **Processing**: OpenCV extracts each character cell, trims margins, and measures baseline/bearing metrics.
5. **Review**: Inspect extracted glyphs, adjust baseline offsets or bearings, and accept/reject variants.
6. **Composer**: Type text with real-time A4 Canvas preview:
   - Dynamic word wrapping
   - Slant, size, and wobble jitter
   - Customizable margins, ink color, line spacing, and ruled/grid paper styles
   - Seed shuffle button for natural variation
7. **Export**: Generate high-resolution PDF output matching the live preview.
