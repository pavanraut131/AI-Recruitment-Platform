import { createCn } from "cn/config"

/**
 * clsx + tailwind-merge (the `cn` package), taught the type scale's custom
 * sizes (src/styles/tokens.css): without this, `text-dense` would read as a
 * colour and silently drop `text-muted-foreground` (or the other way round).
 * Every component, shadcn's included, imports `cn` from here.
 */
export const cn = createCn({
  extend: { classGroups: { "font-size": [{ text: ["dense", "section", "title", "score"] }] } },
})
