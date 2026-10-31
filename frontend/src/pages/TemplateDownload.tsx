import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Download, PenLine, Check, AlertCircle } from 'lucide-react'

const SHEETS = [
  {
    sheet: 1,
    title: 'Sheet 1 — Lowercase (a–z)',
    chars: 'Row 1: a b c d e f g h i j k l m  |  Row 2: n o p q r s t u v w x y z',
    desc: '2 rows × 13 columns. Plenty of space to write naturally without squeezing.',
  },
  {
    sheet: 2,
    title: 'Sheet 2 — Uppercase (A–Z)',
    chars: 'Row 1: A B C D E F G H I J K L M  |  Row 2: N O P Q R S T U V W X Y Z',
    desc: '2 rows × 13 columns. Generous cell dimensions.',
  },
  {
    sheet: 3,
    title: 'Sheet 3 — Digits & Punctuation',
    chars: 'Row 1: 0 1 2 3 4 5 6 7 8 9  |  Row 2 & 3: . , ! ? ; : \' " - ( )',
    desc: 'Numbers and common punctuation marks.',
  },
]

const TIPS = [
  'Use a consistent pen — ballpoint or rollerball works best.',
  'Write at your natural size; do not try to fit within the boxes exactly.',
  'Maintain your normal slant and speed for authentic results.',
  'Ensure good lighting when scanning; avoid shadows.',
  'Scan or photograph at least 300 dpi for best extraction quality.',
  'Only one sample per character is needed. If you rescan, it replaces that character.',
]

export default function TemplateDownload() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const profileId = searchParams.get('profileId')
  const [tooltip, setTooltip] = useState<number | null>(null)

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
          <div key={s.sheet} className="bg-white border border-ink-100 rounded-xl p-6 shadow-sm">
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <h2 className="font-semibold text-ink-800 mb-1">{s.title}</h2>
                <p className="text-sm text-ink-500 mb-3">{s.desc}</p>
                <div className="bg-ink-50 rounded-lg px-4 py-3 font-mono text-ink-700 text-sm tracking-widest">
                  {s.chars}
                </div>
              </div>
              <div className="relative">
                <button
                  onMouseEnter={() => setTooltip(s.sheet)}
                  onMouseLeave={() => setTooltip(null)}
                  className="flex items-center gap-2 border border-ink-200 text-ink-500 px-4 py-2 rounded-lg text-sm cursor-not-allowed hover:bg-ink-50 transition-colors"
                  disabled
                >
                  <Download className="w-4 h-4" />
                  Download PDF
                </button>
                {tooltip === s.sheet && (
                  <div className="absolute right-0 top-full mt-2 bg-ink-800 text-paper text-xs rounded-lg px-3 py-2 whitespace-nowrap z-10">
                    Coming soon
                  </div>
                )}
              </div>
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
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 flex gap-3 mb-8">
        <AlertCircle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
        <p className="text-sm text-amber-800">
          While PDF download is coming soon, you can still upload scanned images of any hand-drawn character grid.
          NeverWrite will try to auto-detect and extract glyphs.
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
