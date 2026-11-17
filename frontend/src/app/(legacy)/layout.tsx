import "./legacy.css"

/*
 * TODO(E1-04..08): the prototype screens in this route group are replaced
 * one by one (campaigns: E1-04/05, uploads: E1-06, candidate → person
 * profile: E1-07, home: E1-08). Delete each page, its old component under
 * src/components/*.tsx and its rules in legacy.css as it is replaced; E1-09
 * deletes this group.
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
