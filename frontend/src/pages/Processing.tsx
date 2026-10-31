import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { AlertCircle, CheckCircle } from 'lucide-react'
import { pollJob, Job } from '../lib/api'

const STAGE_LABELS: Record<string, string> = {
  upload: 'Uploading scan…',
  binarize: 'Binarizing image…',
  segment: 'Segmenting characters…',
  extract: 'Extracting glyphs…',
  metrics: 'Analysing handwriting metrics…',
  finalize: 'Finalizing profile…',
}

export default function Processing() {
  const { profileId, jobId } = useParams<{ profileId: string; jobId: string }>()
  const navigate = useNavigate()
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!jobId) return
    const stop = pollJob(
      jobId,
      (j) => {
        setJob(j)
        if (j.status === 'done') {
          setTimeout(() => navigate(`/profiles/${profileId}/glyphs`), 800)
        }
        if (j.status === 'error') {
          setError(j.error ?? 'An unknown error occurred.')
        }
      },
      1500
    )
    return stop
  }, [jobId, profileId, navigate])

  const progress = job?.progress ?? 0
  const stage = (job?.meta?.stage as string) ?? job?.stage ?? ''
  const stageLabel = STAGE_LABELS[stage] ?? (job?.status === 'pending' ? 'Queued…' : 'Processing…')

  return (
    <div className="max-w-lg mx-auto px-4 py-20 flex flex-col items-center text-center">
      {/* Status icon */}
      {job?.status === 'done' ? (
        <CheckCircle className="w-16 h-16 text-green-500 mb-6" />
      ) : error ? (
        <AlertCircle className="w-16 h-16 text-red-500 mb-6" />
      ) : (
        <div className="w-16 h-16 rounded-full border-4 border-ink-200 border-t-ink-700 animate-spin mb-6" />
      )}

      <h1 className="text-2xl font-bold text-ink-900 mb-2">
        {error ? 'Processing failed' : job?.status === 'done' ? 'Done!' : 'Processing your scan'}
      </h1>

      {error ? (
        <div className="mt-4 bg-red-50 border border-red-200 rounded-xl p-4 w-full text-left">
          <p className="text-red-700 text-sm">{error}</p>
          <button
            onClick={() => navigate(`/profiles/${profileId}/capture`)}
            className="mt-3 text-sm text-red-600 underline hover:text-red-800"
          >
            ← Go back and try again
          </button>
        </div>
      ) : (
        <>
          <p className="text-ink-500 mb-8">{stageLabel}</p>

          {/* Progress bar */}
          <div className="w-full bg-ink-100 rounded-full h-3 mb-3">
            <div
              className="bg-ink-700 h-3 rounded-full transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-sm text-ink-400">{Math.round(progress)}% complete</p>

          {/* Stage steps */}
          <div className="mt-10 w-full text-left space-y-2">
            {Object.entries(STAGE_LABELS).map(([key, label]) => {
              const keys = Object.keys(STAGE_LABELS)
              const stageIdx = keys.indexOf(stage)
              const thisIdx = keys.indexOf(key)
              const isDone = stageIdx > thisIdx || job?.status === 'done'
              const isCurrent = key === stage
              return (
                <div key={key} className={`flex items-center gap-3 text-sm py-1.5 px-3 rounded-lg ${isCurrent ? 'bg-ink-50' : ''}`}>
                  <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 ${
                    isDone ? 'bg-ink-700 border-ink-700' : isCurrent ? 'border-ink-500' : 'border-ink-200'
                  }`}>
                    {isDone && <CheckCircle className="w-3 h-3 text-white" />}
                    {isCurrent && !isDone && (
                      <div className="w-1.5 h-1.5 bg-ink-500 rounded-full animate-pulse" />
                    )}
                  </div>
                  <span className={isDone ? 'text-ink-700' : isCurrent ? 'text-ink-800 font-medium' : 'text-ink-400'}>
                    {label}
                  </span>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
