import { hashKey, QueryClient, type QueryKey } from "@tanstack/react-query"
import { describe, expect, it } from "vitest"

import { queryKeys } from "./query-keys"

/** The keys of the cached queries a filter matches (as invalidation would). */
function matching(client: QueryClient, queryKey: QueryKey): string[] {
  return client
    .getQueryCache()
    .findAll({ queryKey })
    .map((query) => hashKey(query.queryKey))
    .sort()
}

describe("queryKeys", () => {
  it("has one factory per resource", () => {
    expect(Object.keys(queryKeys).sort()).toEqual(
      ["applications", "authProviders", "campaigns", "home", "me", "people", "resumes", "stats"].sort(),
    )
  })

  it("puts the request's params in list keys", () => {
    expect(queryKeys.campaigns.list({ status: ["active"], q: "eng" })).toEqual([
      "campaigns",
      "list",
      { status: ["active"], q: "eng" },
    ])
    expect(queryKeys.campaigns.list()).toEqual(["campaigns", "list", {}])
    expect(queryKeys.applications.list("c-1", { sort: "-score", page: 2 })).toEqual([
      "applications",
      "list",
      "c-1",
      { sort: "-score", page: 2 },
    ])
    expect(queryKeys.resumes.list("c-1", { page: 3 })).toEqual(["resumes", "list", "c-1", { page: 3 }])
    expect(queryKeys.people.list({ q: "ada" })).toEqual(["people", "list", { q: "ada" }])
    expect(queryKeys.people.detail("p-1")).toEqual(["people", "detail", "p-1"])
    expect(queryKeys.home.all).toEqual(["home"])
    expect(queryKeys.stats.all).toEqual(["stats"])
    expect(queryKeys.me.all).toEqual(["me"])
  })

  it("hashes params independently of their order and of undefined values", () => {
    expect(hashKey(queryKeys.applications.list("c-1", { page: 2, q: "a" }))).toBe(
      hashKey(queryKeys.applications.list("c-1", { q: "a", page: 2, skill: undefined })),
    )
    expect(hashKey(queryKeys.applications.list("c-1", { page: 2 }))).not.toBe(
      hashKey(queryKeys.applications.list("c-1", { page: 3 })),
    )
  })

  it("nests keys so that a prefix selects exactly the queries below it", () => {
    const client = new QueryClient()
    const keys = [
      queryKeys.campaigns.list(),
      queryKeys.campaigns.list({ status: ["closed"] }),
      queryKeys.campaigns.detail("c-1"),
      queryKeys.campaigns.stats("c-1"),
      queryKeys.campaigns.detail("c-2"),
      queryKeys.applications.list("c-1"),
      queryKeys.applications.list("c-1", { category: ["strong_match"] }),
      queryKeys.applications.list("c-2"),
      queryKeys.applications.detail("a-1"),
      queryKeys.resumes.list("c-1"),
      queryKeys.resumes.list("c-2"),
      queryKeys.people.list(),
      queryKeys.people.detail("p-1"),
    ]
    for (const key of keys) client.setQueryData(key, {})

    const hashes = (...wanted: QueryKey[]) => wanted.map((key) => hashKey(key)).sort()

    expect(matching(client, queryKeys.campaigns.lists())).toEqual(
      hashes(queryKeys.campaigns.list(), queryKeys.campaigns.list({ status: ["closed"] })),
    )
    // A campaign's detail key covers its stats, not another campaign.
    expect(matching(client, queryKeys.campaigns.detail("c-1"))).toEqual(
      hashes(queryKeys.campaigns.detail("c-1"), queryKeys.campaigns.stats("c-1")),
    )
    expect(matching(client, queryKeys.applications.campaignLists("c-1"))).toEqual(
      hashes(
        queryKeys.applications.list("c-1"),
        queryKeys.applications.list("c-1", { category: ["strong_match"] }),
      ),
    )
    expect(matching(client, queryKeys.applications.lists())).toHaveLength(3)
    expect(matching(client, queryKeys.applications.all)).toHaveLength(4)
    expect(matching(client, queryKeys.resumes.campaignLists("c-2"))).toEqual(
      hashes(queryKeys.resumes.list("c-2")),
    )
    expect(matching(client, queryKeys.people.all)).toHaveLength(2)
  })
})
