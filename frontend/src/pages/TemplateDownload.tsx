import { useNavigate, useSearchParams } from 'react-router-dom'
import { Download, PenLine, Check, Info } from 'lucide-react'
import { SHEETS, VARIANTS_PER_CHAR, templatePdfUrl } from '../lib/sheets'

const SHEET_TITLES: Record<number, string> = {
  1: 'Sheet 1: Lowercase (a to z)',
  2: 'Sheet 2: Uppercase (A to Z)',
  3: 'Sheet 3: Digits & Punctuation',
}

const TIPS = [
  'Print at 100% size on plain white A4 paper. Colour printing is best, but black and white works too.',
  'Use a dark pen: black or dark blue ballpoint, gel or fountain pen. Pencil is too faint to pick up.',
  `Write each character ${VARIANTS_PER_CHAR} times, once per box, sitting on the solid blue line. Let them differ a little, the way your writing naturally does.`,
  'Write at your everyday size and keep each letter inside its box. Ink that touches the box edge gets flagged for review.',
  'Scan the sheet, or photograph it from straight above in good light with all four black corner squares in the picture.',
  'Upload the original file. If the photo is on your phone, send it as a document or by cable; a photo sent as a chat image is compressed and loses the detail of your strokes.',
  'Rescanning a sheet only replaces the boxes you filled in, so you can redo a few characters on a fresh copy.',
]

export default function TemplateDownload() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const profileId = searchParams.get('profileId')

  const handleContinue = () => {
    if (profileId) {
      navigate(`/profiles/${profileId}/capture`)
    } else {
      navigate('/profiles')
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-12">
      <div className="mb-10">
        <h1 className="text-3xl font-bold text-ink-900 mb-2">Download Templates</h1>
        <p className="text-ink-500">
          Print these sheets, write every character in your natural handwriting, then come back to upload them.
        </p>
      </div>

      {/* Sheet cards */}
      <div className="space-y-4 mb-12">
        {SHEETS.map((s) => (
          <div key={s.number} className="bg-white border border-ink-100 rounded-xl p-6 shadow-sm">
            <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
              <div className="flex-1 min-w-0">
                <h2 className="font-semibold text-ink-800 mb-1">{SHEET_TITLES[s.number]}</h2>
                <p className="text-sm text-ink-500 mb-3">
                  {s.charset.length} characters, {VARIANTS_PER_CHAR} boxes each, on one A4 page.
                </p>
                <div className="bg-ink-50 rounded-lg px-4 py-3 font-mono text-ink-700 text-sm tracking-widest break-all">
                  {Array.from(s.charset).join(' ')}
                </div>
              </div>
              <a
                href={templatePdfUrl(s.number)}
                download
                className="flex items-center justify-center gap-2 bg-ink-800 text-paper px-4 py-2 rounded-lg text-sm font-medium hover:bg-ink-700 transition-colors shrink-0"
              >
                <Download className="w-4 h-4" />
                Download PDF
              </a>
            </div>
          </div>
        ))}
      </div>

      {/* Writing tips */}
      <div className="mb-12">
        <h2 className="font-semibold text-ink-800 mb-4 flex items-center gap-2">
          <PenLine className="w-4 h-4" />
          Writing tips for best results
        </h2>
        <ul className="space-y-2">
          {TIPS.map((tip, i) => (
            <li key={i} className="flex items-start gap-3 text-sm text-ink-600">
              <Check className="w-4 h-4 text-ink-400 mt-0.5 shrink-0" />
              {tip}
            </li>
          ))}
        </ul>
      </div>

      {/* Notice */}
      <div className="bg-ink-50 border border-ink-100 rounded-xl p-4 flex gap-3 mb-8">
        <Info className="w-5 h-5 text-ink-500 shrink-0 mt-0.5" />
        <p className="text-sm text-ink-600">
          A hand-drawn sheet without the corner squares still works, but then characters are read in order with
          one sample each, so a missed or extra mark shifts the labels. The printed sheets avoid that.
        </p>
      </div>

      {/* CTA */}
      <div className="text-center">
        <button
          onClick={handleContinue}
          className="bg-ink-800 text-paper px-8 py-3 rounded-lg font-semibold hover:bg-ink-700 transition-colors"
        >
          I've filled my templates →
        </button>
        <p className="text-sm text-ink-400 mt-3">
          {profileId ? 'Continue to upload your scans.' : 'Select or create a profile to upload scans.'}
        </p>
      </div>
    </div>
  )
}
