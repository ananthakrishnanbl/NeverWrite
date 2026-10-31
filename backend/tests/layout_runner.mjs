// Runs the frontend's layoutText on a JSON fixture for test_layout_parity.py.
// Usage: node --experimental-strip-types layout_runner.mjs <copy of layout.ts> <fixture.json>
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const [layoutPath, fixturePath] = process.argv.slice(2)
const { layoutText, missingChars } = await import(pathToFileURL(layoutPath).href)
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'))

const results = fixture.cases.map((c) => {
  const layout = layoutText(c.text, fixture.glyphs, c.settings, fixture.profile_x_height, c.seed)
  return {
    size: [layout.pageWidthPx, layout.pageHeightPx],
    pages: layout.pages.map((page) => page.map((p) => [p.glyph.id, p.x, p.y, p.scale, p.rotation])),
    missing: missingChars(c.text, fixture.glyphs),
  }
})

process.stdout.write(JSON.stringify(results))
