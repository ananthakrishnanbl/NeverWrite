import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Shuffle,
  Download,
  ZoomIn,
  ZoomOut,
  FileText,
  Settings,
  Eye,
  Upload,
  X,
} from 'lucide-react'
import {
  getGlyphs,
  getProfile,
  profileXHeight,
  startBatch,
  startRender,
  Glyph,
  PageSize,
  Profile,
  RenderSettings,
} from '../lib/api'
import {
  isSinglePage,
  layoutText,
  missingChars,
  renderToCanvas,
  LayoutResult,
  PAGE_PRESETS,
} from '../lib/layout'
import { fillTemplate, parseCsv, placeholders, resolveMapping } from '../lib/merge'
import { newSeed, useDraft } from '../store'

type MobileTab = 'text' | 'preview' | 'settings'

const DESKTOP_QUERY = '(min-width: 768px)'

// Cards per set; the backend allows the same
const MAX_CARDS = 200

// Backing pixels per CSS pixel for the large preview, so zooming in stays sharp
const PREVIEW_RATIO = 2

const SIZE_ORDER: PageSize[] = ['A6', 'A7', 'DL', 'A4', 'custom']

/** Tracks the md breakpoint so only one layout (and one canvas) is mounted. */
function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(() => window.matchMedia(DESKTOP_QUERY).matches)
  useEffect(() => {
    const mql = window.matchMedia(DESKTOP_QUERY)
    const onChange = () => setIsDesktop(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])
  return isDesktop
}

function sizeLabel(s: RenderSettings): string {
  return s.page_size === 'custom'
    ? `${s.page_width_mm} × ${s.page_height_mm} mm card`
    : PAGE_PRESETS[s.page_size].label
}

/** Zoom that shows the whole page in a typical preview area. */
function fitZoom(s: RenderSettings): number {
  const pxPerMm = 96 / 25.4
  const z = Math.min(640 / (s.page_height_mm * pxPerMm), 700 / (s.page_width_mm * pxPerMm))
  return Math.max(0.2, Math.min(2, Math.round(z * 10) / 10))
}

/** Show invisible characters by code point so the list is readable. */
function showChar(ch: string): string {
  return /\s/.test(ch) ? `U+${ch.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}` : ch
}

function apiError(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}

interface Card {
  label: string
  text: string
  seed: number
  layout: LayoutResult
  fits: boolean
  missing: string[]
  empty: string[] // mapped placeholders whose CSV cell is blank
}

export default function Composer() {
  const { profileId } = useParams<{ profileId: string }>()
  const navigate = useNavigate()

  const {
    text, setText,
    settings, updateSettings,
    seed, shuffleSeed,
    merge, updateMerge, rerollCard, rerollAllCards,
  } = useDraft(profileId)

  const [glyphs, setGlyphs] = useState<Glyph[]>([])
  const [profile, setProfile] = useState<Profile | null>(null)
  const [pageIndex, setPageIndex] = useState(0)
  const [zoom, setZoom] = useState(() => fitZoom(settings))
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const [csvError, setCsvError] = useState<string | null>(null)
  const [openCard, setOpenCard] = useState<number | null>(null)
  const [mobileTab, setMobileTab] = useState<MobileTab>('text')
  const [loadError, setLoadError] = useState<string | null>(null)
  const isDesktop = useIsDesktop()

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imageCacheRef = useRef(new Map<string, HTMLImageElement>())

  // Load profile & glyphs once
  useEffect(() => {
    if (!profileId) return
    const id = Number(profileId)
    Promise.all([getGlyphs(id), getProfile(id)])
      .then(([g, p]) => { setGlyphs(g); setProfile(p) })
      .catch(() => setLoadError('Failed to load profile data.'))
  }, [profileId])

  // A new size gets a zoom that shows the whole card
  useEffect(() => {
    setZoom(fitZoom(settings))
  }, [settings.page_width_mm, settings.page_height_mm])

  const ready = glyphs.length > 0 && profile !== null
  const xHeight = profile ? profileXHeight(profile) : 0
  const singlePage = isSinglePage(settings)
  const doesntFit = `Doesn't fit on one ${sizeLabel(settings)}. Shorten the text, lower the font size, line spacing or margins, or pick a bigger size.`

  // ── One card ────────────────────────────────────────────────────────────────
  const layout = useMemo(
    () => (ready && !merge.enabled ? layoutText(text, glyphs, settings, xHeight, seed) : null),
    [ready, merge.enabled, text, glyphs, settings, xHeight, seed]
  )
  // Draw from a deferred copy so typing never waits on the canvas
  const shownLayout = useDeferredValue(layout)
  const pageCount = layout?.pages.length ?? 1
  const page = Math.min(pageIndex, pageCount - 1)
  const missing = useMemo(() => (ready ? missingChars(text, glyphs) : []), [ready, text, glyphs])

  useEffect(() => {
    if (!shownLayout || !canvasRef.current) return
    renderToCanvas(canvasRef.current, shownLayout, page, settings, imageCacheRef.current, PREVIEW_RATIO)
  }, [shownLayout, page, settings, isDesktop, mobileTab])

  // ── Mail merge ──────────────────────────────────────────────────────────────
  const names = useMemo(() => placeholders(text), [text])
  const mapping = useMemo(
    () => resolveMapping(names, merge.columns, merge.mapping),
    [names, merge.columns, merge.mapping]
  )
  const unmapped = names.filter((n) => mapping[n] < 0)

  const cards = useMemo<Card[]>(() => {
    if (!ready || !merge.enabled) return []
    const labelCol = names.map((n) => mapping[n]).find((c) => c >= 0)
    return merge.rows.map((row, i) => {
      const cardText = fillTemplate(text, row, mapping)
      const cardSeed = merge.seeds[i] ?? 0
      const cardLayout = layoutText(cardText, glyphs, settings, xHeight, cardSeed)
      return {
        label: labelCol !== undefined ? (row[labelCol] ?? '').trim() : '',
        text: cardText,
        seed: cardSeed,
        layout: cardLayout,
        fits: !singlePage || cardLayout.pages.length <= 1,
        missing: missingChars(cardText, glyphs),
        empty: names.filter((n) => mapping[n] >= 0 && !(row[mapping[n]] ?? '').trim()),
      }
    })
  }, [ready, merge.enabled, merge.rows, merge.seeds, names, mapping, text, glyphs, settings, xHeight, singlePage])
  const shownCards = useDeferredValue(cards)

  const mergeMissing = useMemo(() => [...new Set(cards.flatMap((c) => c.missing))], [cards])

  // ── Export checks: problems block export, warnings only inform ───────────────
  const problems: string[] = []
  if (!ready) problems.push(loadError ?? 'Loading the handwriting profile…')
  else if (!merge.enabled) {
    if (!text.trim()) problems.push('Type a message first.')
    else if (singlePage && pageCount > 1) problems.push(doesntFit)
  } else {
    if (merge.rows.length === 0) problems.push('Upload a CSV with a header row and one row per card.')
    if (unmapped.length > 0) {
      problems.push(`Pick a CSV column for ${unmapped.map((n) => `{{${n}}}`).join(', ')}.`)
    }
    const tooLong = cards.flatMap((c, i) => (c.fits ? [] : [i + 1]))
    if (tooLong.length > 0) {
      problems.push(`${tooLong.length === 1 ? 'Card' : 'Cards'} ${tooLong.join(', ')}: ${doesntFit}`)
    }
    const blank = cards.flatMap((c, i) => (c.text.trim() ? [] : [i + 1]))
    if (blank.length > 0) problems.push(`Card ${blank.join(', ')} would be blank.`)
  }
  const shownMissing = merge.enabled ? mergeMissing : missing

  const handleExport = async () => {
    if (!profileId || problems.length > 0) return
    setExporting(true)
    setExportError(null)
    try {
      if (merge.enabled) {
        const { batch_id } = await startBatch(Number(profileId), {
          settings,
          cards: cards.map((c) => ({ label: c.label, text: c.text, seed: c.seed })),
        })
        navigate(`/profiles/${profileId}/export/batch/${batch_id}`)
      } else {
        const { render_id } = await startRender(Number(profileId), { text, settings, seed })
        navigate(`/profiles/${profileId}/export/${render_id}`)
      }
    } catch (err) {
      setExportError(apiError(err, 'Export failed. Is the API running?'))
      setExporting(false)
    }
  }

  const handleCsv = async (file: File) => {
    setCsvError(null)
    try {
      const { columns, rows } = parseCsv(await file.text())
      if (columns.length === 0) throw new Error('That file is empty.')
      if (rows.length === 0) throw new Error('The CSV has a header row but no rows for cards.')
      if (rows.length > MAX_CARDS) {
        throw new Error(`That CSV has ${rows.length} rows; a set can have up to ${MAX_CARDS} cards.`)
      }
      updateMerge({ fileName: file.name, columns, rows, mapping: {}, seeds: rows.map(() => newSeed()) })
    } catch (e) {
      setCsvError(e instanceof Error ? e.message : 'Could not read that CSV.')
    }
  }

  const charCount = Array.from(text).length
  const cardCountLabel = merge.enabled
    ? `${merge.rows.length} card${merge.rows.length !== 1 ? 's' : ''}`
    : `${pageCount} page${pageCount !== 1 ? 's' : ''}`

  // These panels are plain JSX, not components declared inside Composer: a
  // component declared here is a new type on every render, so React would
  // remount it on each keystroke (textarea loses focus, sliders drop the drag).

  // ── Mode switch ────────────────────────────────────────────────────────────
  const modeSwitch = (
    <div className="grid grid-cols-2 gap-1 bg-ink-50 border border-ink-100 rounded-lg p-1">
      {([
        [false, 'One card'],
        [true, 'Mail merge'],
      ] as const).map(([enabled, label]) => (
        <button
          key={label}
          onClick={() => updateMerge({ enabled })}
          className={`py-1.5 rounded-md text-sm transition-all ${
            merge.enabled === enabled ? 'bg-white shadow-sm text-ink-800 font-medium' : 'text-ink-500 hover:text-ink-700'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  )

  // ── Message editor ──────────────────────────────────────────────────────────
  const textEditor = (
    <>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="flex-1 min-h-[10rem] w-full border border-ink-200 rounded-xl px-4 py-3 text-ink-900 placeholder-ink-400 focus:outline-none focus:border-ink-500 resize-none text-sm leading-relaxed"
        placeholder={merge.enabled ? 'Dear {{name}},\n\nThank you for…' : 'Type your message here…'}
      />
      <div className="flex justify-between text-xs text-ink-400">
        <span>{charCount} characters</span>
        <span>{cardCountLabel}</span>
      </div>
      {!merge.enabled && names.length > 0 && (
        <p className="text-xs text-ink-500">
          This message has placeholders. Switch to Mail merge to fill them from a CSV.
        </p>
      )}
    </>
  )

  // ── Mail-merge data: CSV and placeholder mapping ────────────────────────────
  const mergePanel = (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-1.5">Recipients (CSV)</label>
        <label className="flex items-center justify-center gap-2 border border-dashed border-ink-300 rounded-lg py-2 text-sm text-ink-600 hover:bg-ink-50 cursor-pointer transition-colors">
          <Upload className="w-4 h-4" />
          {merge.fileName ? 'Replace CSV' : 'Upload CSV'}
          <input
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = '' // so choosing the same file again still fires
              if (file) handleCsv(file)
            }}
          />
        </label>
        {merge.fileName && (
          <p className="text-xs text-ink-500 mt-1.5 break-words">
            {merge.fileName}: {merge.rows.length} row{merge.rows.length !== 1 ? 's' : ''}, columns {merge.columns.join(', ')}
          </p>
        )}
        {csvError && <p className="text-xs text-red-600 mt-1.5">{csvError}</p>}
      </div>

      {names.length === 0 ? (
        <p className="text-xs text-ink-500">
          Add placeholders such as <code className="bg-ink-50 px-1 rounded">{'{{name}}'}</code> to the message.
          Each card fills them from its row of the CSV.
        </p>
      ) : (
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block">Placeholders</label>
          {names.map((n) => (
            <div key={n} className="flex items-center gap-2 text-sm">
              <code className="bg-ink-50 border border-ink-100 px-1.5 py-0.5 rounded text-xs text-ink-700 truncate max-w-[45%]">
                {`{{${n}}}`}
              </code>
              <span className="text-ink-400">→</span>
              <select
                value={mapping[n]}
                onChange={(e) => updateMerge({ mapping: { ...merge.mapping, [n]: Number(e.target.value) } })}
                className={`flex-1 min-w-0 border rounded-md px-2 py-1 text-sm bg-white ${
                  mapping[n] < 0 ? 'border-red-300 text-red-600' : 'border-ink-200 text-ink-700'
                }`}
              >
                <option value={-1}>Not mapped</option>
                {merge.columns.map((c, i) => (
                  <option key={i} value={i}>{c || `Column ${i + 1}`}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}
    </div>
  )

  // ── Export button with what's blocking it and what to watch for ──────────────
  const exportBlock = (
    <div className="space-y-2">
      {problems.length > 0 && ready && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700 space-y-1">
          {problems.map((p) => <p key={p}>{p}</p>)}
        </div>
      )}
      {shownMissing.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-xs text-amber-800">
          <p className="font-medium mb-1 flex items-center gap-1">
            <AlertTriangle className="w-3.5 h-3.5" /> No handwriting for these characters
          </p>
          <p className="font-mono text-sm tracking-widest break-all">{shownMissing.map(showChar).join(' ')}</p>
          <p className="mt-1">They print as gaps. Rephrase, or add them in glyph review.</p>
        </div>
      )}
      {exportError && <p className="text-xs text-red-600">{exportError}</p>}
      <button
        onClick={handleExport}
        disabled={exporting || problems.length > 0}
        className="w-full flex items-center justify-center gap-2 bg-ink-800 text-paper py-3 rounded-xl font-semibold hover:bg-ink-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        <Download className="w-4 h-4" />
        {exporting
          ? 'Preparing…'
          : !merge.enabled
            ? 'Export PDF'
            : merge.rows.length > 0
              ? `Export ${merge.rows.length} card${merge.rows.length !== 1 ? 's' : ''}`
              : 'Export cards'}
      </button>
    </div>
  )

  // ── Settings panel (shared between desktop right col and mobile tab) ────────
  const settingsPanel = (
    <div className="space-y-5 overflow-y-auto">
      {/* Card size */}
      <div>
        <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-2">Size</label>
        <div className="grid grid-cols-2 gap-1.5">
          {SIZE_ORDER.map((size) => (
            <button
              key={size}
              onClick={() =>
                updateSettings(
                  size === 'custom'
                    ? { page_size: 'custom' }
                    : { page_size: size, page_width_mm: PAGE_PRESETS[size].width, page_height_mm: PAGE_PRESETS[size].height }
                )
              }
              className={`py-1.5 px-2 rounded-lg text-left border-2 transition-all ${
                size === 'custom' ? 'col-span-2' : ''
              } ${
                settings.page_size === size
                  ? 'border-ink-600 bg-ink-50 text-ink-800'
                  : 'border-ink-100 text-ink-500 hover:border-ink-300'
              }`}
            >
              <span className="block text-sm font-medium">{size === 'custom' ? 'Custom' : PAGE_PRESETS[size].label}</span>
              {size !== 'custom' && (
                <span className="block text-[11px] text-ink-400">
                  {PAGE_PRESETS[size].width} × {PAGE_PRESETS[size].height} mm
                </span>
              )}
            </button>
          ))}
        </div>
        {settings.page_size === 'custom' && (
          <div className="grid grid-cols-2 gap-3 mt-2">
            {(['page_width_mm', 'page_height_mm'] as const).map((k) => (
              <label key={k} className="text-xs text-ink-600">
                {k === 'page_width_mm' ? 'Width' : 'Height'} (mm)
                <input
                  type="number"
                  min={30}
                  max={420}
                  value={settings[k]}
                  onChange={(e) => {
                    const v = Number(e.target.value)
                    if (v > 0) updateSettings({ [k]: v })
                  }}
                  onBlur={() => updateSettings({ [k]: Math.min(420, Math.max(30, settings[k])) })}
                  className="mt-1 w-full border border-ink-200 rounded-md px-2 py-1 text-sm"
                />
              </label>
            ))}
          </div>
        )}
        <p className="text-xs text-ink-400 mt-1.5">
          {singlePage ? 'Cards and envelopes take one page; text that runs over is flagged.' : 'A4 runs onto more pages as needed.'}
        </p>
      </div>

      {/* Alignment */}
      <div>
        <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-2">Alignment</label>
        <div className="grid grid-cols-2 gap-1.5">
          {([['left', 'Left'], ['center', 'Centre']] as const).map(([value, label]) => (
            <button
              key={value}
              onClick={() => updateSettings({ align: value })}
              className={`py-1.5 rounded-lg text-sm border-2 transition-all ${
                settings.align === value
                  ? 'border-ink-600 bg-ink-50 text-ink-800 font-medium'
                  : 'border-ink-100 text-ink-500 hover:border-ink-300'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <Slider label="Font size" unit="pt" value={settings.font_size} min={6} max={24} step={0.5}
        onChange={(v) => updateSettings({ font_size: v })} />
      <Slider label="Line spacing" unit="×" value={settings.line_spacing} min={1.5} max={4.0} step={0.05}
        onChange={(v) => updateSettings({ line_spacing: v })} />

      {/* Ink color */}
      <div>
        <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-1.5">Ink colour</label>
        <div className="flex items-center gap-3">
          <input
            type="color"
            value={settings.ink_color}
            onChange={(e) => updateSettings({ ink_color: e.target.value })}
            className="w-10 h-10 rounded-lg border border-ink-200 cursor-pointer"
          />
          <span className="text-sm font-mono text-ink-500">{settings.ink_color}</span>
        </div>
      </div>

      {/* Margins */}
      <div>
        <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-2">Margins (mm)</label>
        <div className="grid grid-cols-2 gap-3">
          {(['margin_top', 'margin_bottom', 'margin_left', 'margin_right'] as const).map((k) => (
            <Slider key={k} label={k.replace('margin_', '')} unit="mm"
              value={settings[k]} min={4} max={60} step={1}
              onChange={(v) => updateSettings({ [k]: v })} compact />
          ))}
        </div>
      </div>

      <Slider label="Messiness" unit="%" value={settings.messiness} min={0} max={100} step={1}
        onChange={(v) => updateSettings({ messiness: v })} />
      <Slider label="Slant correction" unit="°" value={settings.slant_correction} min={-30} max={30} step={1}
        onChange={(v) => updateSettings({ slant_correction: v })} />

      {/* Seed */}
      {merge.enabled ? (
        <div>
          <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-1.5">Seeds</label>
          <p className="text-xs text-ink-500 mb-2">Each card has its own seed. Reroll one on its card, or all here.</p>
          <button
            onClick={rerollAllCards}
            disabled={merge.rows.length === 0}
            className="flex items-center gap-1.5 border border-ink-200 text-ink-600 px-3 py-2 rounded-lg text-sm hover:bg-ink-50 disabled:opacity-40 transition-colors"
          >
            <Shuffle className="w-3.5 h-3.5" />
            Reroll every card
          </button>
        </div>
      ) : (
        <div>
          <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-1.5">Seed</label>
          <div className="flex items-center gap-2">
            <span className="flex-1 bg-ink-50 border border-ink-100 rounded-lg px-3 py-2 text-sm font-mono text-ink-500 truncate">
              {seed.toString(16).padStart(8, '0')}
            </span>
            <button
              onClick={shuffleSeed}
              className="flex items-center gap-1.5 border border-ink-200 text-ink-600 px-3 py-2 rounded-lg text-sm hover:bg-ink-50 transition-colors"
              title="Shuffle seed"
            >
              <Shuffle className="w-3.5 h-3.5" />
              Shuffle
            </button>
          </div>
        </div>
      )}
    </div>
  )

  // ── Canvas panel (one card) ─────────────────────────────────────────────────
  const canvasPanel = (
    <div className="flex flex-col h-full">
      {/* Zoom & page nav */}
      <div className="flex items-center justify-between gap-2 mb-3 flex-shrink-0">
        <div className="flex items-center gap-1">
          <button onClick={() => setZoom((z) => Math.max(0.2, z - 0.1))} className="p-1.5 rounded-lg hover:bg-ink-100 text-ink-600 transition-colors">
            <ZoomOut className="w-4 h-4" />
          </button>
          <span className="text-xs text-ink-500 font-mono w-12 text-center">{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom((z) => Math.min(3, z + 0.1))} className="p-1.5 rounded-lg hover:bg-ink-100 text-ink-600 transition-colors">
            <ZoomIn className="w-4 h-4" />
          </button>
        </div>
        {pageCount > 1 && (
          <div className="flex items-center gap-2 text-sm text-ink-600">
            <button
              onClick={() => setPageIndex(Math.max(0, page - 1))}
              disabled={page === 0}
              className="p-1.5 rounded-lg hover:bg-ink-100 disabled:opacity-30 transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className={`text-xs font-mono ${singlePage ? 'text-red-600' : ''}`}>
              {singlePage ? 'overflow ' : ''}{page + 1} / {pageCount}
            </span>
            <button
              onClick={() => setPageIndex(Math.min(pageCount - 1, page + 1))}
              disabled={page >= pageCount - 1}
              className="p-1.5 rounded-lg hover:bg-ink-100 disabled:opacity-30 transition-colors"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>

      {singlePage && pageCount > 1 && (
        <div className="mb-3 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm flex gap-2 flex-shrink-0">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{doesntFit}</span>
        </div>
      )}

      {/* Canvas scroll area */}
      <div className="flex-1 overflow-auto flex items-start bg-ink-100 rounded-xl relative min-h-0">
        {loadError && (
          <div className="absolute inset-0 flex items-center justify-center text-red-500 text-sm p-4 text-center">{loadError}</div>
        )}
        {/* Size this box to the zoomed page so the scroll area can reach every
            edge. A bare transform keeps the unscaled size, and centring that
            pushes the page's left side out of reach. mx-auto centres it only
            when it fits. */}
        <div
          className="mx-auto shrink-0"
          style={{
            width: (layout?.pageWidthPx ?? 0) * zoom + 32,
            height: (layout?.pageHeightPx ?? 0) * zoom + 32,
            padding: 16,
          }}
        >
          <div style={{ transform: `scale(${zoom})`, transformOrigin: 'top left' }}>
            <canvas
              ref={canvasRef}
              className="shadow-xl rounded"
              style={{
                display: 'block',
                maxWidth: 'none',
                width: layout?.pageWidthPx ?? 0,
                height: layout?.pageHeightPx ?? 0,
              }}
            />
          </div>
        </div>
      </div>
    </div>
  )

  // ── Card grid (mail merge) ──────────────────────────────────────────────────
  const landscape = settings.page_width_mm > settings.page_height_mm
  const gridPanel = (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between gap-2 mb-3 flex-shrink-0 text-sm text-ink-600">
        <span>
          {cards.length > 0
            ? `${cards.length} card${cards.length !== 1 ? 's' : ''} · ${sizeLabel(settings)}`
            : 'No cards yet'}
        </span>
        {cards.length > 0 && <span className="text-xs text-ink-400">Click a card to see it larger</span>}
      </div>
      <div className="flex-1 overflow-auto bg-ink-100 rounded-xl p-4 min-h-0">
        {loadError ? (
          <p className="text-red-500 text-sm text-center py-16">{loadError}</p>
        ) : cards.length === 0 ? (
          <div className="text-center text-sm text-ink-500 py-16 max-w-sm mx-auto">
            <p className="font-medium text-ink-700 mb-1">Upload a CSV to see every card here</p>
            <p>Use a header row, such as <code className="bg-white px-1 rounded">name</code>, and one row per card.
              Each <code className="bg-white px-1 rounded">{'{{name}}'}</code> in the message is filled from that column.</p>
          </div>
        ) : (
          <div
            className="grid gap-4"
            style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${landscape ? 280 : 180}px, 1fr))` }}
          >
            {shownCards.map((card, i) => (
              <CardPreview
                key={i}
                index={i}
                card={card}
                settings={settings}
                imageCache={imageCacheRef.current}
                onReroll={() => rerollCard(i)}
                onOpen={() => setOpenCard(i)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )

  const previewPanel = merge.enabled ? gridPanel : canvasPanel

  const enlarged = openCard !== null && cards[openCard] ? (
    <CardLightbox
      index={openCard}
      count={cards.length}
      card={cards[openCard]}
      settings={settings}
      imageCache={imageCacheRef.current}
      onReroll={() => rerollCard(openCard)}
      onMove={(i) => setOpenCard(i)}
      onClose={() => setOpenCard(null)}
    />
  ) : null

  // ── MOBILE layout ─────────────────────────────────────────────────────────
  const mobileLayout = (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] md:hidden">
      <div className="flex-1 overflow-hidden p-3">
        {mobileTab === 'text' && (
          <div className="h-full flex flex-col gap-3 overflow-y-auto">
            {modeSwitch}
            {textEditor}
            {merge.enabled && mergePanel}
          </div>
        )}
        {mobileTab === 'preview' && previewPanel}
        {mobileTab === 'settings' && (
          <div className="h-full overflow-y-auto">
            {settingsPanel}
          </div>
        )}
      </div>
      {/* Mobile bottom tabs */}
      <div className="border-t border-ink-100 bg-white flex">
        {([
          { id: 'text', icon: <FileText className="w-5 h-5" />, label: 'Text' },
          { id: 'preview', icon: <Eye className="w-5 h-5" />, label: 'Preview' },
          { id: 'settings', icon: <Settings className="w-5 h-5" />, label: 'Settings' },
        ] as { id: MobileTab; icon: React.ReactNode; label: string }[]).map((tab) => (
          <button
            key={tab.id}
            onClick={() => setMobileTab(tab.id)}
            className={`flex-1 flex flex-col items-center gap-1 py-3 text-xs font-medium transition-colors ${
              mobileTab === tab.id ? 'text-ink-800' : 'text-ink-400'
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>
      <div className="p-3 border-t border-ink-100">
        {exportBlock}
      </div>
      {enlarged}
    </div>
  )

  // ── DESKTOP layout ────────────────────────────────────────────────────────
  const desktopLayout = (
    <div className="hidden md:flex h-[calc(100vh-3.5rem)] overflow-hidden">
      {/* Left: text input */}
      <div className="w-80 flex flex-col border-r border-ink-100 p-5 gap-4 flex-shrink-0 overflow-y-auto">
        {modeSwitch}
        <h2 className="font-semibold text-ink-800">{merge.enabled ? 'Message template' : 'Your message'}</h2>
        {textEditor}
        {merge.enabled && mergePanel}
        {exportBlock}
      </div>

      {/* Center: preview */}
      <div className="flex-1 p-5 min-w-0 flex flex-col">
        {previewPanel}
      </div>

      {/* Right: settings */}
      <div className="w-72 border-l border-ink-100 p-5 flex-shrink-0 overflow-y-auto">
        <h2 className="font-semibold text-ink-800 mb-4">Settings</h2>
        {settingsPanel}
      </div>
      {enlarged}
    </div>
  )

  return isDesktop ? desktopLayout : mobileLayout
}

// ── One card in the mail-merge grid ───────────────────────────────────────────
function CardPreview({
  index, card, settings, imageCache, onReroll, onOpen,
}: {
  index: number
  card: Card
  settings: RenderSettings
  imageCache: Map<string, HTMLImageElement>
  onReroll: () => void
  onOpen: () => void
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    if (ref.current) renderToCanvas(ref.current, card.layout, 0, settings, imageCache)
  }, [card.layout, settings, imageCache])

  return (
    <div className={`bg-white rounded-xl p-2 border-2 ${card.fits ? 'border-transparent' : 'border-red-300'}`}>
      <button onClick={onOpen} className="block w-full" title="See this card larger">
        <canvas ref={ref} className="block w-full h-auto rounded shadow" />
      </button>
      <div className="flex items-center justify-between gap-2 mt-2">
        <div className="min-w-0">
          <p className="text-sm text-ink-800 truncate">
            <span className="text-ink-400 mr-1">{index + 1}.</span>{card.label || `Card ${index + 1}`}
          </p>
          <p className="text-[11px] font-mono text-ink-400">{card.seed.toString(16).padStart(8, '0')}</p>
        </div>
        <button
          onClick={onReroll}
          className="flex items-center gap-1 border border-ink-200 text-ink-600 px-2 py-1 rounded-md text-xs hover:bg-ink-50 transition-colors shrink-0"
          title="New handwriting for this card only"
        >
          <Shuffle className="w-3 h-3" />
          Reroll
        </button>
      </div>
      <CardIssues card={card} />
    </div>
  )
}

function CardIssues({ card }: { card: Card }) {
  return (
    <>
      {!card.fits && <p className="text-xs text-red-600 mt-1">Doesn't fit on one card</p>}
      {card.empty.length > 0 && (
        <p className="text-xs text-amber-700 mt-1">Blank in CSV: {card.empty.map((n) => `{{${n}}}`).join(', ')}</p>
      )}
      {card.missing.length > 0 && (
        <p className="text-xs text-amber-700 mt-1">No glyph: {card.missing.map(showChar).join(' ')}</p>
      )}
    </>
  )
}

// ── A card at full size, with arrows to step through the set ──────────────────
function CardLightbox({
  index, count, card, settings, imageCache, onReroll, onMove, onClose,
}: {
  index: number
  count: number
  card: Card
  settings: RenderSettings
  imageCache: Map<string, HTMLImageElement>
  onReroll: () => void
  onMove: (index: number) => void
  onClose: () => void
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    if (ref.current) renderToCanvas(ref.current, card.layout, 0, settings, imageCache, PREVIEW_RATIO)
  }, [card.layout, settings, imageCache])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowLeft' && index > 0) onMove(index - 1)
      if (e.key === 'ArrowRight' && index < count - 1) onMove(index + 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [index, count, onMove, onClose])

  const { pageWidthPx: w, pageHeightPx: h } = card.layout
  return (
    <div className="fixed inset-0 z-50 bg-ink-900/70 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl p-4 max-w-full max-h-full flex flex-col gap-3" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-ink-800">
            <span className="text-ink-400 mr-1">{index + 1} / {count}</span> {card.label || `Card ${index + 1}`}
            <span className="font-mono text-xs text-ink-400 ml-2">{card.seed.toString(16).padStart(8, '0')}</span>
          </p>
          <div className="flex items-center gap-1">
            <button onClick={() => onMove(index - 1)} disabled={index === 0} className="p-1.5 rounded-lg hover:bg-ink-100 disabled:opacity-30">
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button onClick={() => onMove(index + 1)} disabled={index >= count - 1} className="p-1.5 rounded-lg hover:bg-ink-100 disabled:opacity-30">
              <ChevronRight className="w-4 h-4" />
            </button>
            <button onClick={onReroll} className="flex items-center gap-1 border border-ink-200 text-ink-600 px-2 py-1 rounded-md text-xs hover:bg-ink-50 ml-1">
              <Shuffle className="w-3 h-3" /> Reroll
            </button>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-ink-100 ml-1" title="Close">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
        <canvas
          ref={ref}
          className="block rounded shadow-lg mx-auto"
          style={{
            width: `min(${w}px * 1.6, 90vw, calc((100vh - 9rem) * ${w / h}))`,
            aspectRatio: `${w} / ${h}`,
          }}
        />
        <CardIssues card={card} />
      </div>
    </div>
  )
}

// ── Slider helper ──────────────────────────────────────────────────────────────
function Slider({
  label, unit, value, min, max, step, onChange, compact = false,
}: {
  label: string
  unit: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
  compact?: boolean
}) {
  return (
    <div>
      <div className="flex justify-between text-xs text-ink-600 mb-1">
        <span className={compact ? 'capitalize' : 'font-medium uppercase tracking-wider'}>{label}</span>
        <span className="font-mono">{typeof value === 'number' && value % 1 !== 0 ? value.toFixed(2) : value}{unit}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-ink-700"
      />
    </div>
  )
}
