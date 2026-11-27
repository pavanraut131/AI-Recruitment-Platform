/**
 * Where "Continue with Google" and "Continue with Microsoft" go (EPIC-02
 * §4.3, §9.1): on `/login` above the email form, and on `/accept-invite` for
 * an invited person. **Empty until E2-08** (sign-in with Google and
 * Microsoft) lands, so it renders nothing yet.
 *
 * To fill it (E2-08):
 * - list the configured providers with `GET /auth/providers`
 *   (`listAuthProviders`; an unconfigured provider is not listed, so the slot
 *   stays empty when there is none) in a query that never toasts;
 * - one outline button per provider, full width, "Continue with Google" /
 *   "Continue with Microsoft", above a hairline with "or" in the middle;
 * - on click: `POST /auth/oidc/{provider}/start` with `{ next,
 *   invitation_token }` and `window.location.assign(authorization_url)`;
 * - the callback comes back to `next` (or `/login?error=no_account`, which the
 *   login page already explains).
 *
 * The props are what the start request needs; both pages pass them already.
 */
export type SignInProvidersProps = {
  /** Where to land after signing in (a path from `safeNextPath`). */
  next: string
  /** The invitation being accepted (`/accept-invite`), so the provider's account joins its agency. */
  invitationToken?: string
}

export function SignInProviders(props: SignInProvidersProps): React.ReactNode {
  void props
  return null
}
