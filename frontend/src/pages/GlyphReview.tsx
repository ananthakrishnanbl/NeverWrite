import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Check, X, ChevronRight, SlidersHorizontal, Upload } from 'lucide-react'
import { getGlyphs, patchGlyph, Glyph, GlyphPatch } from '../lib/api'

type Tab = 'lowercase' | 'uppercase' | 'digits' | 'punctuation'

const getMaskUrl = (path: string): string => {
  const clean = path.replace(/\\/g, '/')
  return clean.startsWith('/files/') ? clean : `/files/${clean.replace(/^\//, '')}`
}

const TAB_RANGES: Record<Tab, (ch: string) => boolean> = {
  lowercase: (ch) => /^[a-z]$/.test(ch),
  uppercase: (ch) => /^[A-Z]$/.test(ch),
  digits: (ch) => /^[0-9]$/.test(ch),
  punctuation: (ch) => /^[^a-zA-Z0-9\s]$/.test(ch),
}

const TAB_LABELS: Record<Tab, string> = {
  lowercase: 'Lowercase',
  uppercase: 'Uppercase',
  digits: 'Digits',
  punctuation: 'Punctuation',
}

export default function GlyphReview() {
  const { profileId } = useParams<{ profileId: string }>()
  const navigate = useNavigate()
  const [glyphs, setGlyphs] = useState<Glyph[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('lowercase')
  const [selected, setSelected] = useState<Glyph | null>(null)
  const [patch, setPatch] = useState<GlyphPatch | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!profileId) return
    getGlyphs(Number(profileId))
      .then(setGlyphs)
      .catch(() => setError('Failed to load glyphs.'))
      .finally(() => setLoading(false))
  }, [profileId])

  const filtered = glyphs.filter((g) => TAB_RANGES[tab](g.char))
  const acceptedCount = glyphs.filter((g) => g.accepted).length

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
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-ink-900">Glyph Review</h1>
          <p className="text-ink-500 mt-1">{acceptedCount} / {glyphs.length} glyphs accepted</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate(`/profiles/${profileId}/capture`)}
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

      {/* Summary bar */}
      <div className="mb-6 w-full bg-ink-100 rounded-full h-2">
        <div
          className="bg-ink-700 h-2 rounded-full transition-all"
          style={{ width: glyphs.length > 0 ? `${(acceptedCount / glyphs.length) * 100}%` : '0%' }}
        />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-ink-50 rounded-xl p-1">
        {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 py-2 text-sm font-medium rounded-lg transition-all ${
              tab === t ? 'bg-white text-ink-800 shadow-sm' : 'text-ink-500 hover:text-ink-700'
            }`}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 bg-red-50 text-red-700 border border-red-200 rounded-lg px-4 py-3 text-sm">{error}</div>
      )}

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="w-8 h-8 border-2 border-ink-300 border-t-ink-700 rounded-full animate-spin" />
        </div>
      ) : (
        <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 gap-3">
          {filtered.map((g) => (
            <button
              key={g.id}
              onClick={() => openGlyph(g)}
              className={`relative aspect-square rounded-xl border-2 flex flex-col items-center justify-center gap-1 hover:shadow-md transition-all p-1 ${
                g.accepted
                  ? 'border-ink-300 bg-white'
                  : 'border-red-200 bg-red-50/50'
              } ${selected?.id === g.id ? 'ring-2 ring-ink-500' : ''}`}
            >
              {g.mask_path ? (
                <img
                  src={getMaskUrl(g.mask_path)}
                  alt={g.char}
                  className="w-full h-3/4 object-contain"
                />
              ) : (
                <span className="text-xl font-mono text-ink-700">{g.char}</span>
              )}
              <span className="text-xs text-ink-400 font-mono">{g.char}</span>
              <div className={`absolute top-1 right-1 w-3 h-3 rounded-full ${g.accepted ? 'bg-green-400' : 'bg-red-300'}`} />
            </button>
          ))}
          {filtered.length === 0 && (
            <div className="col-span-full text-center py-12 text-ink-400">
              No glyphs in this category yet.
            </div>
          )}
        </div>
      )}

      {/* Side panel */}
      {selected && patch && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center">
          <div className="absolute inset-0 bg-ink-900/40" onClick={() => setSelected(null)} />
          <div className="relative bg-white rounded-t-2xl sm:rounded-2xl shadow-2xl p-6 w-full max-w-sm z-10">
            <div className="flex items-center justify-between mb-5">
              <h2 className="font-semibold text-ink-800 flex items-center gap-2">
                <SlidersHorizontal className="w-4 h-4" />
                Edit Glyph "{selected.char}"
              </h2>
              <button onClick={() => setSelected(null)} className="text-ink-400 hover:text-ink-700">
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Glyph image */}
            <div className="bg-ink-50 rounded-xl p-4 flex items-center justify-center mb-5 h-28">
              {selected.mask_path ? (
                <img src={getMaskUrl(selected.mask_path)} alt={selected.char} className="max-h-full object-contain" />
              ) : (
                <span className="text-5xl font-mono text-ink-700">{selected.char}</span>
              )}
            </div>

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
              { label: 'Baseline offset', key: 'baseline_offset' as keyof GlyphPatch, min: -80, max: 80, step: 1 },
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
