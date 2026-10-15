import { useState } from 'react'
import type { Ticket } from '../types'

const TYPE_LABELS: Record<string, string> = {
  customer_order: 'Customer order',
  rent_notice: 'Rent notice',
  price_override: 'Price override',
  customer_inquiry: 'Customer question',
}

const STATUS_TEXT: Record<Ticket['status'], string> = {
  open: 'Open',
  in_progress: 'In progress',
  waiting: 'Waiting',
  flagged: 'Needs a person',
  complete: 'Resolved',
}

interface Props {
  tickets: Ticket[]
  runningId: number | null
  busy: boolean
  onStart: (id: number) => void
}

function Summary({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const short = text.length > 220 && !open ? `${text.slice(0, 220)}…` : text
  return (
    <p className="ticket-summary">
      {short}
      {text.length > 220 && (
        <button className="link" onClick={() => setOpen(!open)}>{open ? 'Show less' : 'Show more'}</button>
      )}
    </p>
  )
}

export function Tickets({ tickets, runningId, busy, onStart }: Props) {
  return (
    <ul className="ticket-list">
      {tickets.map((t) => {
        const running = runningId === t.id
        const resolved = t.state === 'resolved'
        return (
          <li key={t.id} className={`ticket ${resolved ? 'resolved' : ''} ${running ? 'running' : ''} status-${t.status}`}>
            <div className="ticket-check" aria-hidden="true">{resolved ? '✓' : `#${t.id}`}</div>
            <div className="ticket-body">
              <div className="ticket-top">
                <span className="ticket-id">Ticket #{t.id}</span>
                <span className="tag">{TYPE_LABELS[t.type] ?? t.type}</span>
                {t.priority === 'urgent' && <span className="tag urgent">Urgent</span>}
                <span className={`status-pill ${t.status}`}>{running ? 'Agents working…' : STATUS_TEXT[t.status]}</span>
              </div>
              <h3>{t.subject}</h3>
              <p className="ticket-meta">
                From <strong>{t.requester}</strong>
                {t.assigned_agent && <> · handled by <strong>{t.assigned_agent.replaceAll('_', ' ')}</strong></>}
              </p>
              {t.resolution && <Summary text={t.resolution} />}
            </div>
            <div className="ticket-action">
              {resolved ? (
                <span className="done-badge">✓ Resolved</span>
              ) : (
                <button
                  className="btn primary"
                  disabled={busy || t.status !== 'open'}
                  title={t.status !== 'open' ? `This ticket is ${STATUS_TEXT[t.status].toLowerCase()}` : 'Summon the boss agent'}
                  onClick={() => onStart(t.id)}
                >
                  {running ? <><span className="spinner" /> Running…</> : 'Start agent team'}
                </button>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
