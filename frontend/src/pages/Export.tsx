import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Download, ArrowLeft, CheckCircle, AlertCircle, FileImage, FileText } from 'lucide-react'
import { getRender, getRenderFileUrl, RenderJob, RenderSettings } from '../lib/api'

export default function Export() {
  const { profileId, renderId } = useParams<{ profileId: string; renderId: string }>()
  const navigate = useNavigate()
  const [render, setRender] = useState<RenderJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [jobDone, setJobDone] = useState(false)

  useEffect(() => {
    if (!renderId) return
    const id = Number(renderId)
    let timer: ReturnType<typeof setTimeout> | undefined
    let cancelled = false
    let loaded = false

    // Poll the render record until it finishes; the timer is cleared on unmount
    const check = async () => {
      try {
        const r = await getRender(id)
        if (cancelled) return
        loaded = true
        setRender(r)
        setLoading(false)
        if (r.status === 'done') { setJobDone(true); return }
        if (r.status === 'error') { setError(r.error ?? 'Render failed.'); return }
      } catch {
        if (cancelled) return
        if (!loaded) { setError('Failed to load render job.'); setLoading(false); return }
        // later failures are transient; keep polling
      }
      timer = setTimeout(check, 2000)
    }
    check()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [renderId])

  const pdfUrl = renderId ? getRenderFileUrl(Number(renderId)) : '#'
  const settings = parseSettings(render?.settings_json)

  return (
    <div className="max-w-xl mx-auto px-4 py-12">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-ink-900 mb-2">Export</h1>
        <p className="text-ink-500">Your handwriting render is being prepared.</p>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="w-8 h-8 border-2 border-ink-300 border-t-ink-700 rounded-full animate-spin" />
        </div>
      ) : error ? (
        <div className="bg-red-50 border border-red-200 rounded-2xl p-6 flex gap-4">
          <AlertCircle className="w-8 h-8 text-red-500 shrink-0" />
          <div>
            <h2 className="font-semibold text-red-700 mb-1">Render failed</h2>
            <p className="text-sm text-red-600">{error}</p>
            <button
              onClick={() => navigate(`/profiles/${profileId}/compose`)}
              className="mt-3 text-sm text-red-500 underline"
            >
              ← Back to composer
            </button>
          </div>
        </div>
      ) : !jobDone ? (
        /* Pending / running */
        <div className="bg-white border border-ink-200 rounded-2xl p-8 text-center">
          <div className="w-12 h-12 rounded-full border-4 border-ink-200 border-t-ink-700 animate-spin mx-auto mb-5" />
          <h2 className="font-semibold text-ink-800 mb-2">Rendering your document…</h2>
          <p className="text-sm text-ink-500">This usually takes a few seconds.</p>
        </div>
      ) : (
        /* Done */
        <div className="space-y-4">
          <div className="bg-green-50 border border-green-200 rounded-2xl p-5 flex gap-4">
            <CheckCircle className="w-7 h-7 text-green-600 shrink-0 mt-0.5" />
            <div>
              <h2 className="font-semibold text-green-800 mb-1">Render complete!</h2>
              <p className="text-sm text-green-700">Your document is ready to download.</p>
            </div>
          </div>

          {/* Render info */}
          {render && (
            <div className="bg-white border border-ink-100 rounded-xl p-5 text-sm text-ink-600 space-y-2">
              <div className="flex justify-between">
                <span>Seed</span>
                <span className="font-mono text-ink-800">{render.seed.toString(16).padStart(8, '0')}</span>
              </div>
              <div className="flex justify-between">
                <span>Created</span>
                <span className="font-mono text-ink-800">{new Date(render.created_at).toLocaleString()}</span>
              </div>
              {settings?.font_size !== undefined && (
                <div className="flex justify-between">
                  <span>Font size</span>
                  <span className="font-mono text-ink-800">{settings.font_size}pt</span>
                </div>
              )}
            </div>
          )}

          {/* Download buttons */}
          <div className="space-y-2">
            <a
              href={pdfUrl}
              download
              className="flex items-center gap-3 w-full bg-ink-800 text-paper px-5 py-3.5 rounded-xl font-semibold hover:bg-ink-700 transition-colors"
            >
              <FileText className="w-5 h-5" />
              Download PDF
            </a>
            <button
              disabled
              className="flex items-center gap-3 w-full border border-ink-200 text-ink-400 px-5 py-3.5 rounded-xl font-semibold cursor-not-allowed"
              title="Coming soon"
            >
              <FileImage className="w-5 h-5" />
              Download PNG
              <span className="ml-auto text-xs bg-ink-100 text-ink-500 px-2 py-0.5 rounded-full">Soon</span>
            </button>
            <button
              disabled
              className="flex items-center gap-3 w-full border border-ink-200 text-ink-400 px-5 py-3.5 rounded-xl font-semibold cursor-not-allowed"
              title="Coming soon"
            >
              <Download className="w-5 h-5" />
              Download SVG
              <span className="ml-auto text-xs bg-ink-100 text-ink-500 px-2 py-0.5 rounded-full">Soon</span>
            </button>
          </div>
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
