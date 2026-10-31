import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, CheckCircle, AlertCircle, FileArchive, FileText } from 'lucide-react'
import { getBatch, getBatchFileUrl, RenderBatch, RenderSettings } from '../lib/api'
import { MissingChars } from './Export'

export default function BatchExport() {
  const { profileId, batchId } = useParams<{ profileId: string; batchId: string }>()
  const navigate = useNavigate()
  const [batch, setBatch] = useState<RenderBatch | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!batchId) return
    const id = Number(batchId)
    let timer: ReturnType<typeof setTimeout> | undefined
    let cancelled = false
    let loaded = false

    // Poll the batch until it finishes; the timer is cleared on unmount
    const check = async () => {
      try {
        const b = await getBatch(id)
        if (cancelled) return
        loaded = true
        setBatch(b)
        if (b.status === 'done') return
        if (b.status === 'error') { setError(b.error ?? 'Export failed.'); return }
      } catch {
        if (cancelled) return
        if (!loaded) { setError('Failed to load the export.'); return }
        // later failures are transient; keep polling
      }
      timer = setTimeout(check, 1500)
    }
    check()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [batchId])

  const settings = parseSettings(batch?.settings_json)
  const missing = [...new Set(batch?.cards.flatMap((c) => c.missing_chars) ?? [])]
  const done = batch?.status === 'done'

  return (
    <div className="max-w-2xl mx-auto px-4 py-12">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-ink-900 mb-2">Export card set</h1>
        <p className="text-ink-500">
          {batch ? `${batch.cards.length} card${batch.cards.length !== 1 ? 's' : ''}` : 'Your cards'}
          {settings?.page_width_mm !== undefined && `, ${settings.page_width_mm} × ${settings.page_height_mm} mm`}
          , one card per page.
        </p>
      </div>

      {error ? (
        <div className="bg-red-50 border border-red-200 rounded-2xl p-6 flex gap-4">
          <AlertCircle className="w-8 h-8 text-red-500 shrink-0" />
          <div>
            <h2 className="font-semibold text-red-700 mb-1">Export failed</h2>
            <p className="text-sm text-red-600">{error}</p>
          </div>
        </div>
      ) : !done ? (
        <div className="bg-white border border-ink-200 rounded-2xl p-8 text-center">
          <div className="w-12 h-12 rounded-full border-4 border-ink-200 border-t-ink-700 animate-spin mx-auto mb-5" />
          <h2 className="font-semibold text-ink-800 mb-2">Rendering your cards…</h2>
          <p className="text-sm text-ink-500">This takes a second or two per card.</p>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="bg-green-50 border border-green-200 rounded-2xl p-5 flex gap-4">
            <CheckCircle className="w-7 h-7 text-green-600 shrink-0 mt-0.5" />
            <div>
              <h2 className="font-semibold text-green-800 mb-1">Cards ready</h2>
              <p className="text-sm text-green-700">Each page matches its card in the Composer preview.</p>
            </div>
          </div>

          {missing.length > 0 && <MissingChars chars={missing} />}

          <div className="space-y-2">
            <a
              href={getBatchFileUrl(Number(batchId), 'pdf')}
              download
              className="flex items-center gap-3 w-full bg-ink-800 text-paper px-5 py-3.5 rounded-xl font-semibold hover:bg-ink-700 transition-colors"
            >
              <FileText className="w-5 h-5" />
              Download all cards (one PDF)
            </a>
            <a
              href={getBatchFileUrl(Number(batchId), 'zip')}
              download
              className="flex items-center gap-3 w-full border border-ink-300 text-ink-700 px-5 py-3.5 rounded-xl font-semibold hover:bg-ink-50 transition-colors"
            >
              <FileArchive className="w-5 h-5" />
              Download a PDF per card (zip)
            </a>
          </div>
        </div>
      )}

      {/* Every card's text and seed, so any card can be regenerated exactly */}
      {batch && batch.cards.length > 0 && (
        <div className="mt-8 bg-white border border-ink-100 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-ink-50 text-ink-500 text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left font-medium px-4 py-2 w-10">#</th>
                <th className="text-left font-medium px-4 py-2">Card</th>
                <th className="text-left font-medium px-4 py-2">Seed</th>
                <th className="text-left font-medium px-4 py-2">No glyph</th>
              </tr>
            </thead>
            <tbody>
              {batch.cards.map((card, i) => (
                <tr key={i} className="border-t border-ink-100">
                  <td className="px-4 py-2 text-ink-400">{i + 1}</td>
                  <td className="px-4 py-2 text-ink-800">{card.label || `Card ${i + 1}`}</td>
                  <td className="px-4 py-2 font-mono text-ink-600">{card.seed.toString(16).padStart(8, '0')}</td>
                  <td className="px-4 py-2 font-mono text-amber-700">{card.missing_chars.join(' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <button
        onClick={() => navigate(`/profiles/${profileId}/compose`)}
        className="mt-8 flex items-center gap-2 text-ink-600 hover:text-ink-800 transition-colors text-sm"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to Composer
      </button>
    </div>
  )
}

function parseSettings(json: string | undefined): Partial<RenderSettings> | null {
  if (!json) return null
  try {
    return JSON.parse(json) as Partial<RenderSettings>
  } catch {
    return null
  }
}
