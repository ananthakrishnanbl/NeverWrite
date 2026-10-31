import { useNavigate } from 'react-router-dom'
import { PenLine, Download, Camera, ArrowRight } from 'lucide-react'

export default function Landing() {
  const navigate = useNavigate()

  return (
    <div className="max-w-5xl mx-auto px-4 py-16">
      {/* Hero */}
      <div className="text-center mb-16">
        <div className="inline-flex items-center gap-2 bg-ink-100 text-ink-600 rounded-full px-4 py-1.5 text-sm font-medium mb-6">
          <PenLine className="w-3.5 h-3.5" />
          Handwriting → Digital
        </div>
        <h1 className="text-5xl font-bold text-ink-900 mb-4 leading-tight">
          Your handwriting.<br />
          <span className="text-ink-500">Perfectly typed.</span>
        </h1>
        <p className="text-lg text-ink-500 max-w-xl mx-auto mb-10">
          Print a template, write your letters, scan it back — and type anything in your own handwriting forever.
        </p>
        <div className="flex items-center justify-center gap-4 flex-wrap">
          <button
            onClick={() => navigate('/profiles')}
            className="bg-ink-800 text-paper px-7 py-3 rounded-lg font-semibold hover:bg-ink-700 transition-colors flex items-center gap-2"
          >
            Create my handwriting profile
            <ArrowRight className="w-4 h-4" />
          </button>
          <button
            onClick={() => navigate('/profiles')}
            className="border border-ink-300 text-ink-700 px-6 py-3 rounded-lg font-medium hover:bg-ink-50 transition-colors"
          >
            I already have a profile
          </button>
        </div>
      </div>

      {/* Before / After visual */}
      <div className="grid grid-cols-2 gap-6 mb-20 max-w-2xl mx-auto">
        <div className="bg-white border border-ink-200 rounded-xl p-6 shadow-sm">
          <p className="text-xs text-ink-400 font-medium uppercase tracking-wider mb-3">Before</p>
          <p className="font-mono text-ink-700 text-xl">Hello World</p>
          <p className="text-xs text-ink-400 mt-2">← typed text</p>
        </div>
        <div className="bg-white border-2 border-ink-400 rounded-xl p-6 shadow-md relative">
          <p className="text-xs text-ink-500 font-medium uppercase tracking-wider mb-3">After</p>
          <div className="h-8 flex items-center">
            <div className="w-full h-px bg-ink-200 relative">
              {/* Simulated handwriting placeholder */}
              <svg viewBox="0 0 200 30" className="w-full h-8 -mt-4" fill="none" stroke="currentColor">
                <path
                  d="M5,20 Q15,8 25,18 Q35,28 45,16 Q55,5 65,20 Q75,30 85,18 Q95,8 105,20 Q115,28 125,16 Q135,5 145,20 Q155,30 165,14 Q175,5 185,20"
                  className="stroke-ink-600"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  fill="none"
                />
              </svg>
            </div>
          </div>
          <p className="text-xs text-ink-500 mt-2">← in your handwriting</p>
          <div className="absolute -top-2 -right-2 bg-ink-700 text-paper text-xs px-2 py-0.5 rounded-full font-medium">
            Your style ✓
          </div>
        </div>
      </div>

      {/* 3-step explainer */}
      <div className="mb-20">
        <h2 className="text-2xl font-semibold text-ink-800 text-center mb-10">How it works</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            {
              icon: <Download className="w-6 h-6" />,
              step: '1',
              title: 'Print template',
              desc: 'Download our A4 template sheets and print them out. Each sheet contains letters, numbers and symbols to trace.',
            },
            {
              icon: <PenLine className="w-6 h-6" />,
              step: '2',
              title: 'Write & scan',
              desc: 'Write every character in your natural handwriting, then snap a photo or scan the pages.',
            },
            {
              icon: <Camera className="w-6 h-6" />,
              step: '3',
              title: 'Type anything',
              desc: 'Upload your scans and NeverWrite builds a personal font. Now type any text and download it in your own hand.',
            },
          ].map((card) => (
            <div key={card.step} className="bg-white border border-ink-100 rounded-xl p-6 shadow-sm hover:shadow-md transition-shadow">
              <div className="w-10 h-10 bg-ink-100 text-ink-700 rounded-lg flex items-center justify-center mb-4">
                {card.icon}
              </div>
              <div className="text-xs text-ink-400 font-semibold uppercase tracking-wider mb-1">Step {card.step}</div>
              <h3 className="font-semibold text-ink-800 mb-2">{card.title}</h3>
              <p className="text-sm text-ink-500 leading-relaxed">{card.desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Bottom CTA */}
      <div className="text-center bg-ink-900 rounded-2xl p-12 text-paper">
        <h2 className="text-3xl font-bold mb-4">Ready to start?</h2>
        <p className="text-ink-300 mb-8">Takes about 15 minutes to set up. Your handwriting, forever.</p>
        <button
          onClick={() => navigate('/profiles')}
          className="bg-paper text-ink-900 px-8 py-3 rounded-lg font-semibold hover:bg-ink-100 transition-colors"
        >
          Get started for free
        </button>
      </div>
    </div>
  )
}
