import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Download, FileArchive, PenTool, RotateCcw, Scan, Settings2 } from 'lucide-react'
import {
  apiError,
  batchPlotFileUrl,
  batchPlotZipUrl,
  getBatchPlot,
  getRenderPlot,
  renderPlotFileUrl,
  BatchPlotSummary,
  PlotFormat,
  PlotSettings,
  PlotSummary,
} from '../lib/api'
import {
  DEFAULT_PLOT_SETTINGS,
  FORMAT_INFO,
  PLOT_FIELDS,
  formatDuration,
  formatLength,
  loadPlotPrefs,
  plotSettingsProblem,
  savePlotPrefs,
} from '../lib/plot'

type Target = { kind: 'render'; renderId: number } | { kind: 'batch'; batchId: number }

/**
 * Plot files for a finished render or card set: SVG, G-code or HPGL, the
 * machine settings they use, the estimated plot time and a preview of the
 * pen paths. Only letters drawn on the Draw page can be plotted.
 */
export default function PlotPanel({ target, profileId }: { target: Target; profileId: number }) {
  const [{ format, settings }, setPrefs] = useState(loadPlotPrefs)
  const [showSettings, setShowSettings] = useState(false)
  const [page, setPage] = useState(1)
  const [card, setCard] = useState(1)
  const [summary, setSummary] = useState<PlotSummary | null>(null)
  const [batch, setBatch] = useState<BatchPlotSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const problem = plotSettingsProblem(settings)

  useEffect(() => savePlotPrefs(format, settings), [format, settings])

  useEffect(() => {
    if (problem) return
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        if (target.kind === 'render') {
          const s = await getRenderPlot(target.renderId, format, settings, page)
          if (!cancelled) { setSummary(s); setError(null) }
        } else {
          const b = await getBatchPlot(target.batchId, format, settings)
          if (!cancelled) { setBatch(b); setError(null) }
        }
      } catch (err) {
        if (!cancelled) setError(apiError(err, 'Could not prepare the plot files.'))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, 250)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [target.kind, target.kind === 'render' ? target.renderId : target.batchId, format, settings, page, problem])

  const shown: PlotSummary | null = target.kind === 'render' ? summary : batch?.cards[card - 1] ?? null
  const unplottable = useMemo(() => {
    const chars = target.kind === 'render' ? summary?.unplottable_chars ?? [] : batch?.cards.flatMap((c) => c.unplottable_chars) ?? []
    return [...new Set(chars)]
  }, [target.kind, summary, batch])
  const traced = useMemo(() => {
    const chars = target.kind === 'render' ? summary?.traced_chars ?? [] : batch?.cards.flatMap((c) => c.traced_chars) ?? []
    return [...new Set(chars)]
  }, [target.kind, summary, batch])
  const anyDrawn = target.kind === 'render'
    ? (summary?.drawn_chars.length ?? 0) > 0
    : batch?.cards.some((c) => c.drawn_chars.length > 0) ?? false
  const clipped = target.kind === 'render' ? summary?.clipped_strokes ?? 0 : batch?.cards.reduce((n, c) => n + c.clipped_strokes, 0) ?? 0

  const fileUrl = (fmt: PlotFormat) =>
    target.kind === 'render'
      ? renderPlotFileUrl(target.renderId, fmt, settings, page)
      : batchPlotFileUrl(target.batchId, fmt, settings, card)

  const setFormat = (f: PlotFormat) => setPrefs((p) => ({ ...p, format: f }))
  const setSetting = (key: keyof PlotSettings, value: number) =>
    setPrefs((p) => ({ ...p, settings: { ...p.settings, [key]: value } }))

  const info = FORMAT_INFO[format]
  const ready = !loading && !error && !problem && shown !== null

  return (
    <section className="bg-white border border-ink-200 rounded-2xl p-5 space-y-4" aria-labelledby="plot-heading">
      <div className="flex items-start gap-3">
        <PenTool className="w-6 h-6 text-ink-600 shrink-0 mt-0.5" />
        <div>
          <h2 id="plot-heading" className="font-semibold text-ink-900">Plot with a pen</h2>
          <p className="text-sm text-ink-500">
            Files a pen plotter follows: one line per stroke, nothing filled, laid out exactly like the PDF.
          </p>
        </div>
      </div>

      {error && !summary && !batch ? (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-900">
          <p>{error}</p>
          {error.includes('Draw page') && (
            <Link to={`/profiles/${profileId}/draw`} className="inline-block mt-2 font-medium underline">
              Draw letters
            </Link>
          )}
        </div>
      ) : (
        <>
          {/* Format */}
          <div>
            <div className="flex gap-1 bg-ink-50 rounded-xl p-1" role="radiogroup" aria-label="Plot file format">
              {(Object.keys(FORMAT_INFO) as PlotFormat[]).map((f) => (
                <button
                  key={f}
                  role="radio"
                  aria-checked={format === f}
                  onClick={() => setFormat(f)}
                  className={`flex-1 py-2 px-2 text-sm font-medium rounded-lg transition-all ${
                    format === f ? 'bg-white text-ink-800 shadow-sm' : 'text-ink-500 hover:text-ink-700'
                  }`}
                >
                  {FORMAT_INFO[f].label} <span className="text-ink-400 font-normal">{FORMAT_INFO[f].ext}</span>
                </button>
              ))}
            </div>
            <p className="mt-2 text-sm text-ink-600">
              <span className="font-medium">For {info.machine}.</span>{' '}
              {info.note}
            </p>
          </div>

          {/* Machine settings */}
          <div className="border border-ink-100 rounded-xl">
            <button
              onClick={() => setShowSettings((v) => !v)}
              className="w-full flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-ink-700"
              aria-expanded={showSettings}
            >
              <Settings2 className="w-4 h-4" />
              Machine settings
              <span className="ml-auto text-ink-400 font-normal">
                {settings.draw_speed} mm/min drawing{format === 'gcode' ? `, pen up Z${settings.pen_up_z}` : ''}
              </span>
            </button>
            {showSettings && (
              <div className="px-4 pb-4">
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  {PLOT_FIELDS.filter((f) => f.formats.includes(format)).map((f) => (
                    <label key={f.key} className="text-xs text-ink-500">
                      {f.label} <span className="text-ink-400">({f.unit})</span>
                      <input
                        type="number"
                        step={f.step}
                        value={Number.isFinite(settings[f.key]) ? settings[f.key] : ''}
                        onChange={(e) => setSetting(f.key, e.target.valueAsNumber)}
                        className="mt-1 w-full border border-ink-200 rounded-lg px-2.5 py-1.5 text-sm text-ink-900 focus:outline-none focus:border-ink-500"
                      />
                    </label>
                  ))}
                </div>
                {format !== 'svg' && (
                  <p className="mt-2 text-xs text-ink-400">
                    Card corner is where the card’s bottom-left corner sits on the machine, with its top edge away from
                    you.
                  </p>
                )}
                <button
                  onClick={() => setPrefs((p) => ({ ...p, settings: { ...DEFAULT_PLOT_SETTINGS } }))}
                  className="mt-3 flex items-center gap-1.5 text-xs text-ink-500 hover:text-ink-800"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  Reset to defaults
                </button>
              </div>
            )}
          </div>

          {problem && <p className="text-sm text-red-600">{problem}</p>}
          {error && (summary || batch) && <p className="text-sm text-red-600">{error}</p>}

          {/* Time and distances */}
          {(target.kind === 'render' ? summary : batch) && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
              <Stat
                label={target.kind === 'batch' ? `All ${batch!.cards.length} cards` : 'Estimated time'}
                value={formatDuration(target.kind === 'batch' ? batch!.total_seconds : summary!.seconds)}
              />
              <Stat
                label="Pen lifts"
                value={String(target.kind === 'batch' ? batch!.total_pen_lifts : summary!.pen_lifts)}
              />
              <Stat
                label="Drawn"
                value={formatLength(target.kind === 'batch' ? batch!.total_draw_mm : summary!.draw_mm)}
              />
              <Stat
                label={target.kind === 'batch' ? 'Per card' : 'Pen-up travel'}
                value={
                  target.kind === 'batch'
                    ? formatDuration(batch!.total_seconds / Math.max(1, batch!.cards.length))
                    : formatLength(summary!.travel_mm)
                }
              />
            </div>
          )}

          {traced.length > 0 && (
            <div className="bg-ink-50 border border-ink-100 rounded-xl p-3 flex gap-2.5 text-sm text-ink-700">
              <Scan className="w-4 h-4 shrink-0 mt-0.5 text-ink-500" />
              <p>
                {anyDrawn ? (
                  <>Traced from scans: <span className="font-mono tracking-widest">{traced.join(' ')}</span>. Their</>
                ) : (
                  <>Every letter here was traced from a scan. The</>
                )}{' '}
                pen paths follow the middle of the ink, so where strokes cross or loop the pen may take a slightly
                different route than you did.{' '}
                <Link to={`/profiles/${profileId}/glyphs?paths=1`} className="underline font-medium">Check the paths</Link>
                , or{' '}
                <Link to={`/profiles/${profileId}/draw`} className="underline font-medium">draw a letter</Link> to give
                it your exact path.
              </p>
            </div>
          )}
          {unplottable.length > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 flex gap-2.5 text-sm text-amber-900">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <p>
                Left out of the plot, because their glyph images are missing:{' '}
                <span className="font-mono tracking-widest">{unplottable.join(' ')}</span>. Upload that sheet again or{' '}
                <Link to={`/profiles/${profileId}/draw`} className="underline font-medium">draw them</Link>.
              </p>
            </div>
          )}
          {clipped > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 flex gap-2.5 text-sm text-amber-900">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <p>
                {clipped} stroke{clipped !== 1 ? 's run' : ' runs'} past the edge of the card and{' '}
                {clipped !== 1 ? 'were' : 'was'} cut there. Increase the margins to keep every letter whole.
              </p>
            </div>
          )}

          {/* Which page or card */}
          {target.kind === 'render' && summary && summary.page_count > 1 && (
            <label className="flex items-center gap-2 text-sm text-ink-600">
              Page
              <select
                value={page}
                onChange={(e) => setPage(Number(e.target.value))}
                className="border border-ink-200 rounded-lg px-2 py-1"
              >
                {Array.from({ length: summary.page_count }, (_, i) => (
                  <option key={i} value={i + 1}>{i + 1} of {summary.page_count}</option>
                ))}
              </select>
              <span className="text-ink-400">A plot file holds one page.</span>
            </label>
          )}
          {target.kind === 'batch' && batch && batch.cards.length > 1 && (
            <label className="flex items-center gap-2 text-sm text-ink-600">
              Preview card
              <select
                value={card}
                onChange={(e) => setCard(Number(e.target.value))}
                className="border border-ink-200 rounded-lg px-2 py-1 max-w-[16rem]"
              >
                {batch.cards.map((c, i) => (
                  <option key={i} value={i + 1}>
                    {i + 1}. {c.label || `Card ${i + 1}`} ({formatDuration(c.seconds)})
                  </option>
                ))}
              </select>
            </label>
          )}

          {/* Pen paths */}
          {shown && !problem && (
            <figure>
              <div
                className="mx-auto bg-paper border border-ink-200 shadow-sm"
                style={{ aspectRatio: `${shown.width_mm} / ${shown.height_mm}`, maxWidth: shown.width_mm > shown.height_mm ? '100%' : '20rem' }}
              >
                <img
                  src={fileUrl('svg')}
                  alt="The pen paths of this card"
                  className="w-full h-full"
                />
              </div>
              <figcaption className="mt-2 text-center text-xs text-ink-400">
                The pen paths, {shown.width_mm} × {shown.height_mm} mm. Each line is one pass of the pen.
              </figcaption>
            </figure>
          )}

          {/* Downloads */}
          <div className="space-y-2">
            <a
              href={ready ? fileUrl(format) : undefined}
              download
              aria-disabled={!ready}
              className={`flex items-center gap-3 w-full px-5 py-3.5 rounded-xl font-semibold transition-colors ${
                ready ? 'bg-ink-800 text-paper hover:bg-ink-700' : 'bg-ink-100 text-ink-400 pointer-events-none'
              }`}
            >
              <Download className="w-5 h-5" />
              {target.kind === 'batch' && batch && batch.cards.length > 1
                ? `Download card ${card} as ${info.label}`
                : `Download ${info.label}`}
              {target.kind === 'render' && summary && summary.page_count > 1 ? ` (page ${page})` : ''}
              <span className="ml-auto text-sm font-normal opacity-70">{info.ext}</span>
            </a>
            {target.kind === 'batch' && (
              <a
                href={ready ? batchPlotZipUrl(target.batchId, format, settings) : undefined}
                download
                aria-disabled={!ready}
                className={`flex items-center gap-3 w-full border px-5 py-3.5 rounded-xl font-semibold transition-colors ${
                  ready ? 'border-ink-300 text-ink-700 hover:bg-ink-50' : 'border-ink-100 text-ink-300 pointer-events-none'
                }`}
              >
                <FileArchive className="w-5 h-5" />
                Download every card as {info.label} (zip)
              </a>
            )}
          </div>

          <p className="text-xs text-ink-400">
            The time is an estimate that leaves out acceleration, so a real machine takes a little longer. Time one card
            on the machine and set the speeds to match.
          </p>
        </>
      )}
    </section>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-ink-50 rounded-lg px-2 py-2.5">
      <div className="font-semibold text-ink-900">{value}</div>
      <div className="text-xs text-ink-500">{label}</div>
    </div>
  )
}
