# 0007 · Frontend state model: server state in Query, URL state in nuqs, the rest in React
**Status:** accepted  ·  **Date:** 2026-11-17

## Context
The old UI fetches in `useEffect`, keeps the results in component state, and polls or re-fetches by hand after every change. The same campaign is loaded several times over, a filter is lost on reload, and a stage change waits for a full reload of the table. E1 rebuilds every screen ([spec §4–§7](../specs/EPIC-01-frontend.md#7-frontend-architecture)). Before the first feature (E1-04, E1-05) is written, we need one answer to "where does this piece of state live?"

The state on these screens comes in three kinds:
- **Server state:** campaigns, applications, resumes, people, stats. The server owns it. Other users and the processing pipeline change it (live, through SSE), so it needs caching, deduplication, refetching, invalidation after mutations, and optimistic updates (bulk stage changes).
- **URL state:** filters, sort, page, the selected tab, the open review panel. It should survive a reload, be shareable as a link, and work with back and forward.
- **Local UI state:** an open dialog, a form's values before submit, the selected rows, hover. It is short-lived and belongs to one component tree.

## Decision
- **Server state lives only in TanStack Query v5.** Every read is a `useQuery` with a key from `src/lib/api/query-keys.ts` and a query function on the generated client ([ADR 0006](0006-contract-first-api-client.md)). Every write is a `useMutation` that invalidates, or optimistically updates and rolls back, by key prefix. Server data is never copied into `useState` or a store.
  - **Defaults** (`src/lib/query/query-client.ts`): `staleTime` 30 s; retries (up to 3) only for network errors and 5xx, never for 4xx or for errors that are not an `ApiProblem`; `refetchOnWindowFocus` and `refetchOnReconnect` for mounted views; no mutation retries.
  - **Errors:** a failed mutation shows a sonner toast with the problem's title and `detail`. A failed query toasts only when a background refetch fails while data is on screen; a first load that fails is the view's error state ([spec §8](../specs/EPIC-01-frontend.md#8-accessibility-and-quality-bar)). `meta: { errorToast }` overrides this per query or mutation, for example a form that shows 422 field errors inline. Identical messages share one toast.
  - **Live updates** come through `useCampaignEvents(campaignId)` (`src/lib/query`): one `EventSource` per open campaign that writes into the same cache. It patches resume lists with `setQueryData`, invalidates applications, the campaign and the stats when a resume finishes (debounced), and reconnects with backoff. It is shared code in `src/lib`, not in `features/uploads`, because both the campaign workspace and the Uploads tab mount it, and features may not import each other.
  - **One `QueryClient` per browser.** It is created by `getQueryClient()` (a fresh one per server render) in `DataProviders`, a client component in the root layout. The devtools load only in development.
- **URL state lives in nuqs.** Filters, sort, page, tabs and the open panel are typed search-param hooks (`useQueryState(s)` with parsers). Query keys are built from those parsed values, so the URL drives the fetch. The `NuqsAdapter` for the App Router is installed once in `DataProviders`.
- **Everything else is React state:** `useState` / `useReducer` in the component that owns it, lifted only as far as needed, and react-hook-form for form values.
- **No global client store.** No Redux, Zustand or Jotai, and no context that holds server data. A store only becomes worth adding if state appears that fits none of the three kinds, crosses distant parts of the tree and must not be in the URL. That needs a new ADR.
- **Server Components** render the shell and pass ids, not data. Data screens are client components on Query (spec §7: the data is per-user and live, and E2 adds auth). Prefetching on the server with Query's hydration is a later optimisation and fits this model unchanged.

## Alternatives considered
- **A global store (Redux Toolkit + RTK Query, or Zustand) for everything.** It gives one mental model, but either the store copies server data (stale-cache bugs, hand-written invalidation) or RTK Query does what TanStack Query does with more ceremony. URL state in a store also has to be synced back to the URL by hand.
- **SWR.** It is lighter, but has weaker mutation support: no mutation cache, and it lacks the optimistic update and rollback helpers that E1-05's bulk actions need, as well as the devtools.
- **`useEffect` fetching plus context** (today's approach). This is what we are replacing: duplicate requests, no cache, races, and manual refetching.
- **Next.js server data fetching only** (Server Components with `fetch` caching and Server Actions). It suits static content. Here, though, the data is per-user and changes live through SSE, and the interactive table needs client-side cache updates. It would also put E2's auth on two paths.
- **Plain `useSearchParams` / `router.push` for URL state.** It works, but parsing, typing, defaults, debouncing and batching updates would have to be written for every filter. nuqs does that in one typed hook.

## Consequences
- **Easier:** each kind of state has one place, so a review can ask "is this in the right place?". Caching, deduplication, retries and refetching are uniform. Live updates and mutations stay consistent because they share keys. Filters survive reloads and links. Tests render hooks with a fresh `QueryClient`, without mocking a store.
- **Harder:** you have to know Query's key and invalidation model, and keys must come from the factories, never be written by hand. Optimistic updates need careful rollback code. Two libraries (Query and nuqs) must be learned instead of one store.
- **Watch for:**
  - Keys that leave out a parameter the query function uses. That serves wrong cached data, so build keys and requests from the same params object.
  - Copying query data into `useState` to edit it. Use the form library's default values instead.
  - Too many invalidations from SSE bursts. They are debounced in the hook; keep it that way.
  - Toasts that duplicate an inline error. Set `meta.errorToast` where the view already shows the problem.
