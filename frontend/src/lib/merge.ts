/** CSV parsing and {{placeholder}} filling for mail-merged cards. */

export interface ParsedCsv {
  columns: string[]
  rows: string[][]
}

/**
 * Parse CSV text (RFC 4180: quoted fields, "" escapes, commas and newlines
 * inside quotes, CRLF or LF). The first row is the header. Blank rows are
 * skipped and short rows are padded to the header width.
 */
export function parseCsv(text: string): ParsedCsv {
  const records: string[][] = []
  let record: string[] = []
  let field = ''
  let quoted = false
  const src = text.replace(/^﻿/, '') // Excel adds a byte-order mark

  for (let i = 0; i < src.length; i++) {
    const ch = src[i]
    if (quoted) {
      if (ch === '"') {
        if (src[i + 1] === '"') { field += '"'; i++ }
        else quoted = false
      } else {
        field += ch
      }
    } else if (ch === '"') {
      quoted = true
    } else if (ch === ',') {
      record.push(field)
      field = ''
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && src[i + 1] === '\n') i++
      record.push(field)
      records.push(record)
      record = []
      field = ''
    } else {
      field += ch
    }
  }
  if (field !== '' || record.length > 0) {
    record.push(field)
    records.push(record)
  }

  const nonBlank = records.filter((r) => r.some((cell) => cell.trim() !== ''))
  if (nonBlank.length === 0) return { columns: [], rows: [] }
  const columns = nonBlank[0].map((c) => c.trim())
  const rows = nonBlank.slice(1).map((r) => columns.map((_, i) => (r[i] ?? '').replace(/\r\n?/g, '\n')))
  return { columns, rows }
}

const PLACEHOLDER = /\{\{\s*([^{}]*?)\s*\}\}/g

/** Placeholder names in `template`, in order of first use. */
export function placeholders(template: string): string[] {
  const names: string[] = []
  for (const m of template.matchAll(PLACEHOLDER)) {
    if (m[1] && !names.includes(m[1])) names.push(m[1])
  }
  return names
}

/**
 * Column index for each placeholder: the user's choice if they made one,
 * otherwise the column with the same name (ignoring case), otherwise -1.
 */
export function resolveMapping(
  names: string[],
  columns: string[],
  chosen: Record<string, number>
): Record<string, number> {
  const resolved: Record<string, number> = {}
  for (const name of names) {
    if (name in chosen && chosen[name] < columns.length) {
      resolved[name] = chosen[name]
    } else {
      resolved[name] = columns.findIndex((c) => c.toLowerCase() === name.toLowerCase())
    }
  }
  return resolved
}

/** Replace each mapped {{placeholder}} with the row's value; unmapped ones stay as typed. */
export function fillTemplate(template: string, row: string[], mapping: Record<string, number>): string {
  return template.replace(PLACEHOLDER, (whole, name: string) => {
    const col = mapping[name]
    return col !== undefined && col >= 0 ? (row[col] ?? '').trim() : whole
  })
}
