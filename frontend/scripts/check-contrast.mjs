#!/usr/bin/env node
// WCAG 2.2 contrast check for the design tokens in src/styles/tokens.css.
//
// Parses the `:root` (light) and `.dark` blocks, converts OKLCH to sRGB,
// alpha-blends translucent tokens over the surface they sit on, and checks the
// key pairs against AA: 4.5:1 for text, 3:1 for UI components and graphics.
//
//   npm run check:contrast            # table + exit code 1 on any failure
//   npm run check:contrast -- --md    # markdown table (for PR descriptions)

import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(join(here, "../src/styles/tokens.css"), "utf8")

function block(selector) {
  // `:root,\n.light {` or `.dark {`: the selector at the start of a line.
  const start = css.search(new RegExp(`^${selector.replace(".", "\\.")}\\s*[{,]`, "m"))
  if (start < 0) throw new Error(`No ${selector} block in tokens.css`)
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start))
  const vars = {}
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) vars[m[1]] = m[2].trim()
  return vars
}

// OKLCH -> linear sRGB -> gamma-encoded sRGB in [0, 1].
function parseOklch(value) {
  const m = value.match(/^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.]+)(%?))?\s*\)$/)
  if (!m) throw new Error(`Unsupported colour: ${value}`)
  const [L, C, H] = [Number(m[1]), Number(m[2]), (Number(m[3]) * Math.PI) / 180]
  const alpha = m[4] === undefined ? 1 : Number(m[4]) / (m[5] ? 100 : 1)
  const a = C * Math.cos(H)
  const b = C * Math.sin(H)
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const mm = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
  const lin = [
    4.0767416621 * l - 3.3077115913 * mm + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * mm - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * mm + 1.707614701 * s,
  ]
  const encode = (c) => {
    const x = Math.min(1, Math.max(0, c))
    return x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055
  }
  return { rgb: lin.map(encode), alpha }
}

// Browsers composite in gamma-encoded sRGB.
function over(fg, bg) {
  return fg.rgb.map((c, i) => c * fg.alpha + bg[i] * (1 - fg.alpha))
}

function luminance(rgb) {
  const lin = rgb.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
}

function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const hex = (rgb) => "#" + rgb.map((c) => Math.round(c * 255).toString(16).padStart(2, "0")).join("")

// [label, foreground token (optionally "token@alpha"), surface token, minimum]
const TEXT = 4.5
const UI = 3
const pairs = [
  ["foreground / background", "foreground", "background", TEXT],
  ["card-foreground / card", "card-foreground", "card", TEXT],
  ["popover-foreground / popover", "popover-foreground", "popover", TEXT],
  ["muted-foreground / background", "muted-foreground", "background", TEXT],
  ["muted-foreground / card", "muted-foreground", "card", TEXT],
  ["muted-foreground / muted", "muted-foreground", "muted", TEXT],
  ["primary-foreground / primary", "primary-foreground", "primary", TEXT],
  ["primary (link text) / background", "primary", "background", TEXT],
  ["secondary-foreground / secondary", "secondary-foreground", "secondary", TEXT],
  ["accent-foreground / accent", "accent-foreground", "accent", TEXT],
  ["destructive (text) / background", "destructive", "background", TEXT],
  // Destructive buttons/badges are text on a 10% (light) / 20% (dark) tint.
  ["destructive / destructive 10% tint", "destructive", "destructive@0.1>background", TEXT],
  ["destructive / destructive 20% tint", "destructive", "destructive@0.2>background", TEXT],
  ["sidebar-foreground / sidebar", "sidebar-foreground", "sidebar", TEXT],
  ["sidebar-accent-fg / sidebar-accent", "sidebar-accent-foreground", "sidebar-accent", TEXT],
  ["sidebar-primary-fg / sidebar-primary", "sidebar-primary-foreground", "sidebar-primary", TEXT],
  ["sidebar-primary / sidebar-accent (active nav)", "sidebar-primary", "sidebar-accent", TEXT],
  ["ring / background", "ring", "background", UI],
  ["ring / card", "ring", "card", UI],
  ["sidebar-ring / sidebar", "sidebar-ring", "sidebar", UI],
  ["primary (button fill) / background", "primary", "background", UI],
]
for (const c of ["strong", "moderate", "weak", "rejected"]) {
  pairs.push(
    [`category-${c}-foreground / -muted (badge)`, `category-${c}-foreground`, `category-${c}-muted`, TEXT],
    [`category-${c}-foreground / background`, `category-${c}-foreground`, "background", TEXT],
    [`category-${c} (bar) / background`, `category-${c}`, "background", UI],
    [`category-${c} (bar) / muted track`, `category-${c}`, "muted", UI]
  )
}
// Reported for information, not gated: shadcn's hairline borders are
// decorative; inputs are identified by their labels, not the border alone.
const info = [
  ["border / background (info)", "border", "background"],
  ["input / background (info)", "input", "background"],
]

const themes = { light: block(":root"), dark: { ...block(":root"), ...block(".dark") } }

function resolve(vars, spec) {
  // "token" or "token@alpha>surfaceToken"
  const [token, rest] = spec.split("@")
  if (!rest) {
    const c = parseOklch(vars[token])
    return c.alpha === 1 ? c.rgb : over(c, resolve(vars, "background"))
  }
  const [alpha, surface] = rest.split(">")
  const c = parseOklch(vars[token])
  return over({ rgb: c.rgb, alpha: Number(alpha) * c.alpha }, resolve(vars, surface))
}

const rows = []
let failures = 0
for (const [label, fg, bg, min] of [...pairs, ...info.map((p) => [...p, null])]) {
  const cells = { label, min }
  for (const [name, vars] of Object.entries(themes)) {
    const surface = resolve(vars, bg)
    const fgParsed = parseOklch(vars[fg.split("@")[0]])
    const fore = fgParsed.alpha === 1 ? fgParsed.rgb : over(fgParsed, surface)
    const r = ratio(fore, surface)
    const pass = min === null ? null : r >= min
    if (pass === false) failures++
    cells[name] = { r, pass, fg: hex(fore), bg: hex(surface) }
  }
  rows.push(cells)
}

const mark = (c) => (c.pass === null ? "info" : c.pass ? "pass" : "FAIL")
if (process.argv.includes("--md")) {
  console.log("| Pair | Min | Light | Dark |")
  console.log("|---|---|---|---|")
  for (const row of rows) {
    const cell = (c) => `${c.r.toFixed(2)}:1 ${mark(c)}`
    console.log(`| ${row.label} | ${row.min ? `${row.min}:1` : "–"} | ${cell(row.light)} | ${cell(row.dark)} |`)
  }
} else {
  const pad = (s, n) => String(s).padEnd(n)
  console.log(pad("Pair", 46) + pad("Min", 6) + pad("Light", 30) + "Dark")
  for (const row of rows) {
    const cell = (c) => `${c.r.toFixed(2)} ${mark(c)} ${c.fg}/${c.bg}`
    console.log(pad(row.label, 46) + pad(row.min ?? "-", 6) + pad(cell(row.light), 30) + cell(row.dark))
  }
}

if (failures) {
  console.error(`\n${failures} pair(s) below WCAG AA.`)
  process.exit(1)
}
