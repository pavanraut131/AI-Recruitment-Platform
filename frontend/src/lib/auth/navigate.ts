/**
 * A full page load of `href`. Used when a session starts over elsewhere
 * (sign-out, an ended session): unlike a client-side navigation it drops
 * everything this page holds in memory, so nothing of one user is left for
 * the next. Its own module so tests can replace it (jsdom cannot navigate).
 */
export function navigateTo(href: string): void {
  window.location.assign(href)
}
