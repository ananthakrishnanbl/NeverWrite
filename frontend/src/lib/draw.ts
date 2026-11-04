/**
 * The Draw page's writing box. It has the size and guides of a box on the
 * printed sheet (backend/workers/template.py), so drawn and scanned letters
 * come out the same size. Strokes are sent in these millimetres, y down.
 */
export const BOX_W_MM = 18
export const BOX_H_MM = 21
export const BASELINE_MM = 13 // from the top of the box
export const X_HEIGHT_MM = 4

/** Line width of a saved drawn letter, in box mm. STROKE_WIDTH_MM in backend/workers/strokes.py. */
export const STROKE_WIDTH_MM = 0.45

/** Points closer than this to the previous one are noise from the digitiser. */
export const MIN_STEP_MM = 0.05

/** URL of a glyph's mask image. */
export function maskUrl(path: string): string {
  const clean = path.replace(/\\/g, '/')
  return clean.startsWith('/files/') ? clean : `/files/${clean.replace(/^\//, '')}`
}
