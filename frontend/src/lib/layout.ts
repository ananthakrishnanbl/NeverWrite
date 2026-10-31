import type { Glyph, PageSize, RenderSettings } from './api'
import { mulberry32, randRange, randInt } from './prng'

export interface PlacedGlyph {
  glyph: Glyph
  x: number        // canvas px
  y: number        // baseline y in canvas px
  scale: number    // uniform scale factor
  rotation: number // radians
}

export interface LayoutResult {
  pages: PlacedGlyph[][]
  pageWidthPx: number
  pageHeightPx: number
}

/** Preset page sizes in mm. DL is the landscape envelope. */
export const PAGE_PRESETS: Record<Exclude<PageSize, 'custom'>, { label: string; width: number; height: number }> = {
  A6: { label: 'A6 card', width: 105, height: 148 },
  A7: { label: 'A7 card', width: 74, height: 105 },
  DL: { label: 'DL envelope', width: 220, height: 110 },
  A4: { label: 'A4 page', width: 210, height: 297 },
}

/** Canvas preview and layout work at 96 dpi. */
const PX_PER_MM = 96 / 25.4 // ≈ 3.7795 px/mm

// Points to pixels: 1 pt = 1/72 inch = 96/72 px at 96 dpi
const PT_TO_PX = 96 / 72

// Letters with no ascender or descender; their height is the x-height
const X_HEIGHT_CHARS = new Set('acemnorsuvwxz')

/** Page size in 96 dpi pixels, rounded the same way as the PDF renderer. */
export function pageSizePx(settings: RenderSettings): [number, number] {
  return [
    Math.round(settings.page_width_mm * PX_PER_MM),
    Math.round(settings.page_height_mm * PX_PER_MM),
  ]
}

/** Cards and envelopes must fit on one page; only A4 may run onto more. */
export function isSinglePage(settings: RenderSettings): boolean {
  return settings.page_size !== 'A4'
}

/**
 * Characters in `text` with no accepted glyph, in order of first use.
 * The layout leaves a gap for each of them, so list them before export.
 */
export function missingChars(text: string, glyphs: Glyph[]): string[] {
  const have = new Set(glyphs.filter((g) => g.accepted).map((g) => g.char))
  const missing: string[] = []
  for (const ch of Array.from(text)) {
    if (ch === ' ' || ch === '\n') continue
    if (have.has(ch) || have.has(ch.toLowerCase())) continue
    if (!missing.includes(ch)) missing.push(ch)
  }
  return missing
}

/**
 * Median height of the profile's short lowercase letters, in glyph pixels.
 * Falls back to the stored profile metric, then to 32.
 */
export function estimateXHeight(glyphs: Glyph[], fallback: number): number {
  const heights = glyphs
    .filter((g) => g.accepted && X_HEIGHT_CHARS.has(g.char))
    .map((g) => g.height)
    .sort((a, b) => a - b)
  if (heights.length === 0) return fallback > 0 ? fallback : 32
  const mid = Math.floor(heights.length / 2)
  return heights.length % 2 ? heights[mid] : (heights[mid - 1] + heights[mid]) / 2
}

/** One character of the word being built: its glyph (null if none) and advance width. */
interface WordChar {
  placed: PlacedGlyph | null
  advance: number
}

/**
 * Lay out `text` using `glyphs` and `settings`.
 * Returns placed glyphs per page.
 *
 * backend/workers/renderer.py ports this function line for line so the PDF
 * matches the preview. Change both together, including the order of rng calls
 * and of the arithmetic; backend/tests/test_layout_parity.py checks them.
 */
export function layoutText(
  text: string,
  glyphs: Glyph[],
  settings: RenderSettings,
  profileXHeight: number,
  seed: number
): LayoutResult {
  const rng = mulberry32(seed)

  // Build glyph lookup: char → variants[]
  const glyphMap = new Map<string, Glyph[]>()
  for (const g of glyphs) {
    if (!g.accepted) continue
    const arr = glyphMap.get(g.char) ?? []
    arr.push(g)
    glyphMap.set(g.char, arr)
  }

  // Font size in px
  const fontSizePx = settings.font_size * PT_TO_PX
  // Scale factor: how much to scale glyphs so their x-height matches fontSizePx
  const xHeightRef = estimateXHeight(glyphs, profileXHeight)
  const baseScale = fontSizePx / xHeightRef

  const [pageWidthPx, pageHeightPx] = pageSizePx(settings)

  // Margins in px
  const mTop = settings.margin_top * PX_PER_MM
  const mBottom = settings.margin_bottom * PX_PER_MM
  const mLeft = settings.margin_left * PX_PER_MM
  const mRight = settings.margin_right * PX_PER_MM

  const contentWidth = pageWidthPx - mLeft - mRight
  const contentHeight = pageHeightPx - mTop - mBottom
  const maxX = mLeft + contentWidth

  // Line height in px
  const lineHeight = fontSizePx * settings.line_spacing

  // Messiness factors (0..100 → 0..1)
  const mess = settings.messiness / 100

  // Slant correction in radians
  const slantRad = (settings.slant_correction * Math.PI) / 180

  const center = settings.align === 'center'

  const pages: PlacedGlyph[][] = [[]]

  let curX = mLeft
  let curY = mTop + fontSizePx // baseline starts one line down from top margin

  // Glyphs on the current line, and the right edge of its last word
  let line: PlacedGlyph[] = []
  let lineEnd = mLeft

  let word: WordChar[] = []
  let wordWidth = 0

  const endLine = () => {
    if (center) {
      const shift = (contentWidth - (lineEnd - mLeft)) / 2
      for (const pg of line) pg.x += shift
    }
    line = []
    lineEnd = mLeft
  }

  const newLine = () => {
    endLine()
    curX = mLeft
    curY += lineHeight
    if (curY > mTop + contentHeight) {
      pages.push([])
      curY = mTop + fontSizePx
    }
  }

  const place = (wc: WordChar) => {
    if (wc.placed) {
      wc.placed.x += curX
      wc.placed.y += curY
      pages[pages.length - 1].push(wc.placed)
      line.push(wc.placed)
    }
    curX += wc.advance
    lineEnd = curX
  }

  const flushWord = () => {
    if (word.length === 0) return
    // Wrap if the word doesn't fit on the current line
    if (curX + wordWidth > maxX && curX > mLeft) newLine()
    // A word wider than a whole line breaks between characters
    const tooWide = curX + wordWidth > maxX
    for (const wc of word) {
      if (tooWide && curX + wc.advance > maxX && curX > mLeft) newLine()
      place(wc)
    }
    word = []
    wordWidth = 0
  }

  for (const ch of Array.from(text)) {
    if (ch === '\n') {
      flushWord()
      newLine()
      continue
    }

    if (ch === ' ') {
      flushWord()
      // Space width: roughly half x-height scaled
      const spaceW = baseScale * xHeightRef * 0.45 * (1 + randRange(rng, -0.1, 0.1) * mess)
      curX += spaceW
      continue
    }

    const variants = glyphMap.get(ch) ?? glyphMap.get(ch.toLowerCase()) ?? []
    if (variants.length === 0) {
      // No glyph: leave a gap (missingChars lists these before export)
      const advance = baseScale * xHeightRef * 0.5
      word.push({ placed: null, advance })
      wordWidth += advance
      continue
    }

    // Pick variant with PRNG
    const variant = variants[randInt(rng, 0, variants.length - 1)]

    // Per-glyph jitter
    const scaleJitter = 1 + randRange(rng, -0.03, 0.03) * mess
    const scale = baseScale * scaleJitter
    const rotation = slantRad + randRange(rng, -0.035, 0.035) * mess
    const baselineWobble = randRange(rng, -1, 1) * mess

    const advance = (variant.bearing_left + variant.width + variant.bearing_right) * scale

    word.push({
      placed: {
        glyph: variant,
        x: variant.bearing_left * scale, // offset by curX when placed
        y: baselineWobble,               // offset by curY when placed
        scale,
        rotation,
      },
      advance,
    })
    wordWidth += advance
  }

  // Flush remaining word
  flushWord()
  endLine()

  // Trailing newlines can leave empty pages at the end; drop them
  while (pages.length > 1 && pages[pages.length - 1].length === 0) pages.pop()

  return { pages, pageWidthPx, pageHeightPx }
}

/** Glyph masks recoloured to the ink colour, keyed by image URL and colour. */
const tintCache = new Map<string, HTMLCanvasElement>()

function tinted(img: HTMLImageElement, url: string, color: string): HTMLCanvasElement {
  const key = `${url}|${color}`
  const hit = tintCache.get(key)
  if (hit) return hit
  // Tint on a separate canvas: source-in on the page canvas would fill the
  // whole glyph box, because the paper underneath is opaque
  const c = document.createElement('canvas')
  c.width = img.naturalWidth
  c.height = img.naturalHeight
  const t = c.getContext('2d')!
  t.drawImage(img, 0, 0)
  t.globalCompositeOperation = 'source-in'
  t.fillStyle = color
  t.fillRect(0, 0, c.width, c.height)
  tintCache.set(key, c)
  return c
}

/** Preview paper colour. The PDF is ink only, so the card stock shows through. */
const PAPER_COLOR = '#faf8f4'

/** Latest draw per canvas, so a slow earlier draw can't paint over a newer one. */
const drawTokens = new WeakMap<HTMLCanvasElement, number>()

/**
 * Render a LayoutResult onto a Canvas element.
 * Uses drawImage with the glyph mask images. `pixelRatio` sets the backing
 * resolution; the caller sizes the canvas in CSS pixels.
 */
export async function renderToCanvas(
  canvas: HTMLCanvasElement,
  layout: LayoutResult,
  pageIndex: number,
  settings: RenderSettings,
  imageCache: Map<string, HTMLImageElement>,
  pixelRatio = 1
): Promise<void> {
  const token = (drawTokens.get(canvas) ?? 0) + 1
  drawTokens.set(canvas, token)

  const { pageWidthPx, pageHeightPx, pages } = layout
  const page = pages[pageIndex] ?? []

  const toUrl = (rawPath: string): string => {
    const clean = rawPath.replace(/\\/g, '/')
    return clean.startsWith('/files/') ? clean : `/files/${clean.replace(/^\//, '')}`
  }

  const loadImage = (rawPath: string): Promise<HTMLImageElement | null> => {
    const url = toUrl(rawPath)
    if (imageCache.has(url)) return Promise.resolve(imageCache.get(url)!)
    return new Promise((resolve) => {
      const img = new Image()
      img.crossOrigin = 'anonymous'
      img.onload = () => { imageCache.set(url, img); resolve(img) }
      img.onerror = () => {
        console.warn('Could not load glyph image:', url)
        resolve(null)
      }
      img.src = url
    })
  }

  // Load all images in parallel before touching the canvas, then draw in one go
  const uniquePaths = [...new Set(page.map((pg) => pg.glyph.mask_path))]
  await Promise.allSettled(uniquePaths.map(loadImage))
  if (drawTokens.get(canvas) !== token) return

  canvas.width = Math.round(pageWidthPx * pixelRatio)
  canvas.height = Math.round(pageHeightPx * pixelRatio)

  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)

  // ── Background ──────────────────────────────────────────────────────────────
  ctx.fillStyle = PAPER_COLOR
  ctx.fillRect(0, 0, pageWidthPx, pageHeightPx)

  // ── Glyphs ───────────────────────────────────────────────────────────────────
  const inkColor = settings.ink_color || '#1a1a2e'

  for (const placed of page) {
    const url = toUrl(placed.glyph.mask_path)
    const img = imageCache.get(url)
    if (!img) continue

    const { x, y, scale, rotation, glyph } = placed
    const drawW = Math.max(1, glyph.width * scale)
    const drawH = Math.max(1, glyph.height * scale)
    const offsetY = glyph.baseline_offset * scale

    ctx.save()
    // Align so the bottom of a normal glyph rests on baseline y, while descenders hang down
    const centerY = y - drawH / 2 + offsetY
    ctx.translate(x + drawW / 2, centerY)
    ctx.rotate(rotation)

    // Draw transparent PNG glyph in the ink colour
    ctx.drawImage(tinted(img, url, inkColor), -drawW / 2, -drawH / 2, drawW, drawH)

    ctx.restore()
  }
}
