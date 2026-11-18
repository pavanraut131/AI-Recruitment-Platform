import "./legacy.css"

/*
 * TODO(E1-10): the last prototype screen, the legacy candidate page, lives in
 * this route group (campaigns were replaced by src/app/campaigns in E1-04,
 * the candidate by the Person profile in E1-07, home by src/app/page.tsx in
 * E1-08). E1-10 deletes this group, legacy.css and the v1 client.
 *
 * The prototype was built dark-only with hard-coded colours, so it renders
 * as a dark island (`.dark` resolves the dark tokens) inside the new shell.
 */
export default function LegacyLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="legacy-prototype dark min-h-full bg-background text-foreground">
      {children}
    </div>
  )
}
