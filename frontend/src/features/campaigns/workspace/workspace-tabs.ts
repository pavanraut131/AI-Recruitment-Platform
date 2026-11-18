import { parseAsStringLiteral } from "nuqs"

/** The campaign workspace's tabs (spec §4), in display order. */
export const WORKSPACE_TABS = ["candidates", "uploads", "insights", "settings"] as const
export type WorkspaceTab = (typeof WORKSPACE_TABS)[number]

export const WORKSPACE_TAB_LABELS: Record<WorkspaceTab, string> = {
  candidates: "Candidates",
  uploads: "Uploads",
  insights: "Insights",
  settings: "Settings",
}

/**
 * The workspace owns the `tab` search param (`?tab=uploads`); the default,
 * candidates, is left out of the URL. Other workspace state (the candidates
 * table's filters, E1-05) uses its own keys. A tab change is a history entry,
 * so Back returns to the previous tab.
 */
export const workspaceTabParser = parseAsStringLiteral(WORKSPACE_TABS)
  .withDefault("candidates")
  .withOptions({ history: "push" })
