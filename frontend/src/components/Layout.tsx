import { useEffect } from 'react'
import { Link, Outlet, matchPath, useLocation } from 'react-router-dom'
import { PenLine } from 'lucide-react'

/** Browser tab titles by route; the most specific pattern comes first. */
const PAGE_TITLES: [string, string][] = [
  ['/template', 'Template sheets'],
  ['/profiles', 'Profiles'],
  ['/profiles/:profileId/capture', 'Upload sheets'],
  ['/profiles/:profileId/processing/:jobId', 'Processing'],
  ['/profiles/:profileId/glyphs', 'Glyph review'],
  ['/profiles/:profileId/preview', 'Style preview'],
  ['/profiles/:profileId/compose', 'Composer'],
  ['/profiles/:profileId/export/batch/:batchId', 'Card set export'],
  ['/profiles/:profileId/export/:renderId', 'Export'],
]

export default function Layout() {
  const { pathname } = useLocation()
  useEffect(() => {
    const match = PAGE_TITLES.find(([pattern]) => matchPath(pattern, pathname))
    document.title = match ? `${match[1]} · NeverWrite` : 'NeverWrite: your handwriting on every card'
  }, [pathname])

  return (
    <div className="min-h-screen flex flex-col">
      {/* Nav */}
      <nav className="sticky top-0 z-50 bg-paper/90 backdrop-blur border-b border-ink-100">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2 font-semibold text-ink-800 hover:text-ink-600 transition-colors">
            <PenLine className="w-5 h-5" />
            <span>NeverWrite</span>
          </Link>
          <Link
            to="/profiles"
            className="text-sm text-ink-600 hover:text-ink-800 transition-colors font-medium"
          >
            My Profiles
          </Link>
        </div>
      </nav>

      {/* Page content */}
      <main className="flex-1">
        <Outlet />
      </main>

      {/* Footer */}
      <footer className="border-t border-ink-100 py-6 text-center text-sm text-ink-400">
        © 2026 NeverWrite · your own handwriting on every card
      </footer>
    </div>
  )
}
