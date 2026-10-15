import type { Approval } from '../types'
import { money } from '../format'

const LABELS: Record<string, string> = {
  pay_rent: 'Rent payment',
  pay_invoice: 'Vendor invoice payment',
}

interface Props {
  payments: Approval[]
  busy: boolean
  onAccept: (id: number) => void
  onReject: (id: number) => void
}

export function Payments({ payments, busy, onAccept, onReject }: Props) {
  if (!payments.length) return null
  return (
    <section className="panel payments-panel" aria-label="Payments waiting for you">
      <div className="panel-head">
        <h2>💳 Payments waiting for your approval</h2>
      </div>
      <p className="panel-intro">
        The agents only <em>prepare</em> payments. The boss has signed off on these; nothing is paid until you accept.
      </p>
      <ul className="payment-list">
        {payments.map((p) => (
          <li key={p.id} className="payment">
            <div>
              <div className="payment-title">
                {LABELS[p.action] ?? p.action} · <strong>{money(Math.abs(p.economic_impact))}</strong>
              </div>
              <div className="payment-meta">
                Ticket #{p.ticket_id} · prepared by the {p.agent.replaceAll('_', ' ')} agent · approval #{p.id}
              </div>
              {p.details && <p className="payment-details">{p.details}</p>}
            </div>
            <div className="payment-actions">
              <button className="btn accept" disabled={busy} onClick={() => onAccept(p.id)}>Accept payment</button>
              <button className="btn reject" disabled={busy} onClick={() => onReject(p.id)}>Reject payment</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
