/**
 * The three capture sheets. Mirrors SHEET_CONFIGS in backend/workers/template.py;
 * change both together.
 */
export interface Sheet {
  number: number
  name: string
  charset: string
}

export const SHEETS: Sheet[] = [
  { number: 1, name: 'Lowercase', charset: 'abcdefghijklmnopqrstuvwxyz' },
  { number: 2, name: 'Uppercase', charset: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' },
  { number: 3, name: 'Digits & Punctuation', charset: '0123456789.,!?;:\'"-()' },
]

/** Boxes per character on the printed sheet. */
export const VARIANTS_PER_CHAR = 3

/** A character counts as ready once it has this many accepted variants. */
export const READY_VARIANTS = 2

export function templatePdfUrl(sheetNumber: number): string {
  return `/api/templates/${sheetNumber}/pdf`
}
