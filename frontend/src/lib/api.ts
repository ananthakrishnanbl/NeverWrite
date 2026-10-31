import axios from 'axios'

const http = axios.create({ baseURL: '/api' })

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ProfileMetrics {
  x_height: number
  stroke_w: number
  slant: number
  avg_char_gap: number
  avg_word_gap: number
}

export interface Profile {
  id: number
  name: string
  created_at: string
  metrics: ProfileMetrics | null
  glyph_count?: number
}

export interface Glyph {
  id: number
  profile_id: number
  char: string
  variant_index: number
  mask_path: string
  svg_path: string | null
  width: number
  height: number
  baseline_offset: number
  bearing_left: number
  bearing_right: number
  accepted: boolean
}

export interface GlyphPatch {
  accepted: boolean
  bearing_left: number
  bearing_right: number
  baseline_offset: number
}

export interface Job {
  id: string
  type: string
  status: 'pending' | 'running' | 'done' | 'error'
  progress: number
  meta: Record<string, unknown>
  error: string | null
  stage?: string
}

export interface RenderSettings {
  font_size: number
  line_spacing: number
  ink_color: string
  paper_type: 'plain' | 'ruled' | 'grid' | 'none'
  margin_top: number
  margin_bottom: number
  margin_left: number
  margin_right: number
  messiness: number
  slant_correction: number
}

export interface RenderRequest {
  text: string
  settings: RenderSettings
  seed: number
}

export interface RenderJob {
  id: number
  profile_id: number
  text: string
  settings: RenderSettings
  seed: number
  status: string
  output_path: string | null
  error: string | null
  created_at: string
}

export interface TemplateInfo {
  sheet_number: number
  characters: string[]
  description: string
}

// ─── Profiles ─────────────────────────────────────────────────────────────────

export async function createProfile(name: string): Promise<Profile> {
  const res = await http.post<Profile>('/profiles', { name })
  return res.data
}

export async function listProfiles(): Promise<Profile[]> {
  const res = await http.get<Profile[]>('/profiles')
  return res.data
}

export async function getProfile(id: number): Promise<Profile> {
  const res = await http.get<Profile>(`/profiles/${id}`)
  return res.data
}

// ─── Scans ────────────────────────────────────────────────────────────────────

export async function uploadScan(
  profileId: number,
  file: File,
  sheetNumber: number,
  onProgress?: (pct: number) => void
): Promise<{ job_id: string }> {
  const form = new FormData()
  form.append('file', file)
  form.append('sheet_number', String(sheetNumber))
  const res = await http.post<{ job_id: string }>(
    `/profiles/${profileId}/scans`,
    form,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (evt) => {
        if (onProgress && evt.total) {
          onProgress(Math.round((evt.loaded / evt.total) * 100))
        }
      },
    }
  )
  return res.data
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────

export async function getJob(jobId: string): Promise<Job> {
  const res = await http.get<Job>(`/jobs/${jobId}`)
  return res.data
}

export function pollJob(
  jobId: string,
  onUpdate: (job: Job) => void,
  intervalMs = 1500
): () => void {
  let active = true
  const tick = async () => {
    if (!active) return
    try {
      const job = await getJob(jobId)
      onUpdate(job)
      if (job.status === 'done' || job.status === 'error') {
        active = false
        return
      }
    } catch {
      // swallow transient errors
    }
    if (active) setTimeout(tick, intervalMs)
  }
  tick()
  return () => { active = false }
}

// ─── Glyphs ───────────────────────────────────────────────────────────────────

export async function getGlyphs(profileId: number): Promise<Glyph[]> {
  const res = await http.get<Glyph[]>(`/profiles/${profileId}/glyphs`)
  return res.data
}

export async function patchGlyph(
  glyphId: number,
  patch: Partial<GlyphPatch>
): Promise<Glyph> {
  const res = await http.patch<Glyph>(`/glyphs/${glyphId}`, patch)
  return res.data
}

// ─── Renders ──────────────────────────────────────────────────────────────────

export async function startRender(
  profileId: number,
  req: RenderRequest
): Promise<{ job_id: string; render_id: number }> {
  const res = await http.post<{ job_id: string; render_id: number }>(
    `/profiles/${profileId}/renders`,
    req
  )
  return res.data
}

export async function getRender(renderId: number): Promise<RenderJob> {
  const res = await http.get<RenderJob>(`/renders/${renderId}`)
  return res.data
}

export function getRenderFileUrl(renderId: number): string {
  return `/files/renders/${renderId}/output.pdf`
}

// ─── Templates ────────────────────────────────────────────────────────────────

export async function getTemplates(): Promise<TemplateInfo[]> {
  const res = await http.get<TemplateInfo[]>('/templates')
  return res.data
}
