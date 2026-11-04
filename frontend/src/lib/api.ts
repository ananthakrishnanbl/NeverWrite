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
  metrics_json: string // JSON-encoded ProfileMetrics, "{}" until a sheet is processed
  glyph_count?: number
}

/** Stored x-height of a profile in glyph pixels, or 0 if unknown. */
export function profileXHeight(profile: Profile): number {
  try {
    const metrics = JSON.parse(profile.metrics_json || '{}') as Partial<ProfileMetrics>
    return metrics.x_height ?? 0
  } catch {
    return 0
  }
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
  issue?: string | null // why extraction held this variant back for review
  has_strokes?: boolean // drawn on the Draw page, so a pen plotter can write it
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
  warning?: string | null
  stage?: string
}

export type PageSize = 'A6' | 'A7' | 'DL' | 'A4' | 'custom'

export interface RenderSettings {
  page_size: PageSize    // preset name; the size itself is in page_width_mm / page_height_mm
  page_width_mm: number
  page_height_mm: number
  align: 'left' | 'center'
  font_size: number
  line_spacing: number
  ink_color: string
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
  settings_json: string // JSON-encoded RenderSettings
  seed: number
  status: string
  output_path: string | null
  error: string | null
  missing_chars: string[] // characters drawn as gaps because they have no glyph
  created_at: string
}

export interface BatchCard {
  label: string
  text: string
  seed: number
}

export interface RenderBatchCard extends BatchCard {
  missing_chars: string[]
}

export interface RenderBatch {
  id: number
  profile_id: number
  settings_json: string // JSON-encoded RenderSettings
  cards: RenderBatchCard[]
  status: string
  output_path: string | null
  zip_path: string | null
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

export async function deleteProfile(id: number): Promise<void> {
  await http.delete(`/profiles/${id}`)
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

/** One stroke: [x, y] points in millimetres of the writing box, in the order the pen moved. */
export type DrawnStroke = [number, number][]

/** Save one variant of a character drawn on the Draw page, replacing whatever that slot held. */
export async function saveDrawnGlyph(
  profileId: number,
  body: { char: string; variant_index: number; strokes: DrawnStroke[] }
): Promise<Glyph> {
  const res = await http.post<Glyph>(`/profiles/${profileId}/drawn-glyphs`, body)
  return res.data
}

// ─── Renders ──────────────────────────────────────────────────────────────────

export async function startRender(
  profileId: number,
  req: RenderRequest
): Promise<{ job_id: string; render_id: number }> {
  const res = await http.post<{ job_id: string; render_id: number }>(
    `/profiles/${profileId}/render`,
    req
  )
  return res.data
}

export async function getRender(renderId: number): Promise<RenderJob> {
  const res = await http.get<RenderJob>(`/renders/${renderId}`)
  return res.data
}

export function getRenderFileUrl(renderId: number): string {
  return `/api/renders/${renderId}/file`
}

/** Render every card into one PDF (a page per card) plus a zip of single-card PDFs. */
export async function startBatch(
  profileId: number,
  req: { settings: RenderSettings; cards: BatchCard[] }
): Promise<{ job_id: string; batch_id: number }> {
  const res = await http.post<{ job_id: string; batch_id: number }>(
    `/profiles/${profileId}/render-batch`,
    req
  )
  return res.data
}

export async function getBatch(batchId: number): Promise<RenderBatch> {
  const res = await http.get<RenderBatch>(`/render-batches/${batchId}`)
  return res.data
}

export function getBatchFileUrl(batchId: number, kind: 'pdf' | 'zip'): string {
  return `/api/render-batches/${batchId}/${kind === 'pdf' ? 'file' : 'zip'}`
}

// ─── Templates ────────────────────────────────────────────────────────────────

export async function getTemplates(): Promise<TemplateInfo[]> {
  const res = await http.get<TemplateInfo[]>('/templates')
  return res.data
}

// ─── Plot files ───────────────────────────────────────────────────────────────

export type PlotFormat = 'svg' | 'gcode' | 'hpgl'

/** Machine settings for plot files. Speeds are mm/min. Mirrors PlotQuery in backend/app/routers/plots.py. */
export interface PlotSettings {
  pen_width_mm: number
  draw_speed: number
  travel_speed: number
  pen_up_z: number
  pen_down_z: number
  z_speed: number
  lift_time_s: number
  offset_x: number
  offset_y: number
}

export interface PlotSummary {
  format: PlotFormat
  page: number
  page_count: number
  width_mm: number
  height_mm: number
  strokes: number
  pen_lifts: number
  draw_mm: number
  travel_mm: number
  seconds: number
  unplottable_chars: string[] // on the card, but drawn from a scan, so left out of the plot
  missing_chars: string[]     // no glyph at all
  clipped_strokes: number     // cut at the card edge
}

export interface BatchPlotSummary {
  format: PlotFormat
  cards: (PlotSummary & { label: string })[]
  total_seconds: number
  total_draw_mm: number
  total_pen_lifts: number
}

function plotQuery(format: PlotFormat, settings: PlotSettings, extra: Record<string, number> = {}): string {
  const params = new URLSearchParams({ format })
  for (const [key, value] of Object.entries({ ...settings, ...extra })) params.set(key, String(value))
  return params.toString()
}

/** The API's explanation for a failed request, or `fallback`. */
export function apiError(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg).replace(/^Value error, /, '')
  }
  return fallback
}

export async function getRenderPlot(
  renderId: number, format: PlotFormat, settings: PlotSettings, page = 1
): Promise<PlotSummary> {
  const res = await http.get<PlotSummary>(`/renders/${renderId}/plot?${plotQuery(format, settings, { page })}`)
  return res.data
}

export function renderPlotFileUrl(renderId: number, format: PlotFormat, settings: PlotSettings, page = 1): string {
  return `/api/renders/${renderId}/plot/file?${plotQuery(format, settings, { page })}`
}

export async function getBatchPlot(batchId: number, format: PlotFormat, settings: PlotSettings): Promise<BatchPlotSummary> {
  const res = await http.get<BatchPlotSummary>(`/render-batches/${batchId}/plot?${plotQuery(format, settings)}`)
  return res.data
}

export function batchPlotFileUrl(batchId: number, format: PlotFormat, settings: PlotSettings, card: number): string {
  return `/api/render-batches/${batchId}/plot/file?${plotQuery(format, settings, { card })}`
}

export function batchPlotZipUrl(batchId: number, format: PlotFormat, settings: PlotSettings): string {
  return `/api/render-batches/${batchId}/plot/zip?${plotQuery(format, settings)}`
}
