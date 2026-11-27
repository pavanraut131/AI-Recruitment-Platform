import { expect } from "@playwright/test"

/*
 * The E2E stack's Mailpit (EPIC-02 §4.4, §11.4): the emails the backend sends
 * (invitations, password resets) land there, and its HTTP API
 * (`E2E_MAILPIT_URL`, passed by scripts/e2e.sh) lets a test read the link.
 */

const MAILPIT_URL = process.env.E2E_MAILPIT_URL ?? "http://localhost:18025"

type MessageSummary = { ID: string; Subject: string; Created: string }

async function search(query: string): Promise<MessageSummary[]> {
  const response = await fetch(`${MAILPIT_URL}/api/v1/search?query=${encodeURIComponent(query)}`)
  expect(response.ok, `Mailpit search: ${response.status}`).toBe(true)
  const body = (await response.json()) as { messages: MessageSummary[] }
  return body.messages
}

/** The id of the newest email to `address` (newest first), or `null`. */
export async function latestEmailId(address: string): Promise<string | null> {
  const messages = await search(`to:"${address}"`)
  return messages[0]?.ID ?? null
}

/**
 * Waits for a new email to `address` (newer than `previousId`, the newest one
 * before the action that sends it) whose text has a link to `path`, and
 * returns that link. Emails go out from a Celery task, so this polls.
 */
export async function waitForLink(address: string, path: string, previousId: string | null): Promise<string> {
  let id: string | null = null
  await expect
    .poll(
      async () => {
        id = await latestEmailId(address)
        return id !== null && id !== previousId
      },
      { message: `an email to ${address}`, timeout: 30_000, intervals: [500, 1_000, 2_000] },
    )
    .toBe(true)
  const response = await fetch(`${MAILPIT_URL}/api/v1/message/${id}`)
  expect(response.ok).toBe(true)
  const message = (await response.json()) as { Text: string }
  const link = message.Text.match(new RegExp(`https?://\\S+${path.replace(/[/]/g, "\\/")}#token=[\\w-]+`))?.[0]
  expect(link, `a ${path} link in the email to ${address}`).toBeTruthy()
  return link!
}
