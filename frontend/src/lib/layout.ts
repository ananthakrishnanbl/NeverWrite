import { Glyph, RenderSettings } from './api'
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

/** A4 at 96 dpi for canvas preview (210 mm × 297 mm). */
const A4_W_MM = 210
const A4_H_MM = 297
const PX_PER_MM = 96 / 25.4 // ≈ 3.7795 px/mm

const PAGE_W_PX = Math.round(A4_W_MM * PX_PER_MM)
const PAGE_H_PX = Math.round(A4_H_MM * PX_PER_MM)

// Points to pixels: 1 pt = 1/72 inch = 96/72 px at 96 dpi
const PT_TO_PX = 96 / 72

// Letters with no ascender or descender; their height is the x-height
const X_HEIGHT_CHARS = new Set('acemnorsuvwxz')

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

/**
 * Lay out `text` using `glyphs` and `settings`.
 * Returns placed glyphs per page.
 *
 * backend/workers/renderer.py ports this function line for line so the PDF
 * matches the preview. Change both together, including the order of rng calls.
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

  // Margins in px
  const mTop = settings.margin_top * PX_PER_MM
  const mBottom = settings.margin_bottom * PX_PER_MM
  const mLeft = settings.margin_left * PX_PER_MM
  const mRight = settings.margin_right * PX_PER_MM

  const contentWidth = PAGE_W_PX - mLeft - mRight
  const contentHeight = PAGE_H_PX - mTop - mBottom

  // Line height in px
  const lineHeight = fontSizePx * settings.line_spacing

  // Messiness factors (0..100 → 0..1)
  const mess = settings.messiness / 100

  // Slant correction in radians
  const slantRad = (settings.slant_correction * Math.PI) / 180

  const pages: PlacedGlyph[][] = []
  let currentPage: PlacedGlyph[] = []
  pages.push(currentPage)

  let curX = mLeft
  let curY = mTop + fontSizePx // baseline starts one line down from top margin

  const chars = Array.from(text)
  let wordBuffer: PlacedGlyph[] = []
  let wordWidth = 0

  const flushWord = () => {
    if (wordBuffer.length === 0) return
    // Check if word fits on current line
    if (curX + wordWidth > mLeft + contentWidth && curX > mLeft) {
      // Wrap line
      curX = mLeft
      curY += lineHeight
      if (curY > mTop + contentHeight) {
        currentPage = []
        pages.push(currentPage)
        curY = mTop + fontSizePx
      }
    }
    for (const pg of wordBuffer) {
      pg.x += curX
      pg.y += curY
      currentPage.push(pg)
    }
    curX += wordWidth
    wordBuffer = []
    wordWidth = 0
  }

  for (let ci = 0; ci < chars.length; ci++) {
    const ch = chars[ci]

    if (ch === '\n') {
      flushWord()
      curX = mLeft
      curY += lineHeight
      if (curY > mTop + contentHeight) {
        currentPage = []
        pages.push(currentPage)
        curY = mTop + fontSizePx
      }
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
      // Unknown char: advance by a small space
      wordWidth += baseScale * xHeightRef * 0.5
      continue
    }

    // Pick variant with PRNG
    const variant = variants[randInt(rng, 0, variants.length - 1)]

    // Per-glyph jitter
    const scaleJitter = 1 + randRange(rng, -0.03, 0.03) * mess
    const scale = baseScale * scaleJitter
    const rotation = slantRad + randRange(rng, -0.035, 0.035) * mess
    const baselineWobble = randRange(rng, -1, 1) * mess

    const glyphW = (variant.bearing_left + variant.width + variant.bearing_right) * scale
    const charGap = variant.bearing_left * scale

    const placed: PlacedGlyph = {
      glyph: variant,
      x: wordWidth + charGap, // relative; will be offset by curX when flushed
      y: baselineWobble,       // relative; will be offset by curY when flushed
      scale,
      rotation,
    }

    wordBuffer.push(placed)
    wordWidth += glyphW
  }

  // Flush remaining word
  flushWord()

  return {
    pages,
    pageWidthPx: PAGE_W_PX,
    pageHeightPx: PAGE_H_PX,
  }
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

/** Paper background colors */
const PAPER_COLORS: Record<RenderSettings['paper_type'], string> = {
  plain: '#faf8f4',
  ruled: '#faf8f4',
  grid: '#faf8f4',
  none: '#ffffff',
}

/**
 * Render a LayoutResult onto a Canvas element.
 * Uses drawImage with the glyph mask images.
 */
export async function renderToCanvas(
  canvas: HTMLCanvasElement,
  layout: LayoutResult,
  pageIndex: number,
  settings: RenderSettings,
  imageCache: Map<string, HTMLImageElement>
): Promise<void> {
  const { pageWidthPx, pageHeightPx, pages } = layout
  canvas.width = pageWidthPx
  canvas.height = pageHeightPx

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  // ── Background ──────────────────────────────────────────────────────────────
  ctx.fillStyle = PAPER_COLORS[settings.paper_type]
  ctx.fillRect(0, 0, pageWidthPx, pageHeightPx)

  const PX_PER_MM = 96 / 25.4
  const mTop = settings.margin_top * PX_PER_MM
  const mBottom = settings.margin_bottom * PX_PER_MM
  const mLeft = settings.margin_left * PX_PER_MM
  const contentW = pageWidthPx - mLeft - settings.margin_right * PX_PER_MM
  const contentH = pageHeightPx - mTop - mBottom
  const fontSizePx = settings.font_size * (96 / 72)
  const lineHeight = fontSizePx * settings.line_spacing

  // ── Ruled lines ─────────────────────────────────────────────────────────────
  if (settings.paper_type === 'ruled') {
    ctx.strokeStyle = '#b8c8d8'
    ctx.lineWidth = 0.5
    let ly = mTop + fontSizePx
    while (ly < mTop + contentH) {
      ctx.beginPath()
      ctx.moveTo(mLeft, ly)
      ctx.lineTo(mLeft + contentW, ly)
      ctx.stroke()
      ly += lineHeight
    }
    // Left margin line
    ctx.strokeStyle = '#f0a0a0'
    ctx.lineWidth = 0.7
    ctx.beginPath()
    ctx.moveTo(mLeft, mTop)
    ctx.lineTo(mLeft, mTop + contentH)
    ctx.stroke()
  }

  // ── Grid lines ───────────────────────────────────────────────────────────────
  if (settings.paper_type === 'grid') {
    const gridSize = fontSizePx * 0.5
    ctx.strokeStyle = '#c8d8e8'
    ctx.lineWidth = 0.4
    for (let gx = mLeft; gx <= mLeft + contentW; gx += gridSize) {
      ctx.beginPath()
      ctx.moveTo(gx, mTop)
      ctx.lineTo(gx, mTop + contentH)
      ctx.stroke()
    }
    for (let gy = mTop; gy <= mTop + contentH; gy += gridSize) {
      ctx.beginPath()
      ctx.moveTo(mLeft, gy)
      ctx.lineTo(mLeft + contentW, gy)
      ctx.stroke()
    }
  }

  // ── Glyphs ───────────────────────────────────────────────────────────────────
  const page = pages[pageIndex]
  if (!page) return

  const inkColor = settings.ink_color || '#1a1a2e'

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

  // Load all images in parallel
  const uniquePaths = [...new Set(page.map((pg) => pg.glyph.mask_path))]
  await Promise.allSettled(uniquePaths.map(loadImage))

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
