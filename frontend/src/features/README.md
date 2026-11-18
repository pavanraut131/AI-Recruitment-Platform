# Features

Product code is grouped by **feature**, one folder per area of the product
(EPIC-01 §7):

```
features/
  campaigns/      list, new campaign, workspace tabs, hooks
  applications/   candidates table, filters, review panel, bulk actions
  uploads/        dropzone, processing list
  people/         person profile, talent list
  home/
```

Folders are created by the tickets that need them (E1-03 onwards).

Each feature exposes what routes may use from its `index.ts` (for example
`@/features/campaigns` exports `CampaignList`, `NewCampaignForm` and
`CampaignWorkspace`); the rest of the folder is internal.

When one feature's screen hosts another feature's content, the **route**
composes them through a slot prop, so neither feature imports the other. The
campaign workspace takes `candidatesTab` and `uploadsTab` (React nodes), and
`src/app/campaigns/[id]/page.tsx` passes the applications feature's table and
the uploads feature's tab into them.

## Rules

1. **Routes compose features.** Files in `src/app` stay thin: read params,
   pick a feature component, render it. A feature's public entry is its
   `index.ts`; a `dev.ts` entry may expose fixtures for `/dev/*` routes only
   (e.g. `applications/dev.ts` for `/dev/candidates`).
2. **Features never import each other.** Code in `src/features/<a>/**` may not
   import from `src/features/<b>/**`. If two features need the same thing, it
   is shared code: move it to
   - `src/components/` for UI (`ui/` holds the shadcn primitives,
     `app-shell/` the sidebar, top bar and ⌘K palette), or
   - `src/lib/` for non-UI code: the API client and query keys (`lib/api`),
     the Query provider and shared data hooks such as `useCampaignEvents`
     (`lib/query`; the campaign workspace and the Uploads tab both use it),
     utilities.
3. **No `axios`, `react-toastify` or legacy `@/lib/api`** (the v1 client) in
   features. Use the generated client from `@/lib/api/client` (ADR 0006) with
   the keys in `@/lib/api/query-keys`, and `toast` from `sonner`. Server state
   lives in TanStack Query, URL state in nuqs, the rest in React (ADR 0007).

Inside a feature, import freely, relatively or via `@/features/<same>/…`.

## Enforcement

`eslint.config.mjs` generates a `no-restricted-imports` block for every folder
in `src/features`, so a new feature is covered automatically. `npm run lint`
fails on a violation, for example:

```
src/features/campaigns/list.tsx
  1:1  error  '@/features/people/hooks' import is restricted from being used by a pattern.
       src/features/campaigns may not import another feature. Move shared code to
       src/components or src/lib (see src/features/README.md)  no-restricted-imports
```

This is the frontend counterpart of the backend's import-linter contracts.
