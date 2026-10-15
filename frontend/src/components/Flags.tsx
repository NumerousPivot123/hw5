import type { Flag } from '../types'

const KIND_LABELS: Record<string, string> = {
  ticket_failure: 'Agents had difficulty',
  needs_human: 'Needs your decision',
  scope: 'Agent outside its role',
  optimization: 'Workflow idea',
  safety: 'Safety concern',
}

interface Props {
  flags: Flag[]
  onResolve: (id: number) => void
}

export function Flags({ flags, onResolve }: Props) {
  if (!flags.length) return null
  return (
    <section className="panel flags-panel" aria-label="Flags for a person">
      <div className="panel-head">
        <h2>🚩 Flags for a person</h2>
      </div>
      <p className="panel-intro">
        The agents raise these when they need a human. Press <strong>Resolved</strong> once you've dealt with one; its ticket goes back on the list.
      </p>
      <ul className="flag-list">
        {flags.map((f) => (
          <li key={f.id} className={`flag kind-${f.kind}`}>
            <div>
              <div className="flag-kind">
                {KIND_LABELS[f.kind] ?? f.kind}{f.ticket_id ? ` · Ticket #${f.ticket_id}` : ''}
              </div>
              <p>{f.message}</p>
            </div>
            <button className="btn ghost" onClick={() => onResolve(f.id)}>Resolved</button>
          </li>
        ))}
      </ul>
    </section>
  )
}
