import { useState, useRef, DragEvent, ChangeEvent, useEffect } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { Upload, ImageIcon, Check, X, ArrowRight } from 'lucide-react'
import { uploadScan, getGlyphs } from '../lib/api'

const SHEETS = [
  { number: 1, label: 'Sheet 1', sublabel: 'Lowercase (a–z)' },
  { number: 2, label: 'Sheet 2', sublabel: 'Uppercase (A–Z)' },
  { number: 3, label: 'Sheet 3', sublabel: 'Digits & Punctuation' },
]

export default function Capture() {
  const { profileId } = useParams<{ profileId: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  // Glyph Review links here with ?sheet=N to redo part of a sheet
  const [activeSheet, setActiveSheet] = useState(() => {
    const n = Number(searchParams.get('sheet'))
    return SHEETS.some((s) => s.number === n) ? n : 1
  })
  
  // Keep file & preview state persistent per sheet number so switching sheets does NOT wipe images
  const [sheetFiles, setSheetFiles] = useState<Record<number, File | null>>({})
  const [sheetPreviews, setSheetPreviews] = useState<Record<number, string | null>>({})
  const [uploadedSheets, setUploadedSheets] = useState<Set<number>>(new Set())
  const [hasExistingGlyphs, setHasExistingGlyphs] = useState(false)
  
  const [uploading, setUploading] = useState(false)
  const [uploadPct, setUploadPct] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Check if profile already has extracted glyphs
  useEffect(() => {
    if (!profileId) return
    getGlyphs(Number(profileId))
      .then((glyphs) => {
        if (glyphs.length > 0) {
          setHasExistingGlyphs(true)
          const lowerExists = glyphs.some((g) => /^[a-z]$/.test(g.char))
          const upperExists = glyphs.some((g) => /^[A-Z]$/.test(g.char))
          const otherExists = glyphs.some((g) => /^[^a-zA-Z]$/.test(g.char))
          const set = new Set<number>()
          if (lowerExists) set.add(1)
          if (upperExists) set.add(2)
          if (otherExists) set.add(3)
          setUploadedSheets(set)
        }
      })
      .catch(() => {})
  }, [profileId])

  const currentFile = sheetFiles[activeSheet] ?? null
  const currentPreview = sheetPreviews[activeSheet] ?? null

  const handleFile = (f: File) => {
    const url = URL.createObjectURL(f)
    setSheetFiles((prev) => ({ ...prev, [activeSheet]: f }))
    setSheetPreviews((prev) => ({ ...prev, [activeSheet]: url }))
    setError(null)
  }

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f && f.type.startsWith('image/')) handleFile(f)
  }

  const onFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
  }

  const handleUpload = async () => {
    if (!currentFile || !profileId) return
    setUploading(true)
    setUploadPct(0)
    setError(null)
    try {
      const { job_id } = await uploadScan(
        Number(profileId),
        currentFile,
        activeSheet,
        (pct) => setUploadPct(pct)
      )
      setUploadedSheets((prev) => new Set(prev).add(activeSheet))
      navigate(`/profiles/${profileId}/processing/${job_id}`)
    } catch {
      setError('Upload failed. Please try again.')
    } finally {
      setUploading(false)
    }
  }

  const clearFile = () => {
    setSheetFiles((prev) => ({ ...prev, [activeSheet]: null }))
    setSheetPreviews((prev) => ({ ...prev, [activeSheet]: null }))
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-12">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-ink-900 mb-2">Upload Handwriting Scans</h1>
          <p className="text-ink-500">Upload clean photos or scans of your handwritten sheets.</p>
          <p className="text-sm text-ink-400 mt-1">
            Keep all four corner squares in the picture, and upload the original file rather than a copy sent
            through a chat app.
          </p>
        </div>
        {hasExistingGlyphs && (
          <button
            onClick={() => navigate(`/profiles/${profileId}/glyphs`)}
            className="flex items-center gap-1.5 text-sm text-ink-700 bg-ink-100 hover:bg-ink-200 px-3.5 py-2 rounded-lg font-medium transition-colors"
          >
            Go to Review
            <ArrowRight className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Sheet tabs */}
      <div className="flex gap-2 mb-8">
        {SHEETS.map((s) => {
          const hasFile = !!sheetFiles[s.number]
          const isUploaded = uploadedSheets.has(s.number)
          const isActive = activeSheet === s.number

          return (
            <button
              key={s.number}
              onClick={() => setActiveSheet(s.number)}
              className={`flex-1 flex flex-col items-center py-3 px-2 rounded-xl border-2 transition-all text-sm font-medium ${
                isActive
                  ? 'border-ink-700 bg-ink-50 text-ink-900 shadow-sm'
                  : 'border-ink-100 bg-white text-ink-500 hover:border-ink-300'
              }`}
            >
              <span className="font-semibold">{s.label}</span>
              <span className="text-xs text-ink-400 mt-0.5">{s.sublabel}</span>
              {isUploaded ? (
                <span className="mt-1 flex items-center gap-1 text-green-600 text-xs font-semibold">
                  <Check className="w-3 h-3" /> Extracted
                </span>
              ) : hasFile ? (
                <span className="mt-1 flex items-center gap-1 text-ink-700 text-xs font-semibold">
                  ● Ready to upload
                </span>
              ) : null}
            </button>
          )
        })}
      </div>

      {/* Drop zone / Selected image */}
      {!currentFile ? (
        <div
          onDrop={onDrop}
          onDragOver={(e) => e.preventDefault()}
          onClick={() => fileInputRef.current?.click()}
          className="border-2 border-dashed border-ink-300 rounded-2xl p-12 flex flex-col items-center justify-center gap-4 cursor-pointer hover:border-ink-500 hover:bg-ink-50/50 transition-all"
        >
          <div className="w-14 h-14 bg-ink-100 rounded-2xl flex items-center justify-center">
            <Upload className="w-7 h-7 text-ink-500" />
          </div>
          <div className="text-center">
            <p className="font-semibold text-ink-700 mb-1">
              Select or drop {SHEETS.find((s) => s.number === activeSheet)?.label} image
            </p>
            <p className="text-sm text-ink-400">
              {SHEETS.find((s) => s.number === activeSheet)?.sublabel} — JPG, PNG, TIFF accepted
            </p>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={onFileChange}
          />
        </div>
      ) : (
        <div className="border-2 border-ink-200 rounded-2xl overflow-hidden shadow-sm">
          <div className="relative">
            <img
              src={currentPreview!}
              alt="Scan preview"
              className="w-full object-contain max-h-80 bg-ink-50"
            />
            <button
              onClick={clearFile}
              className="absolute top-3 right-3 w-8 h-8 bg-white/90 hover:bg-white rounded-full shadow flex items-center justify-center transition-colors"
              title="Remove this image"
            >
              <X className="w-4 h-4 text-ink-700" />
            </button>
          </div>
          <div className="p-4 bg-white flex items-center gap-3 border-t border-ink-100">
            <ImageIcon className="w-4 h-4 text-ink-400 shrink-0" />
            <span className="text-sm text-ink-700 font-medium truncate flex-1">{currentFile.name}</span>
            <span className="text-xs text-ink-400">{(currentFile.size / 1024).toFixed(0)} KB</span>
          </div>
        </div>
      )}

      {/* Upload progress */}
      {uploading && (
        <div className="mt-4">
          <div className="flex justify-between text-sm text-ink-600 mb-1">
            <span>Uploading {SHEETS.find((s) => s.number === activeSheet)?.label}…</span>
            <span>{uploadPct}%</span>
          </div>
          <div className="w-full bg-ink-100 rounded-full h-2">
            <div
              className="bg-ink-700 h-2 rounded-full transition-all"
              style={{ width: `${uploadPct}%` }}
            />
          </div>
        </div>
      )}

      {error && (
        <div className="mt-4 bg-red-50 text-red-700 border border-red-200 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      <button
        onClick={handleUpload}
        disabled={!currentFile || uploading}
        className="mt-6 w-full bg-ink-800 text-paper py-3.5 rounded-xl font-semibold hover:bg-ink-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2 shadow-sm"
      >
        <Upload className="w-4 h-4" />
        {uploading
          ? `Uploading… ${uploadPct}%`
          : `Upload & Process ${SHEETS.find((s) => s.number === activeSheet)?.label}`}
      </button>

      {/* Hint if multiple sheets are ready */}
      {Object.values(sheetFiles).filter(Boolean).length > 1 && (
        <p className="text-center text-xs text-ink-400 mt-3">
          Tip: Upload each sheet one by one. Your selected images for other tabs remain saved!
        </p>
      )}
    </div>
  )
}
