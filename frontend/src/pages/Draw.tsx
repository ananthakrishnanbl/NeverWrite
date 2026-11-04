import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Check, ChevronRight, Eraser, PenLine, Undo2 } from 'lucide-react'
import { apiError, getGlyphs, saveDrawnGlyph, DrawnStroke, Glyph } from '../lib/api'
import { BASELINE_MM, BOX_H_MM, BOX_W_MM, MIN_STEP_MM, STROKE_WIDTH_MM, X_HEIGHT_MM, maskUrl } from '../lib/draw'
import { SHEETS, VARIANTS_PER_CHAR } from '../lib/sheets'

interface Slot {
  char: string
  variant: number
}

const INK = '#1a1a2e'
const GUIDE = '#7cc8ea'

/** Every slot in sheet order: a0 a1 a2 b0 ... */
const ALL_SLOTS: Slot[] = SHEETS.flatMap((s) =>
  Array.from(s.charset).flatMap((char) => Array.from({ length: VARIANTS_PER_CHAR }, (_, variant) => ({ char, variant })))
)

const slotKey = (char: string, variant: number) => `${char}|${variant}`

/** The first slot after `from` (wrapping round) with no drawn glyph, or null when every slot is drawn. */
function nextUndrawn(drawn: Set<string>, from: Slot | null): Slot | null {
  const start = from ? ALL_SLOTS.findIndex((s) => s.char === from.char && s.variant === from.variant) + 1 : 0
  for (let i = 0; i < ALL_SLOTS.length; i++) {
    const slot = ALL_SLOTS[(start + i) % ALL_SLOTS.length]
    if (!drawn.has(slotKey(slot.char, slot.variant))) return slot
  }
  return null
}

export default function Draw() {
  const { profileId } = useParams<{ profileId: string }>()
  const navigate = useNavigate()
  const pid = Number(profileId)

  const [glyphs, setGlyphs] = useState<Glyph[]>([])
  const [loading, setLoading] = useState(true)
  const [slot, setSlot] = useState<Slot>(ALL_SLOTS[0])
  const [strokes, setStrokes] = useState<DrawnStroke[]>([])
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const current = useRef<DrawnStroke | null>(null)
  const activePointer = useRef<number | null>(null)
  const penSeen = useRef(false)
  const frame = useRef<number | null>(null)

  const bySlot = useMemo(() => new Map(glyphs.map((g) => [slotKey(g.char, g.variant_index), g])), [glyphs])
  const drawn = useMemo(
    () => new Set(glyphs.filter((g) => g.has_strokes).map((g) => slotKey(g.char, g.variant_index))),
    [glyphs]
  )
  const sheet = SHEETS.find((s) => s.charset.includes(slot.char)) ?? SHEETS[0]

  useEffect(() => {
    getGlyphs(pid)
      .then((gs) => {
        setGlyphs(gs)
        const done = new Set(gs.filter((g) => g.has_strokes).map((g) => slotKey(g.char, g.variant_index)))
        setSlot(nextUndrawn(done, null) ?? ALL_SLOTS[0])
      })
      .catch(() => setMessage({ ok: false, text: 'Failed to load this profile’s letters.' }))
      .finally(() => setLoading(false))
  }, [pid])

  // ── Canvas ────────────────────────────────────────────────────────────────

  const paint = useCallback(() => {
    frame.current = null
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const dpr = window.devicePixelRatio || 1
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
    }
    // Draw in box millimetres
    const k = (w / BOX_W_MM) * dpr
    ctx.setTransform(k, 0, 0, k, 0, 0)
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, BOX_W_MM, BOX_H_MM)

    ctx.strokeStyle = GUIDE
    ctx.lineWidth = 0.1
    ctx.setLineDash([])
    ctx.beginPath()
    ctx.moveTo(0, BASELINE_MM)
    ctx.lineTo(BOX_W_MM, BASELINE_MM)
    ctx.stroke()
    ctx.setLineDash([0.5, 0.4])
    ctx.beginPath()
    ctx.moveTo(0, BASELINE_MM - X_HEIGHT_MM)
    ctx.lineTo(BOX_W_MM, BASELINE_MM - X_HEIGHT_MM)
    ctx.stroke()
    ctx.setLineDash([])

    ctx.fillStyle = '#d6d0c4'
    ctx.font = '2.4px Inter, system-ui, sans-serif'
    ctx.fillText(slot.char, 0.8, 2.8)

    ctx.strokeStyle = INK
    ctx.fillStyle = INK
    ctx.lineWidth = STROKE_WIDTH_MM
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    const all = current.current ? [...strokes, current.current] : strokes
    for (const stroke of all) {
      if (stroke.length === 1) {
        ctx.beginPath()
        ctx.arc(stroke[0][0], stroke[0][1], STROKE_WIDTH_MM / 2, 0, Math.PI * 2)
        ctx.fill()
        continue
      }
      ctx.beginPath()
      ctx.moveTo(stroke[0][0], stroke[0][1])
      for (const [x, y] of stroke.slice(1)) ctx.lineTo(x, y)
      ctx.stroke()
    }
  }, [strokes, slot.char])

  const schedulePaint = useCallback(() => {
    if (frame.current === null) frame.current = requestAnimationFrame(paint)
  }, [paint])

  useEffect(() => {
    paint()
  }, [paint, loading])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const observer = new ResizeObserver(() => paint())
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [paint, loading])

  const toMm = (e: { clientX: number; clientY: number }): [number, number] => {
    const rect = canvasRef.current!.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * BOX_W_MM
    const y = ((e.clientY - rect.top) / rect.height) * BOX_H_MM
    return [Math.round(x * 100) / 100, Math.round(y * 100) / 100]
  }

  const addPoint = (p: [number, number]) => {
    const stroke = current.current
    if (!stroke) return
    const [lx, ly] = stroke[stroke.length - 1]
    if (Math.hypot(p[0] - lx, p[1] - ly) >= MIN_STEP_MM) stroke.push(p)
  }

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    if (e.pointerType === 'pen') penSeen.current = true
    // Once a stylus has been used, ignore touches: that is the hand resting on the screen
    else if (e.pointerType === 'touch' && penSeen.current) return
    if (activePointer.current !== null) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    activePointer.current = e.pointerId
    current.current = [toMm(e)]
    setMessage(null)
    schedulePaint()
  }

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.pointerId !== activePointer.current) return
    const events = e.nativeEvent.getCoalescedEvents?.() ?? []
    for (const ev of events.length ? events : [e.nativeEvent]) addPoint(toMm(ev))
    schedulePaint()
  }

  const endStroke = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.pointerId !== activePointer.current) return
    activePointer.current = null
    const stroke = current.current
    current.current = null
    if (stroke) setStrokes((prev) => [...prev, stroke])
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  const chooseSlot = (next: Slot) => {
    setSlot(next)
    setStrokes([])
    setMessage(null)
  }

  const undo = useCallback(() => setStrokes((prev) => prev.slice(0, -1)), [])

  const save = useCallback(async () => {
    if (strokes.length === 0 || saving) return
    setSaving(true)
    try {
      const glyph = await saveDrawnGlyph(pid, { char: slot.char, variant_index: slot.variant, strokes })
      const updated = [...glyphs.filter((g) => g.id !== glyph.id), glyph]
      setGlyphs(updated)
      setStrokes([])
      const done = new Set(updated.filter((g) => g.has_strokes).map((g) => slotKey(g.char, g.variant_index)))
      const next = nextUndrawn(done, slot)
      setMessage({
        ok: true,
        text: next
          ? `Saved ${slot.char}, ${slot.variant + 1} of ${VARIANTS_PER_CHAR}.`
          : 'Saved. Every character now has all its variants drawn.',
      })
      if (next) setSlot(next)
    } catch (err) {
      setMessage({ ok: false, text: apiError(err, 'Could not save that drawing. Please try again.') })
    } finally {
      setSaving(false)
    }
  }, [strokes, saving, pid, slot, glyphs])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'Enter') {
        e.preventDefault()
        save()
      } else if ((e.key === 'z' && (e.ctrlKey || e.metaKey)) || e.key === 'Backspace') {
        e.preventDefault()
        undo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [save, undo])

  // ── Progress ──────────────────────────────────────────────────────────────

  const drawnCount = (char: string) =>
    Array.from({ length: VARIANTS_PER_CHAR }, (_, v) => drawn.has(slotKey(char, v))).filter(Boolean).length

  const sheetProgress = SHEETS.map((s) => ({
    sheet: s,
    done: Array.from(s.charset).filter((c) => drawnCount(c) === VARIANTS_PER_CHAR).length,
  }))

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
        <div className="max-w-2xl">
          <h1 className="text-3xl font-bold text-ink-900">Draw your letters</h1>
          <p className="text-ink-600 mt-1">
            Write each character three times, sitting on the solid line. The dashed line is the height of a small x.
          </p>
          <p className="text-sm text-ink-400 mt-1">
            Drawn letters are what a pen plotter follows. A stylus on a tablet looks closest to pen on paper; a finger
            or mouse works too.
          </p>
        </div>
        <button
          onClick={() => navigate(`/profiles/${pid}/glyphs`)}
          className="flex items-center gap-1.5 text-sm text-ink-700 bg-ink-100 hover:bg-ink-200 px-3.5 py-2 rounded-lg font-medium transition-colors"
        >
          Review all letters
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      {/* Sheet tabs */}
      <div className="flex gap-1 mb-3 bg-ink-50 rounded-xl p-1">
        {sheetProgress.map(({ sheet: s, done }) => (
          <button
            key={s.number}
            onClick={() => {
              const first = Array.from(s.charset).find((c) => drawnCount(c) < VARIANTS_PER_CHAR) ?? s.charset[0]
              const variant = Array.from({ length: VARIANTS_PER_CHAR }, (_, v) => v).find(
                (v) => !drawn.has(slotKey(first, v))
              ) ?? 0
              chooseSlot({ char: first, variant })
            }}
            className={`flex-1 py-2 px-2 text-sm font-medium rounded-lg transition-all ${
              sheet.number === s.number ? 'bg-white text-ink-800 shadow-sm' : 'text-ink-500 hover:text-ink-700'
            }`}
          >
            {s.name}{' '}
            <span className={done === s.charset.length ? 'text-green-600' : 'text-ink-400'}>
              {done}/{s.charset.length}
            </span>
          </button>
        ))}
      </div>

      {/* Characters of this sheet */}
      <div className="flex flex-wrap gap-1.5 mb-6" role="list" aria-label="Characters">
        {Array.from(sheet.charset).map((c) => {
          const n = drawnCount(c)
          const active = c === slot.char
          return (
            <button
              key={c}
              role="listitem"
              onClick={() => {
                const v = Array.from({ length: VARIANTS_PER_CHAR }, (_, i) => i).find((i) => !drawn.has(slotKey(c, i)))
                chooseSlot({ char: c, variant: v ?? 0 })
              }}
              title={`${c}: ${n} of ${VARIANTS_PER_CHAR} drawn`}
              className={`w-9 h-9 rounded-lg font-mono text-base border-2 transition-colors ${
                active
                  ? 'border-ink-800 bg-ink-800 text-paper'
                  : n === VARIANTS_PER_CHAR
                    ? 'border-green-300 bg-green-50 text-green-800'
                    : n > 0
                      ? 'border-amber-300 bg-amber-50 text-amber-800'
                      : 'border-ink-100 bg-white text-ink-500 hover:border-ink-300'
              }`}
            >
              {c}
            </button>
          )
        })}
      </div>

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="w-8 h-8 border-2 border-ink-300 border-t-ink-700 rounded-full animate-spin" />
        </div>
      ) : (
        <div className="flex flex-col md:flex-row gap-6 items-start">
          {/* Writing box */}
          <div className="w-full md:w-auto">
            <div className="w-full max-w-[380px] md:w-[380px] mx-auto">
              <canvas
                ref={canvasRef}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={endStroke}
                onPointerCancel={endStroke}
                aria-label={`Writing box for ${slot.char}, variant ${slot.variant + 1}`}
                className="block w-full rounded-xl border-2 border-ink-200 bg-white cursor-crosshair touch-none select-none"
                style={{ aspectRatio: `${BOX_W_MM} / ${BOX_H_MM}` }}
              />
              <div className="flex gap-2 mt-3">
                <button
                  onClick={undo}
                  disabled={strokes.length === 0}
                  className="flex items-center gap-1.5 border border-ink-200 text-ink-700 px-3 py-2.5 rounded-lg text-sm font-medium hover:bg-ink-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  title="Undo the last stroke (Ctrl+Z)"
                >
                  <Undo2 className="w-4 h-4" />
                  Undo
                </button>
                <button
                  onClick={() => setStrokes([])}
                  disabled={strokes.length === 0}
                  className="flex items-center gap-1.5 border border-ink-200 text-ink-700 px-3 py-2.5 rounded-lg text-sm font-medium hover:bg-ink-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Eraser className="w-4 h-4" />
                  Clear
                </button>
                <button
                  onClick={save}
                  disabled={strokes.length === 0 || saving}
                  className="ml-auto flex items-center gap-1.5 bg-ink-800 text-paper px-4 py-2.5 rounded-lg text-sm font-semibold hover:bg-ink-700 disabled:opacity-40 disabled:cursor-not-allowed"
                  title="Save and move on (Enter)"
                >
                  <Check className="w-4 h-4" />
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </div>
              {message && (
                <p className={`mt-3 text-sm ${message.ok ? 'text-green-700' : 'text-red-600'}`} role="status">
                  {message.text}
                </p>
              )}
            </div>
          </div>

          {/* Variants of this character */}
          <div className="w-full md:flex-1 space-y-4">
            <div className="bg-white border border-ink-100 rounded-xl p-4">
              <div className="flex items-baseline justify-between mb-3">
                <h2 className="font-semibold text-ink-800">
                  Character <span className="font-mono text-xl">{slot.char}</span>
                </h2>
                <span className="text-sm text-ink-400">
                  {drawnCount(slot.char)} of {VARIANTS_PER_CHAR} drawn
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {Array.from({ length: VARIANTS_PER_CHAR }, (_, v) => {
                  const g = bySlot.get(slotKey(slot.char, v))
                  const active = v === slot.variant
                  return (
                    <button
                      key={v}
                      onClick={() => chooseSlot({ char: slot.char, variant: v })}
                      className={`rounded-lg border-2 p-2 text-left transition-colors ${
                        active ? 'border-ink-700 bg-ink-50' : 'border-ink-100 hover:border-ink-300'
                      }`}
                      aria-pressed={active}
                    >
                      <div className="h-16 flex items-center justify-center">
                        {g ? (
                          <img src={maskUrl(g.mask_path)} alt="" className="max-h-full max-w-full object-contain" />
                        ) : (
                          <span className="text-xs text-ink-300">Empty</span>
                        )}
                      </div>
                      <div className="mt-1 flex items-center justify-between text-xs">
                        <span className="text-ink-500">{v + 1}</span>
                        {g && (
                          <span className={g.has_strokes ? 'text-green-700' : 'text-amber-700'}>
                            {g.has_strokes ? 'drawn' : 'scanned'}
                          </span>
                        )}
                      </div>
                    </button>
                  )
                })}
              </div>
              <p className="mt-3 text-xs text-ink-400">
                Saving replaces the highlighted slot. Scanned letters can’t be plotted, since they’re pictures of
                ink; draw over them to make them plottable.
              </p>
            </div>

            <div className="bg-ink-50 rounded-xl p-4 text-sm text-ink-600 space-y-1.5">
              <p className="flex gap-2">
                <PenLine className="w-4 h-4 shrink-0 mt-0.5 text-ink-400" />
                Write at your normal pace and in your everyday style. Dots and crossbars are separate strokes, just as
                on paper.
              </p>
              <p className="pl-6">
                <kbd className="font-mono text-xs bg-white border border-ink-200 rounded px-1">Enter</kbd> saves,{' '}
                <kbd className="font-mono text-xs bg-white border border-ink-200 rounded px-1">Ctrl+Z</kbd> undoes a
                stroke.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
