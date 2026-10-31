import { Link, Outlet } from 'react-router-dom'
import { PenLine } from 'lucide-react'

export default function Layout() {
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
        © 2024 NeverWrite — your handwriting, digitized
      </footer>
    </div>
  )
}
