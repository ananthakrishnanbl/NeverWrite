/** Mulberry32 seeded PRNG. Returns a factory given a seed. */
export function mulberry32(seed: number) {
  let s = seed >>> 0
  return function next(): number {
    s += 0x6D2B79F5
    let z = s
    z = Math.imul(z ^ (z >>> 15), z | 1)
    z ^= z + Math.imul(z ^ (z >>> 7), z | 61)
    return ((z ^ (z >>> 14)) >>> 0) / 4294967296
  }
}

/** Returns a random float in [min, max) */
export function randRange(rng: () => number, min: number, max: number): number {
  return min + rng() * (max - min)
}

/** Returns a random integer in [min, max] */
export function randInt(rng: () => number, min: number, max: number): number {
  return Math.floor(min + rng() * (max - min + 1))
}
