import type { DatabaseState } from '../types'
import { money } from '../format'

const SIZE_ORDER = ['XS', 'S', 'M', 'L', 'XL', 'XXL', 'OS']
const bySize = ([a]: [string, number], [b]: [string, number]) => SIZE_ORDER.indexOf(a) - SIZE_ORDER.indexOf(b)

export function DatabaseView({ db }: { db: DatabaseState | null }) {
  if (!db) return null
  return (
    <section className="panel db-panel" aria-label="Current state of the database">
      <div className="panel-head">
        <h2>🗄️ The database right now</h2>
        <span className="muted">campus_customs_new.db · desk date <strong>{db.desk.date_today}</strong></span>
      </div>
      <p className="panel-intro">Everything the agents read and change. It updates as soon as they finish.</p>

      <div className="db-grid">
        <div className="db-card">
          <h3>Cash</h3>
          {db.cash_accounts.map((a) => (
            <div key={a.name} className="big-number">
              {money(a.balance)}
              <div className="muted">{a.name} · as of {a.date}</div>
            </div>
          ))}
        </div>

        <div className="db-card">
          <h3>Lease</h3>
          {db.leases.map((l) => (
            <div key={l.id}>
              <strong>{l.space_name}</strong> · {l.landlord}
              <div>{money(l.monthly_rent)}/month · next due <strong>{l.next_due}</strong> ({l.days_until_due} days)</div>
              {l.notes && <div className="muted">{l.notes}</div>}
            </div>
          ))}
        </div>

        <div className="db-card wide">
          <h3>Inventory</h3>
          <table>
            <thead><tr><th>Item</th><th>Stock by size</th><th>Total</th><th>Aisle</th><th>Price</th></tr></thead>
            <tbody>
              {db.inventory.map((i) => (
                <tr key={i.sku}>
                  <td><strong>{i.name}</strong><div className="muted">{i.sku}</div></td>
                  <td>
                    {Object.entries(i.stock_by_size).sort(bySize).map(([size, qty]) => (
                      <span key={size} className={`size ${qty === 0 ? 'out' : ''}`}>{size}: {qty}</span>
                    ))}
                  </td>
                  <td>{i.total_in_stock}</td>
                  <td>{i.location}</td>
                  <td>{money(i.list_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="db-card">
          <h3>Invoices</h3>
          {db.invoices.length === 0 && <p className="muted">None</p>}
          {db.invoices.map((inv) => (
            <div key={inv.id} className="row-item">
              <strong>#{inv.id}</strong> {inv.vendor_name} · {money(inv.amount)}
              <span className={`status-pill ${inv.status === 'paid' ? 'complete' : 'waiting'}`}>{inv.status}</span>
              <div className="muted">
                {inv.description} · due {inv.due_date}{inv.days_overdue ? ` · ${inv.days_overdue} days overdue` : ''}
              </div>
            </div>
          ))}
        </div>

        <div className="db-card">
          <h3>Payments made</h3>
          {db.payments.length === 0 && <p className="muted">None yet</p>}
          {db.payments.map((p) => (
            <div key={p.id} className="row-item">
              <strong>{p.kind}</strong> · {money(p.amount)} · {p.paid_at}
              <div className="muted">from {p.account} · approved by {p.approved_by}</div>
            </div>
          ))}
        </div>

        <div className="db-card wide">
          <h3>Customer memos <span className="muted">(saved, never sent)</span></h3>
          {db.memos.length === 0 && <p className="muted">None yet</p>}
          {db.memos.map((m) => (
            <div key={m.id} className="memo">
              <div className="memo-head">To <strong>{m.recipient}</strong> · Ticket #{m.ticket_id} · <em>{m.status.replaceAll('_', ' ')}</em></div>
              <p>{m.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
