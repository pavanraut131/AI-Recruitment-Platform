/**
 * One block of a Settings page: a section heading and its sentence, an
 * optional action on the right (Invite, New client), then the content.
 * Sections sit under a hairline, not in boxes (docs/design/README.md).
 */
export function SettingsSection({
  id,
  title,
  description,
  action,
  children,
}: {
  id: string
  title: string
  description?: React.ReactNode
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex max-w-2xl flex-col gap-1">
          <h2 id={id} className="text-section font-semibold">
            {title}
          </h2>
          {description && <p className="text-ink-2">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}
