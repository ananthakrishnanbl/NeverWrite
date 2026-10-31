import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Check, X, ChevronRight, SlidersHorizontal, Upload, AlertTriangle } from 'lucide-react'
import { getGlyphs, patchGlyph, Glyph, GlyphPatch } from '../lib/api'
import { estimateXHeight } from '../lib/layout'
import { SHEETS, Sheet, VARIANTS_PER_CHAR, READY_VARIANTS } from '../lib/sheets'

const getMaskUrl = (path: string): string => {
  const clean = path.replace(/\\/g, '/')
  return clean.startsWith('/files/') ? clean : `/files/${clean.replace(/^\//, '')}`
}

interface CharStatus {
  char: string
  variants: (Glyph | null)[] // indexed by variant_index; null = nothing captured
  accepted: number
}

function charStatuses(sheet: Sheet, glyphs: Glyph[]): CharStatus[] {
  return Array.from(sheet.charset).map((char) => {
    const own = glyphs.filter((g) => g.char === char)
    const slots = Math.max(VARIANTS_PER_CHAR, ...own.map((g) => g.variant_index + 1))
    const variants = Array.from({ length: slots }, (_, v) => own.find((g) => g.variant_index === v) ?? null)
    return { char, variants, accepted: own.filter((g) => g.accepted).length }
  })
}

const THUMB_H = 84
const BASELINE_AT = 0.68 // of the thumbnail height, from the top
const X_HEIGHT_AT = 0.24 // the profile's x-height, as a share of the thumbnail height

/**
 * A glyph at the profile's common scale, sitting on a faint baseline, so a
 * variant that is too big, too small or off the line stands out next to the others.
 */
function GlyphThumb({ glyph, scale, height = THUMB_H, baselineOffset }: {
  glyph: Glyph
  scale: number
  height?: number
  baselineOffset?: number
}) {
  const baselineY = height * BASELINE_AT
  const w = glyph.width * scale
  const h = glyph.height * scale
  const bottom = baselineY + (baselineOffset ?? glyph.baseline_offset) * scale
  return (
    <div className="relative w-full overflow-hidden" style={{ height }}>
      <div className="absolute inset-x-0 border-t border-sky-200" style={{ top: baselineY }} />
      <img
        src={getMaskUrl(glyph.mask_path)}
        alt={glyph.char}
        className="absolute left-1/2 -translate-x-1/2 max-w-none"
        style={{ top: bottom - h, width: w, height: h }}
      />
    </div>
  )
}

function StatusBadge({ accepted }: { accepted: number }) {
  if (accepted >= READY_VARIANTS) {
    return <span className="text-xs font-medium text-green-700 bg-green-50 px-2 py-0.5 rounded-full">Ready</span>
  }
  if (accepted > 0) {
    return <span className="text-xs font-medium text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full">{accepted} accepted</span>
  }
  return <span className="text-xs font-medium text-red-700 bg-red-50 px-2 py-0.5 rounded-full">Missing</span>
}

export default function GlyphReview() {
  const { profileId } = useParams<{ profileId: string }>()
  const navigate = useNavigate()
  const [glyphs, setGlyphs] = useState<Glyph[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sheetNumber, setSheetNumber] = useState(1)
  const [selected, setSelected] = useState<Glyph | null>(null)
  const [patch, setPatch] = useState<GlyphPatch | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!profileId) return
    getGlyphs(Number(profileId))
      .then((gs) => {
        setGlyphs(gs)
        // Open on the first sheet that has anything on it
        const first = SHEETS.find((s) => gs.some((g) => s.charset.includes(g.char)))
        if (first) setSheetNumber(first.number)
      })
      .catch(() => setError('Failed to load glyphs.'))
      .finally(() => setLoading(false))
  }, [profileId])

  const progress = useMemo(
    () => SHEETS.map((sheet) => {
      const statuses = charStatuses(sheet, glyphs)
      return {
        sheet,
        statuses,
        ready: statuses.filter((s) => s.accepted >= READY_VARIANTS).length,
        missing: statuses.filter((s) => s.accepted === 0).map((s) => s.char),
        thin: statuses.filter((s) => s.accepted > 0 && s.accepted < READY_VARIANTS).map((s) => s.char),
      }
    }),
    [glyphs]
  )
  const current = progress.find((p) => p.sheet.number === sheetNumber) ?? progress[0]
  const totalChars = progress.reduce((n, p) => n + p.statuses.length, 0)
  const readyChars = progress.reduce((n, p) => n + p.ready, 0)
  const acceptedCount = glyphs.filter((g) => g.accepted).length

  // One scale for every thumbnail, from the profile's x-height
  const scale = useMemo(() => {
    const heights = glyphs.map((g) => g.height).sort((a, b) => a - b)
    const typical = heights.length ? heights[Math.floor(heights.length / 2)] * 0.55 : 0
    return (THUMB_H * X_HEIGHT_AT) / estimateXHeight(glyphs, typical)
  }, [glyphs])

  const replaceGlyph = (g: Glyph) => setGlyphs((prev) => prev.map((x) => (x.id === g.id ? g : x)))

  const setAccepted = async (g: Glyph, accepted: boolean) => {
    if (g.accepted === accepted) return
    replaceGlyph({ ...g, accepted })
    try {
      replaceGlyph(await patchGlyph(g.id, { accepted }))
    } catch {
      replaceGlyph(g)
      setError('Could not save that change. Please try again.')
    }
  }

  const openGlyph = (g: Glyph) => {
    setSelected(g)
    setPatch({
      accepted: g.accepted,
      baseline_offset: g.baseline_offset,
      bearing_left: g.bearing_left,
      bearing_right: g.bearing_right,
    })
  }

  const saveGlyph = useCallback(async () => {
    if (!selected || !patch) return
    setSaving(true)
    try {
      const updated = await patchGlyph(selected.id, patch)
      setGlyphs((prev) => prev.map((g) => (g.id === updated.id ? updated : g)))
      setSelected(null)
    } catch {
      // keep panel open on error
    } finally {
      setSaving(false)
    }
  }, [selected, patch])

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="text-3xl font-bold text-ink-900">Glyph Review</h1>
          <p className="text-ink-600 mt-1">
            Setup:{' '}
            {progress.map((p, i) => (
              <span key={p.sheet.number}>
                {i > 0 && ', '}
                {p.sheet.name.toLowerCase()} <span className="font-semibold">{p.ready}/{p.statuses.length}</span>
              </span>
            ))}
          </p>
          <p className="text-sm text-ink-400 mt-0.5">
            A character is ready once it has {READY_VARIANTS} accepted variants. {acceptedCount} of {glyphs.length}{' '}
            variants accepted.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={() => navigate(`/profiles/${profileId}/capture?sheet=${sheetNumber}`)}
            className="flex items-center gap-2 border border-ink-300 text-ink-700 px-4 py-2.5 rounded-lg font-medium hover:bg-ink-50 transition-colors"
          >
            <Upload className="w-4 h-4" />
            Upload Another Sheet
          </button>
          <button
            onClick={() => navigate(`/profiles/${profileId}/preview`)}
            className="flex items-center gap-2 bg-ink-800 text-paper px-5 py-2.5 rounded-lg font-medium hover:bg-ink-700 transition-colors"
          >
            Continue to Preview
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Summary bar: characters ready across all sheets */}
      <div className="mb-6 w-full bg-ink-100 rounded-full h-2">
        <div
          className="bg-ink-700 h-2 rounded-full transition-all"
          style={{ width: totalChars > 0 ? `${(readyChars / totalChars) * 100}%` : '0%' }}
        />
      </div>

      {/* Sheet tabs */}
      <div className="flex gap-1 mb-4 bg-ink-50 rounded-xl p-1">
        {progress.map((p) => (
          <button
            key={p.sheet.number}
            onClick={() => setSheetNumber(p.sheet.number)}
            className={`flex-1 py-2 px-2 text-sm font-medium rounded-lg transition-all ${
              sheetNumber === p.sheet.number ? 'bg-white text-ink-800 shadow-sm' : 'text-ink-500 hover:text-ink-700'
            }`}
          >
            {p.sheet.name}{' '}
            <span className={p.ready === p.statuses.length ? 'text-green-600' : 'text-ink-400'}>
              {p.ready}/{p.statuses.length}
            </span>
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 bg-red-50 text-red-700 border border-red-200 rounded-lg px-4 py-3 text-sm">{error}</div>
      )}

      {!loading && (current.missing.length > 0 || current.thin.length > 0) && (
        <div className="mb-6 bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-900">
          {current.missing.length > 0 && (
            <p>
              <span className="font-semibold">Missing:</span>{' '}
              <span className="font-mono">{current.missing.join(' ')}</span>
            </p>
          )}
          {current.thin.length > 0 && (
            <p className={current.missing.length > 0 ? 'mt-1' : ''}>
              <span className="font-semibold">Fewer than {READY_VARIANTS} accepted:</span>{' '}
              <span className="font-mono">{current.thin.join(' ')}</span>
            </p>
          )}
          <p className="mt-2 text-amber-800">
            Accept a variant below if it looks right, or write these again on a fresh copy of Sheet{' '}
            {current.sheet.number} and upload it. Only the boxes you fill in are replaced.
          </p>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="w-8 h-8 border-2 border-ink-300 border-t-ink-700 rounded-full animate-spin" />
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {current.statuses.map((s) => {
            const issues = s.variants.flatMap((g) => (g?.issue ? [g] : []))
            return (
              <div
                key={s.char}
                className={`rounded-xl border-2 bg-white p-3 ${
                  s.accepted >= READY_VARIANTS ? 'border-ink-100' : s.accepted > 0 ? 'border-amber-200' : 'border-red-200'
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="font-mono text-lg text-ink-800 leading-none">{s.char}</span>
                  <StatusBadge accepted={s.accepted} />
                </div>
                <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${s.variants.length}, minmax(0, 1fr))` }}>
                  {s.variants.map((g, v) =>
                    g ? (
                      <div
                        key={v}
                        className={`relative rounded-lg border overflow-hidden ${
                          g.accepted ? 'border-ink-200' : 'border-red-200 bg-red-50/40'
                        } ${selected?.id === g.id ? 'ring-2 ring-ink-500' : ''}`}
                      >
                        <button
                          onClick={() => openGlyph(g)}
                          className={`block w-full hover:bg-ink-50/60 ${g.accepted ? '' : 'opacity-60'}`}
                          title="Adjust baseline and spacing"
                        >
                          <GlyphThumb glyph={g} scale={scale} />
                        </button>
                        {g.issue && (
                          <span className="absolute top-1 right-1" title={g.issue}>
                            <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
                          </span>
                        )}
                        <div className="flex border-t border-ink-100 text-ink-400">
                          <button
                            onClick={() => setAccepted(g, true)}
                            className={`flex-1 py-1 flex justify-center ${g.accepted ? 'bg-green-50 text-green-600' : 'hover:text-green-600'}`}
                            aria-label={`Accept ${s.char} variant ${v + 1}`}
                            title="Accept"
                          >
                            <Check className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => setAccepted(g, false)}
                            className={`flex-1 py-1 flex justify-center border-l border-ink-100 ${!g.accepted ? 'bg-red-50 text-red-500' : 'hover:text-red-500'}`}
                            aria-label={`Reject ${s.char} variant ${v + 1}`}
                            title="Reject"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div
                        key={v}
                        className="rounded-lg border border-dashed border-ink-200 flex items-center justify-center text-xs text-ink-300"
                        style={{ minHeight: THUMB_H + 26 }}
                      >
                        Empty
                      </div>
                    )
                  )}
                </div>
                {issues.map((g) => (
                  <p key={g.id} className="mt-2 text-xs text-amber-700 flex gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
                    <span>Box {g.variant_index + 1}: {g.issue}.</span>
                  </p>
                ))}
              </div>
            )
          })}
        </div>
      )}

      {/* Edit panel */}
      {selected && patch && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center">
          <div className="absolute inset-0 bg-ink-900/40" onClick={() => setSelected(null)} />
          <div className="relative bg-white rounded-t-2xl sm:rounded-2xl shadow-2xl p-6 w-full max-w-sm z-10">
            <div className="flex items-center justify-between mb-5">
              <h2 className="font-semibold text-ink-800 flex items-center gap-2">
                <SlidersHorizontal className="w-4 h-4" />
                "{selected.char}", box {selected.variant_index + 1}
              </h2>
              <button onClick={() => setSelected(null)} className="text-ink-400 hover:text-ink-700">
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Glyph on its baseline, following the slider */}
            <div className="bg-ink-50 rounded-xl px-4 mb-3">
              <GlyphThumb glyph={selected} scale={scale * 1.6} height={THUMB_H * 1.6} baselineOffset={patch.baseline_offset} />
            </div>
            {selected.issue && (
              <p className="mb-4 text-xs text-amber-700 flex gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
                {selected.issue}.
              </p>
            )}

            {/* Accept / Reject */}
            <div className="flex gap-2 mb-5">
              <button
                onClick={() => setPatch((p) => p ? { ...p, accepted: true } : p)}
                className={`flex-1 py-2 rounded-lg text-sm font-medium flex items-center justify-center gap-2 border-2 transition-all ${
                  patch.accepted ? 'border-green-500 bg-green-50 text-green-700' : 'border-ink-200 text-ink-500'
                }`}
              >
                <Check className="w-4 h-4" /> Accept
              </button>
              <button
                onClick={() => setPatch((p) => p ? { ...p, accepted: false } : p)}
                className={`flex-1 py-2 rounded-lg text-sm font-medium flex items-center justify-center gap-2 border-2 transition-all ${
                  !patch.accepted ? 'border-red-400 bg-red-50 text-red-600' : 'border-ink-200 text-ink-500'
                }`}
              >
                <X className="w-4 h-4" /> Reject
              </button>
            </div>

            {/* Sliders */}
            {[
              { label: 'Baseline offset', key: 'baseline_offset' as keyof GlyphPatch, min: -150, max: 150, step: 1 },
              { label: 'Left bearing', key: 'bearing_left' as keyof GlyphPatch, min: 0, max: 20, step: 0.5 },
              { label: 'Right bearing', key: 'bearing_right' as keyof GlyphPatch, min: 0, max: 20, step: 0.5 },
            ].map(({ label, key, min, max, step }) => (
              <div key={key} className="mb-4">
                <div className="flex justify-between text-sm text-ink-600 mb-1">
                  <span>{label}</span>
                  <span className="font-mono">{(patch[key] as number).toFixed(1)}</span>
                </div>
                <input
                  type="range"
                  min={min}
                  max={max}
                  step={step}
                  value={patch[key] as number}
                  onChange={(e) => setPatch((p) => p ? { ...p, [key]: Number(e.target.value) } : p)}
                  className="w-full accent-ink-700"
                />
              </div>
            ))}

            <button
              onClick={saveGlyph}
              disabled={saving}
              className="w-full bg-ink-800 text-paper py-2.5 rounded-xl font-semibold hover:bg-ink-700 disabled:opacity-50 transition-colors"
            >
              {saving ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
