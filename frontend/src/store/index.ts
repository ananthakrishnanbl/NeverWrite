import { create } from 'zustand'
import { RenderSettings } from '../lib/api'

interface InkTwinStore {
  currentProfileId: number | null
  setCurrentProfileId: (id: number) => void

  composerText: string
  setComposerText: (t: string) => void

  renderSettings: RenderSettings
  updateRenderSettings: (patch: Partial<RenderSettings>) => void

  seed: number
  shuffleSeed: () => void
}

const defaultSettings: RenderSettings = {
  font_size: 12,
  line_spacing: 1.2,
  ink_color: '#1a1a2e',
  paper_type: 'plain',
  margin_top: 20,
  margin_bottom: 20,
  margin_left: 25,
  margin_right: 20,
  messiness: 40,
  slant_correction: 0,
}

export const useInkTwinStore = create<InkTwinStore>((set) => ({
  currentProfileId: null,
  setCurrentProfileId: (id) => set({ currentProfileId: id }),

  composerText: 'The quick brown fox jumps over the lazy dog.',
  setComposerText: (t) => set({ composerText: t }),

  renderSettings: { ...defaultSettings },
  updateRenderSettings: (patch) =>
    set((s) => ({ renderSettings: { ...s.renderSettings, ...patch } })),

  seed: Math.floor(Math.random() * 0xffffffff),
  shuffleSeed: () => set({ seed: Math.floor(Math.random() * 0xffffffff) }),
}))
