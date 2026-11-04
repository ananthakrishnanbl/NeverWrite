import type { PlotFormat, PlotSettings } from './api'

/** Defaults of PlotQuery in backend/app/routers/plots.py: a guess for a 3D printer holding a fineliner. */
export const DEFAULT_PLOT_SETTINGS: PlotSettings = {
  pen_width_mm: 0.4,
  draw_speed: 2400,
  travel_speed: 6000,
  pen_up_z: 2,
  pen_down_z: 0,
  z_speed: 300,
  lift_time_s: 0.15,
  offset_x: 0,
  offset_y: 0,
}

/** `machine` completes the sentence "For ...". */
export const FORMAT_INFO: Record<PlotFormat, { label: string; ext: string; machine: string; note: string }> = {
  svg: {
    label: 'SVG',
    ext: '.svg',
    machine: 'Inkscape, sign-shop software and most plotters',
    note: 'Plain lines in millimetres, nothing filled. Import it into the software that drives the plotter or cutter.',
  },
  gcode: {
    label: 'G-code',
    ext: '.gcode',
    machine: 'a 3D printer with a pen fixed beside the nozzle',
    note:
      'Lower the pen until it just touches the card, then start the file. It homes X and Y only, never Z, and has ' +
      'no heater commands, so the nozzle and bed stay cold.',
  },
  hpgl: {
    label: 'HPGL',
    ext: '.plt',
    machine: 'vinyl cutters and older pen plotters',
    note: 'On a cutter, turn blade offset compensation off, or the curves come out distorted.',
  },
}

/** A machine setting the Export page lets you change, and the formats it matters for. */
export interface PlotField {
  key: keyof PlotSettings
  label: string
  unit: string
  step: number
  formats: PlotFormat[]
}

export const PLOT_FIELDS: PlotField[] = [
  { key: 'draw_speed', label: 'Drawing speed', unit: 'mm/min', step: 100, formats: ['svg', 'gcode', 'hpgl'] },
  { key: 'travel_speed', label: 'Travel speed', unit: 'mm/min', step: 100, formats: ['svg', 'gcode', 'hpgl'] },
  { key: 'pen_width_mm', label: 'Pen width', unit: 'mm', step: 0.05, formats: ['svg'] },
  { key: 'lift_time_s', label: 'Pen lift and drop', unit: 's', step: 0.05, formats: ['svg', 'hpgl'] },
  { key: 'pen_down_z', label: 'Pen down Z', unit: 'mm', step: 0.1, formats: ['gcode'] },
  { key: 'pen_up_z', label: 'Pen up Z', unit: 'mm', step: 0.1, formats: ['gcode'] },
  { key: 'z_speed', label: 'Z speed', unit: 'mm/min', step: 50, formats: ['gcode'] },
  { key: 'offset_x', label: 'Card corner X', unit: 'mm', step: 1, formats: ['gcode', 'hpgl'] },
  { key: 'offset_y', label: 'Card corner Y', unit: 'mm', step: 1, formats: ['gcode', 'hpgl'] },
]

/** Why these settings would be refused, or null when they are fine. */
export function plotSettingsProblem(s: PlotSettings): string | null {
  if (Object.values(s).some((v) => !Number.isFinite(v))) return 'Fill in every machine setting.'
  if (s.draw_speed <= 0 || s.travel_speed <= 0 || s.z_speed <= 0) return 'Speeds must be above zero.'
  if (s.pen_width_mm <= 0) return 'Pen width must be above zero.'
  if (s.pen_up_z <= s.pen_down_z) return 'Pen up Z must be higher than pen down Z.'
  if (s.lift_time_s < 0 || s.offset_x < 0 || s.offset_y < 0) return 'Lift time and card corner can’t be negative.'
  return null
}

const STORAGE_KEY = 'neverwrite.plotter'

/** The machine settings and format last used in this browser; one machine is shared by every profile. */
export function loadPlotPrefs(): { format: PlotFormat; settings: PlotSettings } {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    const format: PlotFormat = ['svg', 'gcode', 'hpgl'].includes(saved.format) ? saved.format : 'svg'
    const settings = { ...DEFAULT_PLOT_SETTINGS }
    for (const key of Object.keys(settings) as (keyof PlotSettings)[]) {
      if (typeof saved.settings?.[key] === 'number') settings[key] = saved.settings[key]
    }
    return { format, settings }
  } catch {
    return { format: 'svg', settings: { ...DEFAULT_PLOT_SETTINGS } }
  }
}

export function savePlotPrefs(format: PlotFormat, settings: PlotSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ format, settings }))
  } catch {
    // storage blocked or full: the settings just won't be remembered
  }
}

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours) return `${hours} h ${String(minutes).padStart(2, '0')} min`
  if (minutes) return `${minutes} min ${String(secs).padStart(2, '0')} s`
  return `${secs} s`
}

export function formatLength(mm: number): string {
  return mm >= 1000 ? `${(mm / 1000).toFixed(2)} m` : `${Math.round(mm)} mm`
}
