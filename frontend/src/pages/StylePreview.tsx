import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Check, ArrowLeft } from 'lucide-react'
import { getGlyphs, getProfile, profileXHeight, Glyph, Profile } from '../lib/api'
import { layoutText, renderToCanvas } from '../lib/layout'
import { useNeverWriteStore } from '../store'

const PANGRAM = 'The quick brown fox jumps over the lazy dog.'

export default function StylePreview() {
  const { profileId } = useParams<{ profileId: string }>()
  const navigate = useNavigate()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const imageCacheRef = useRef(new Map<string, HTMLImageElement>())
  const { renderSettings, seed } = useNeverWriteStore()

  useEffect(() => {
    if (!profileId) return
    const id = Number(profileId)
    Promise.all([getGlyphs(id), getProfile(id)])
      .then(async ([glyphs, profile]: [Glyph[], Profile]) => {
        const canvas = canvasRef.current
        if (!canvas) return
        const xHeight = profileXHeight(profile)
        const layout = layoutText(PANGRAM, glyphs, renderSettings, xHeight, seed)
        await renderToCanvas(canvas, layout, 0, renderSettings, imageCacheRef.current)
      })
      .catch(() => setError('Failed to render preview.'))
      .finally(() => setLoading(false))
  }, [profileId, renderSettings, seed])

  return (
    <div className="max-w-3xl mx-auto px-4 py-12">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-ink-900 mb-2">Style Preview</h1>
        <p className="text-ink-500">Here's a pangram rendered in your handwriting. Does it look right?</p>
      </div>

      {error && (
        <div className="mb-4 bg-red-50 text-red-700 border border-red-200 rounded-lg px-4 py-3 text-sm">{error}</div>
      )}

      <div className="bg-white border border-ink-200 rounded-2xl overflow-hidden shadow-sm mb-8 relative">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
            <div className="w-8 h-8 border-2 border-ink-300 border-t-ink-700 rounded-full animate-spin" />
          </div>
        )}
        <canvas
          ref={canvasRef}
          className="w-full"
          style={{ display: 'block' }}
        />
        {!loading && !error && (
          <div className="p-4 border-t border-ink-100 bg-ink-50">
            <p className="text-sm text-ink-500 italic">"{PANGRAM}"</p>
          </div>
        )}
      </div>

      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate(`/profiles/${profileId}/compose`)}
          className="flex items-center gap-2 bg-ink-800 text-paper px-6 py-3 rounded-xl font-semibold hover:bg-ink-700 transition-colors"
        >
          <Check className="w-4 h-4" />
          Looks like me ✓
        </button>
        <button
          onClick={() => navigate(`/profiles/${profileId}/glyphs`)}
          className="flex items-center gap-2 text-ink-600 hover:text-ink-800 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Go back to glyph review
        </button>
      </div>
    </div>
  )
}
