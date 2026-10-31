import { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ChevronLeft,
  ChevronRight,
  Shuffle,
  Download,
  ZoomIn,
  ZoomOut,
  FileText,
  Settings,
  Eye,
} from 'lucide-react'
import { getGlyphs, getProfile, profileXHeight, startRender, Glyph, Profile } from '../lib/api'
import { layoutText, renderToCanvas, LayoutResult } from '../lib/layout'
import { useInkTwinStore } from '../store'

type MobileTab = 'text' | 'preview' | 'settings'

export default function Composer() {
  const { profileId } = useParams<{ profileId: string }>()
  const navigate = useNavigate()

  const {
    composerText, setComposerText,
    renderSettings, updateRenderSettings,
    seed, shuffleSeed,
  } = useInkTwinStore()

  const [glyphs, setGlyphs] = useState<Glyph[]>([])
  const [profile, setProfile] = useState<Profile | null>(null)
  const [layout, setLayout] = useState<LayoutResult | null>(null)
  const [pageIndex, setPageIndex] = useState(0)
  const [zoom, setZoom] = useState(0.5)
  const [rendering, setRendering] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [mobileTab, setMobileTab] = useState<MobileTab>('text')
  const [loadError, setLoadError] = useState<string | null>(null)

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imageCacheRef = useRef(new Map<string, HTMLImageElement>())
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load profile & glyphs once
  useEffect(() => {
    if (!profileId) return
    const id = Number(profileId)
    Promise.all([getGlyphs(id), getProfile(id)])
      .then(([g, p]) => { setGlyphs(g); setProfile(p) })
      .catch(() => setLoadError('Failed to load profile data.'))
  }, [profileId])

  // Debounced re-render
  const scheduleRender = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setRendering(true)
    }, 300)
  }, [])

  useEffect(() => { scheduleRender() }, [composerText, renderSettings, seed, scheduleRender])

  // Actually run layout + draw
  useEffect(() => {
    if (!rendering || glyphs.length === 0 || !profile) return
    const xHeight = profileXHeight(profile)
    const result = layoutText(composerText, glyphs, renderSettings, xHeight, seed)
    setLayout(result)
    setPageIndex((p) => Math.min(p, Math.max(0, result.pages.length - 1)))
    setRendering(false)
  }, [rendering, glyphs, profile, composerText, renderSettings, seed])

  // Draw to canvas when layout or page changes
  useEffect(() => {
    if (!layout || !canvasRef.current) return
    renderToCanvas(canvasRef.current, layout, pageIndex, renderSettings, imageCacheRef.current)
  }, [layout, pageIndex, renderSettings])

  const handleExport = async () => {
    if (!profileId || !layout) return
    setExporting(true)
    try {
      const { render_id } = await startRender(Number(profileId), {
        text: composerText,
        settings: renderSettings,
        seed,
      })
      navigate(`/profiles/${profileId}/export/${render_id}`)
    } catch {
      setExporting(false)
    }
  }

  const pageCount = layout?.pages.length ?? 1
  const charCount = Array.from(composerText).length

  // ── Settings panel (shared between desktop right col and mobile tab) ────────
  const SettingsPanel = () => (
    <div className="space-y-5 overflow-y-auto">
      <Slider label="Font size" unit="pt" value={renderSettings.font_size} min={8} max={24} step={0.5}
        onChange={(v) => updateRenderSettings({ font_size: v })} />
      <Slider label="Line spacing" unit="×" value={renderSettings.line_spacing} min={1.5} max={4.0} step={0.05}
        onChange={(v) => updateRenderSettings({ line_spacing: v })} />

      {/* Ink color */}
      <div>
        <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-1.5">Ink colour</label>
        <div className="flex items-center gap-3">
          <input
            type="color"
            value={renderSettings.ink_color}
            onChange={(e) => updateRenderSettings({ ink_color: e.target.value })}
            className="w-10 h-10 rounded-lg border border-ink-200 cursor-pointer"
          />
          <span className="text-sm font-mono text-ink-500">{renderSettings.ink_color}</span>
        </div>
      </div>

      {/* Paper type */}
      <div>
        <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-2">Paper type</label>
        <div className="grid grid-cols-2 gap-1.5">
          {(['plain', 'ruled', 'grid', 'none'] as const).map((pt) => (
            <button
              key={pt}
              onClick={() => updateRenderSettings({ paper_type: pt })}
              className={`py-2 rounded-lg text-sm capitalize border-2 transition-all ${
                renderSettings.paper_type === pt
                  ? 'border-ink-600 bg-ink-50 text-ink-800 font-medium'
                  : 'border-ink-100 text-ink-500 hover:border-ink-300'
              }`}
            >
              {pt}
            </button>
          ))}
        </div>
      </div>

      {/* Margins */}
      <div>
        <label className="text-xs font-medium text-ink-600 uppercase tracking-wider block mb-2">Margins (mm)</label>
        <div className="grid grid-cols-2 gap-3">
          {(['margin_top', 'margin_bottom', 'margin_left', 'margin_right'] as const).map((k) => (
            <Slider key={k} label={k.replace('margin_', '')} unit="mm"
              value={renderSettings[k]} min={10} max={40} step={1}
              onChange={(v) => updateRenderSettings({ [k]: v })} compact />
          ))}
        </div>
      </div>

      <Slider label="Messiness" unit="%" value={renderSettings.messiness} min={0} max={100} step={1}
        onChange={(v) => updateRenderSettings({ messiness: v })} />
      <Slider label="Slant correction" unit="°" value={renderSettings.slant_correction} min={-30} max={30} step={1}
        onChange={(v) => updateRenderSettings({ slant_correction: v })} />

      {/* Seed */}
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
    </div>
  )

  // ── Canvas panel ──────────────────────────────────────────────────────────
  const CanvasPanel = () => (
    <div className="flex flex-col h-full">
      {/* Zoom & page nav */}
      <div className="flex items-center justify-between gap-2 mb-3 flex-shrink-0">
        <div className="flex items-center gap-1">
          <button onClick={() => setZoom((z) => Math.max(0.2, z - 0.1))} className="p-1.5 rounded-lg hover:bg-ink-100 text-ink-600 transition-colors">
            <ZoomOut className="w-4 h-4" />
          </button>
          <span className="text-xs text-ink-500 font-mono w-12 text-center">{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom((z) => Math.min(2, z + 0.1))} className="p-1.5 rounded-lg hover:bg-ink-100 text-ink-600 transition-colors">
            <ZoomIn className="w-4 h-4" />
          </button>
        </div>
        <div className="flex items-center gap-2 text-sm text-ink-600">
          <button
            onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
            disabled={pageIndex === 0}
            className="p-1.5 rounded-lg hover:bg-ink-100 disabled:opacity-30 transition-colors"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-xs font-mono">
            {pageIndex + 1} / {pageCount}
          </span>
          <button
            onClick={() => setPageIndex((p) => Math.min(pageCount - 1, p + 1))}
            disabled={pageIndex >= pageCount - 1}
            className="p-1.5 rounded-lg hover:bg-ink-100 disabled:opacity-30 transition-colors"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Canvas scroll area */}
      <div className="flex-1 overflow-auto flex items-start justify-center bg-ink-100 rounded-xl relative min-h-0">
        {loadError && (
          <div className="absolute inset-0 flex items-center justify-center text-red-500 text-sm p-4 text-center">{loadError}</div>
        )}
        {rendering && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/60 z-10 rounded-xl">
            <div className="w-6 h-6 border-2 border-ink-300 border-t-ink-700 rounded-full animate-spin" />
          </div>
        )}
        <div style={{ transform: `scale(${zoom})`, transformOrigin: 'top center', padding: '16px' }}>
          <canvas
            ref={canvasRef}
            className="shadow-xl rounded"
            style={{ display: 'block', maxWidth: 'none' }}
          />
        </div>
      </div>
    </div>
  )

  // ── MOBILE layout ─────────────────────────────────────────────────────────
  const MobileLayout = () => (
    <div className="flex flex-col h-screen md:hidden">
      <div className="flex-1 overflow-hidden p-3">
        {mobileTab === 'text' && (
          <div className="h-full flex flex-col gap-3">
            <textarea
              value={composerText}
              onChange={(e) => setComposerText(e.target.value)}
              className="flex-1 w-full border border-ink-200 rounded-xl px-4 py-3 text-ink-900 placeholder-ink-400 focus:outline-none focus:border-ink-500 resize-none text-sm leading-relaxed"
              placeholder="Type your text here…"
            />
            <div className="flex items-center justify-between text-xs text-ink-400">
              <span>{charCount} chars</span>
              <span>{pageCount} page{pageCount !== 1 ? 's' : ''}</span>
            </div>
          </div>
        )}
        {mobileTab === 'preview' && <CanvasPanel />}
        {mobileTab === 'settings' && (
          <div className="h-full overflow-y-auto">
            <SettingsPanel />
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
        <button
          onClick={handleExport}
          disabled={exporting}
          className="w-full flex items-center justify-center gap-2 bg-ink-800 text-paper py-3 rounded-xl font-semibold hover:bg-ink-700 disabled:opacity-50 transition-colors"
        >
          <Download className="w-4 h-4" />
          {exporting ? 'Preparing export…' : 'Export'}
        </button>
      </div>
    </div>
  )

  // ── DESKTOP layout ────────────────────────────────────────────────────────
  const DesktopLayout = () => (
    <div className="hidden md:flex h-[calc(100vh-3.5rem)] overflow-hidden">
      {/* Left: text input */}
      <div className="w-72 flex flex-col border-r border-ink-100 p-5 gap-4 flex-shrink-0">
        <h2 className="font-semibold text-ink-800">Your text</h2>
        <textarea
          value={composerText}
          onChange={(e) => setComposerText(e.target.value)}
          className="flex-1 border border-ink-200 rounded-xl px-4 py-3 text-ink-900 placeholder-ink-400 focus:outline-none focus:border-ink-500 resize-none text-sm leading-relaxed"
          placeholder="Type your text here…"
        />
        <div className="flex justify-between text-xs text-ink-400">
          <span>{charCount} characters</span>
          <span>{pageCount} page{pageCount !== 1 ? 's' : ''}</span>
        </div>
        <button
          onClick={handleExport}
          disabled={exporting}
          className="flex items-center justify-center gap-2 bg-ink-800 text-paper py-3 rounded-xl font-semibold hover:bg-ink-700 disabled:opacity-50 transition-colors"
        >
          <Download className="w-4 h-4" />
          {exporting ? 'Preparing…' : 'Export'}
        </button>
      </div>

      {/* Center: canvas */}
      <div className="flex-1 p-5 min-w-0 flex flex-col">
        <CanvasPanel />
      </div>

      {/* Right: settings */}
      <div className="w-72 border-l border-ink-100 p-5 flex-shrink-0 overflow-y-auto">
        <h2 className="font-semibold text-ink-800 mb-4">Settings</h2>
        <SettingsPanel />
      </div>
    </div>
  )

  return (
    <>
      <MobileLayout />
      <DesktopLayout />
    </>
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
