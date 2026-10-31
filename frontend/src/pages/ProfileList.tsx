import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, PenLine, Camera, ChevronRight, User, Trash2 } from 'lucide-react'
import { listProfiles, createProfile, deleteProfile, Profile } from '../lib/api'

export default function ProfileList() {
  const navigate = useNavigate()
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showNewForm, setShowNewForm] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    listProfiles()
      .then(setProfiles)
      .catch(() => setError('Failed to load profiles.'))
      .finally(() => setLoading(false))
  }, [])

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreating(true)
    try {
      const profile = await createProfile(newName.trim())
      navigate(`/template?profileId=${profile.id}`)
    } catch {
      setError('Failed to create profile.')
      setCreating(false)
    }
  }

  const handleDelete = async (profile: Profile) => {
    if (!window.confirm(`Delete "${profile.name}" and all of its handwriting samples? This can't be undone.`)) return
    try {
      await deleteProfile(profile.id)
      setProfiles((prev) => prev.filter((p) => p.id !== profile.id))
    } catch {
      setError('Failed to delete profile.')
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-12">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-ink-900">My Profiles</h1>
          <p className="text-ink-500 mt-1">Each profile captures a unique handwriting style.</p>
        </div>
        <button
          onClick={() => setShowNewForm(true)}
          className="flex items-center gap-2 bg-ink-800 text-paper px-4 py-2 rounded-lg font-medium hover:bg-ink-700 transition-colors text-sm"
        >
          <Plus className="w-4 h-4" />
          New Profile
        </button>
      </div>

      {/* New profile form */}
      {showNewForm && (
        <div className="mb-6 bg-white border-2 border-ink-300 rounded-xl p-6 shadow-sm">
          <h2 className="font-semibold text-ink-800 mb-4">Name your profile</h2>
          <div className="flex gap-3">
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              placeholder="e.g. My everyday handwriting"
              className="flex-1 border border-ink-200 rounded-lg px-4 py-2.5 text-ink-900 placeholder-ink-400 focus:outline-none focus:border-ink-500 transition-colors"
              autoFocus
            />
            <button
              onClick={handleCreate}
              disabled={creating || !newName.trim()}
              className="bg-ink-800 text-paper px-5 py-2.5 rounded-lg font-medium hover:bg-ink-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {creating ? 'Creating…' : 'Create'}
            </button>
            <button
              onClick={() => { setShowNewForm(false); setNewName('') }}
              className="border border-ink-200 text-ink-600 px-4 py-2.5 rounded-lg hover:bg-ink-50 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="mb-4 bg-red-50 text-red-700 border border-red-200 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-8 h-8 border-2 border-ink-300 border-t-ink-700 rounded-full animate-spin" />
        </div>
      ) : profiles.length === 0 ? (
        <div className="text-center py-20 border-2 border-dashed border-ink-200 rounded-2xl">
          <User className="w-12 h-12 text-ink-300 mx-auto mb-4" />
          <h2 className="text-lg font-semibold text-ink-700 mb-2">No profiles yet</h2>
          <p className="text-ink-400 mb-6">Create your first handwriting profile to get started.</p>
          <button
            onClick={() => setShowNewForm(true)}
            className="bg-ink-800 text-paper px-6 py-2.5 rounded-lg font-medium hover:bg-ink-700 transition-colors inline-flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Create your first profile
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {profiles.map((p) => (
            <div
              key={p.id}
              className="bg-white border border-ink-100 rounded-xl p-5 shadow-sm hover:shadow-md transition-shadow flex items-center justify-between"
            >
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-ink-100 rounded-lg flex items-center justify-center">
                  <PenLine className="w-5 h-5 text-ink-600" />
                </div>
                <div>
                  <h3 className="font-semibold text-ink-900">{p.name}</h3>
                  <p className="text-sm text-ink-400">
                    Created {new Date(p.created_at).toLocaleDateString()}
                    {p.glyph_count !== undefined && p.glyph_count > 0
                      ? ` · ${p.glyph_count} glyphs`
                      : ''}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleDelete(p)}
                  className="p-1.5 rounded-lg text-ink-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                  title="Delete profile"
                  aria-label={`Delete ${p.name}`}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
                <button
                  onClick={() => navigate(`/profiles/${p.id}/capture`)}
                  className="flex items-center gap-1.5 text-sm border border-ink-200 text-ink-600 px-3 py-1.5 rounded-lg hover:bg-ink-50 transition-colors"
                >
                  <Camera className="w-3.5 h-3.5" />
                  Add samples
                </button>
                <button
                  onClick={() => navigate(`/profiles/${p.id}/compose`)}
                  className="flex items-center gap-1.5 text-sm bg-ink-800 text-paper px-3 py-1.5 rounded-lg hover:bg-ink-700 transition-colors"
                >
                  Open
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
