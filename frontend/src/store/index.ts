import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { RenderSettings } from '../lib/api'

/** Mail-merge state: the uploaded CSV, the placeholder mapping and a seed per card. */
export interface MergeData {
  enabled: boolean
  fileName: string
  columns: string[]
  rows: string[][]                 // one array of cell values per CSV row, aligned with columns
  mapping: Record<string, number>  // placeholder → column index, for choices the user made
  seeds: number[]                  // one per row, so each card can be rerolled and regenerated alone
}

/** Everything the Composer needs to restore its work for one profile. */
export interface Draft {
  text: string
  settings: RenderSettings
  seed: number
  merge: MergeData
}

export const defaultSettings: RenderSettings = {
  page_size: 'A6',
  page_width_mm: 105,
  page_height_mm: 148,
  align: 'left',
  font_size: 10,
  line_spacing: 2.8, // multiple of the x-height; ascenders and descenders need ~2.5×
  ink_color: '#1a1a2e',
  margin_top: 12,
  margin_bottom: 12,
  margin_left: 12,
  margin_right: 12,
  messiness: 40,
  slant_correction: 0,
}

export const newSeed = () => Math.floor(Math.random() * 0xffffffff)

const emptyMerge: MergeData = {
  enabled: false,
  fileName: '',
  columns: [],
  rows: [],
  mapping: {},
  seeds: [],
}

/** Shown for a profile until its first edit, which saves it as that profile's draft. */
const freshDraft: Draft = {
  text: 'Thank you so much for being part of our day.\n\nWith love,\nAnu and Rahul',
  settings: defaultSettings,
  seed: newSeed(),
  merge: emptyMerge,
}

type DraftPatch = Partial<Draft> | ((draft: Draft) => Partial<Draft>)

interface NeverWriteStore {
  drafts: Record<string, Draft> // keyed by profile id
  updateDraft: (profileId: string, patch: DraftPatch) => void
}

export const useNeverWriteStore = create<NeverWriteStore>()(
  persist(
    (set) => ({
      drafts: {},
      updateDraft: (profileId, patch) =>
        set((s) => {
          const current = s.drafts[profileId] ?? freshDraft
          const changes = typeof patch === 'function' ? patch(current) : patch
          return { drafts: { ...s.drafts, [profileId]: { ...current, ...changes } } }
        }),
    }),
    {
      name: 'neverwrite-drafts',
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ drafts: s.drafts }),
      // Fill in fields added since a draft was saved
      merge: (persisted, current) => {
        const saved = (persisted as Partial<NeverWriteStore> | undefined)?.drafts ?? {}
        const drafts: Record<string, Draft> = {}
        for (const [id, d] of Object.entries(saved)) {
          drafts[id] = {
            ...freshDraft,
            ...d,
            settings: { ...defaultSettings, ...d.settings },
            merge: { ...emptyMerge, ...d.merge },
          }
        }
        return { ...current, drafts }
      },
    }
  )
)

/** The saved draft for a profile, with setters that save as they go. */
export function useDraft(profileId: string | undefined) {
  const key = profileId ?? ''
  const draft = useNeverWriteStore((s) => s.drafts[key]) ?? freshDraft
  const updateDraft = useNeverWriteStore((s) => s.updateDraft)
  const update = (patch: DraftPatch) => updateDraft(key, patch)

  return {
    ...draft,
    setText: (text: string) => update({ text }),
    updateSettings: (patch: Partial<RenderSettings>) =>
      update((d) => ({ settings: { ...d.settings, ...patch } })),
    shuffleSeed: () => update({ seed: newSeed() }),
    updateMerge: (patch: Partial<MergeData>) => update((d) => ({ merge: { ...d.merge, ...patch } })),
    rerollCard: (index: number) =>
      update((d) => ({
        merge: { ...d.merge, seeds: d.merge.seeds.map((s, i) => (i === index ? newSeed() : s)) },
      })),
    rerollAllCards: () =>
      update((d) => ({ merge: { ...d.merge, seeds: d.merge.rows.map(() => newSeed()) } })),
  }
}
