# HireFlow AI design guide

How the app looks and talks since the D1 redesign. The approved direction is three mock-up
screens; this guide is the short version of what they decided, and how the app's code
carries it out. When a screen and this guide disagree, fix the screen.

- **Mock-ups:** open `mockups/home.html`, `mockups/candidates.html` and
  `mockups/candidates-dark.html` by double-click (self-contained, Google Fonts only).
  `source/` keeps the original canvas files they were made from (`*.dc.html`: markup with
  `{{holes}}`, `<sc-for>` loops and the sample data in `renderVals()`); they need the
  canvas's `support.js` to render.
- **Tokens:** `frontend/src/styles/tokens.css`. **Components:** `frontend/src/components`,
  the score display in `components/application/score.tsx`.
- **Live gallery:** `/dev/ui` in a development build shows every token, the type scale and
  the score display in light and dark side by side.

## Palette and tokens

Neutral paper and ink greys and **one** accent, a deep forest green. Colour means
something: the accent is for the primary action, links, selection, focus and strong
matches; amber is for possible matches; everything else is grey.

| Role | Token (Tailwind class) | Light | Dark |
|---|---|---|---|
| Page surface (main column) | `--background` (`bg-background`) | `#FFFFFF` | `#17171A` |
| Paper behind it (sidebar, wells) | `--canvas` (`bg-canvas`) | `#F7F7F5` | `#111113` |
| Sunken (hover, chips, tracks) | `--muted`, `--secondary`, `--accent` | `#F1F1EE` | `#1E1E22` / `#26262B` |
| Ink: names, numbers, headings | `--foreground` (`text-foreground`) | `#18181B` | `#EDEDEF` |
| Ink-2: sentences | `--ink-2` (`text-ink-2`) | `#45454B` | `#BDBDC3` |
| Ink-3: meta, captions, dates | `--muted-foreground` | `#66666D` | `#9A9AA2` |
| Hairlines | `--border` (`border`) | `#E6E5E1` | `#2A2A2F` |
| Control outlines | `--input` | `#D9D8D3` | `#36363C` |
| Accent | `--primary`, `--ring` | `#1F5C4A` | `#86C9AE` |
| Accent tint (chips, avatars, bulk bar) | `--primary-soft` | `#E6EFEA` | `#1B2D26` |
| Selected table row | `--row-active` + 2px accent inset | `#EEF3F0` | `#1C2A24` |
| Strong match | `--category-strong` | `#1F5C4A` | `#86C9AE` |
| Possible match | `--category-moderate` | `#94570F` | `#E3A75E` |
| Unlikely / not a match | `--category-weak`, `--category-rejected` | `#7A7A80` (text `#66666D`) | `#9A9AA2` |
| Errors, missing must-haves | `--destructive` | `#A3361F` | `#F0937D` |
| Charts | `--chart-1` accent … `--chart-5` light grey | | |

Rules:

- Components use the classes, never hex. The shadcn names are kept so upstream
  components work unchanged.
- `npm run check:contrast` checks every text and UI pair in both themes (AA: 4.5:1 text,
  3:1 graphics), including text on a selected row and on the paper. Add a pair when you add
  a token.
- Text in a category colour uses `--category-*-foreground`; the unlikely grey for text is
  ink-3, because `#7A7A80` is 4.3:1 on white.

## Type

**Instrument Sans** for everything, loaded through `next/font/google` (downloaded at build
time, served by the app). Figures are tabular everywhere (`font-variant-numeric:
tabular-nums` on `body`), so scores and counts line up in columns and in sentences.

| Use | Size | Class |
|---|---|---|
| Screen title | 26px, semibold | `text-title font-semibold tracking-tight` |
| Review panel name | 20px, semibold | `text-xl font-semibold` |
| Section heading | 15px, semibold | `text-section font-semibold` |
| Summary sentence | 16px, ink-2 | `text-base text-ink-2` |
| Body | 14px | `text-sm` (the default) |
| Meta, tables, block headings | 13px | `text-dense` |
| Table headers | 12px, ink-3 | `text-xs text-muted-foreground` |
| The panel's big score | 36px, semibold | `text-score font-semibold` |

`cn()` (`@/lib/utils`) knows the custom sizes, so `cn("text-dense", "text-muted-foreground")`
keeps both. Import it from there, not from the `cn` package.

## Spacing and layout

- **One focal point per screen.** Home: the review queue. Workspace: the candidates table
  (and the open candidate). Everything else is quieter.
- **No KPI card grids.** Numbers go in a sentence ("94 applicants. 4 strong, 40 possible…")
  or a slim inline meter.
- **Hairlines, not boxes.** Sections and tables sit under a 1px `border-t`; no outer card
  borders. A tinted well (`bg-canvas`, rounded) is for one special area per screen, like
  the job-description extractor or the dropzone.
- **Page frame:** `max-w-[1080px]`, gutters `px-4 sm:px-8 lg:px-14`, `pt-6 pb-14`; 40px
  between blocks on Home, 20–24px inside the workspace. The workspace is wider
  (`max-w-[1440px]`) and the sidebar collapses to an icon rail there.
- **Tables are dense and readable:** 13px, two-line candidate cell (name in ink, email in
  ink-3), 8px row padding, no zebra. The selected row is tinted (`bg-row-active`) with an
  accent inset edge; checked rows are grey.
- **Radii:** 8px controls, 6px small controls, 10–12px wells, full for pills and avatars.
- **Motion:** colour and opacity only, at most 150ms, none with `prefers-reduced-motion`
  (`globals.css`). No bounces, no slide-ins beyond the panel.
- **Focus:** a 2px accent outline, 2px offset, on everything focusable.
- **Phone width:** one column, 16px gutters, wide tables scroll inside their own container,
  never the page.

## The score display

Every score reads the same way: **the number, a 5-segment meter and a plain-word label.**

```
90 ▮▮▮▮▮ Strong match      59 ▮▮▮▯▯ Possible match      28 ▮▯▯▯▯ Unlikely match
```

- One segment per 20 points, rounded (`ScoreSegments`). Segments take the category's
  colour; empty ones are hairline grey. The meter is decorative (`aria-hidden`): the number
  and the label carry the value.
- Labels (the API's values do not change): `strong_match` → **Strong match**,
  `moderate_match` → **Possible match**, `weak_match` → **Unlikely match**,
  `rejected` → **Not a match**. In a sentence: strong, possible, unlikely, not a match.
- Components: `ScoreCell` (number + meter, `withLabel` for the label), `MatchLabel`,
  `ScoreHeadline` (the review panel's big number), `MustHaveChips`, `ScoreBreakdown`
  ("How the score adds up": thin neutral bars, "40 / 40").
- Unscored: a dash, no meter, and a sentence that says why where there is room.

## Voice and microcopy

Plain, specific, recruiter vocabulary. Write what a good recruiter would say to a
colleague.

1. **Sentence case** everywhere: titles, buttons, tabs, headings.
2. **No exclamation marks.** No "AI-powered", "Scored by the AI", "smart", "magic".
3. **Verbs on buttons**, named after what happens: "Move to phone screen", "Upload
   resumes", "Create campaign". Not "Submit", "OK", "Proceed".
4. **Recruiter words:** candidates, applicants, must-haves, phone screen, not a fit,
   brief, shortlist. Not "entities", "records", "rejected_manual".
5. **Numbers in sentences**, with the figure first: "12 candidates are waiting for a first
   look". Real numbers only; never invent one to fill a sentence.
6. **Empty states say what to do next**, in two lines: what is (not) there, then the
   action. "No candidates yet. Upload their resumes…" with an "Upload resumes" button.
7. **Errors say what happened and what to try:** the API's problem title and detail, and
   "Try again".
8. **No ticket numbers, no internals** in the UI ("arrives with E2", "deleted meanwhile?").

| Don't | Do |
|---|---|
| Moderate match | Possible match |
| Unreviewed | New |
| Reject | Not a fit |
| Scored by the AI | (say nothing; the score speaks) |
| Upload resumes and HireFlow AI will extract, score and rank each candidate here. | Upload their resumes. Each one is read, scored against this campaign's must-haves and ranked here. |
| Applications: 94 · Strong matches: 4 · Average score: 48 | 94 applicants. 4 strong, 40 possible, 45 unlikely and 5 not a match. Average score 48. |
| Workspace and account settings arrive with agencies and sign-in (E2). | Agency and account settings arrive with sign-in. Until then, edit a campaign from its Brief tab. |
| Extracting / Analysing | Reading / Scoring |
| Saved automatically as you type | Saves as you type |
| No results found. | Nothing matches. Try a page name, like Campaigns. |

## How the mock-ups map to the app

| Mock-up | App | Where the data differs |
|---|---|---|
| Home (`Main.dc.html`) | `/`, `features/home` | The mock's greeting is dropped (D3, owner review): without a name it read as filler, so Home has a plain title and the day's numbers as one quiet row, like a campaign's. It uses the window like the workspace. A short "Good afternoon, <name>" can return after E2 adds sign-in. The queue lists `GET /home`'s recent strong matches with no stage yet (the API has no cross-campaign queue, no "why" line and no current role per match: the row shows the campaign and when it was scored instead). Campaign rows show the counts `Campaign.counts` has, with a strong-share meter instead of the mock's pipeline-stage bar (no per-stage counts per campaign) and the role instead of a client (no client field). The processing line has no "last one at 08:12" (no timestamp for the last finished upload). |
| Agency switcher (sidebar top) | Product header ("HireFlow AI") | E2-10 adds agencies; until then the header names the product only. |
| Candidates (`Candidates.dc.html`) | `/campaigns/[id]`, `features/campaigns` + `features/applications` | Header: "Created 12 Nov" (no opened-at date), average instead of median score (the stats have no median). Table rows show the email under the name (no current role on an application). "Applied" is "Updated", the column the API sorts by. The panel overlays the table on narrow screens and sits beside it on wide ones; it is non-modal, so J/K keep working. Location is not shown (not extracted). |
| Candidates, dark (`CandidatesDark.dc.html`) | Same, `.dark` tokens | Follows the system theme, or the sidebar's Theme menu. |

Screens the mock-ups did not draw (campaign list, new campaign, Uploads, Insights, Brief,
Person profile, Talent, `/dev/ui`) follow the same rules: page frame, hairlines, one
primary action, the score display, the voice.
