import { useCallback, useEffect, useRef, useState } from 'react'
import { api, API_BASE } from './api'
import { AGENTS, AgentNetwork } from './components/AgentNetwork'
import { DatabaseView } from './components/DatabaseView'
import { Flags } from './components/Flags'
import { PaymentApproved } from './components/Modal'
import { Payments } from './components/Payments'
import { Tickets } from './components/Tickets'
import { agentName, money, time } from './format'
import type {
  AgentName, Approval, CashAccount, DatabaseState, EventSummary, Flag, PaymentResult, Ticket, TimelineItem,
} from './types'

const AGENT_IDS = new Set<string>(AGENTS.map((a) => a.id))
const LIT_FOR_MS = 2500 // an agent stays lit this long after its last step
const POLL_MS = 700

type Notice = { tone: 'success' | 'info' | 'error'; text: string }

const IDLE_CAPTION = 'The team is resting. Press "Start agent team" on a ticket to wake them up.'

/** Which agents did something in the last couple of seconds (the newest one is always lit). */
function activeAgents(events: EventSummary): Set<AgentName> {
  const now = Date.now()
  const lit = new Set<AgentName>()
  let newest: { agent: AgentName; t: number } | null = null
  for (const [agent, info] of Object.entries(events.agents)) {
    if (!AGENT_IDS.has(agent) || !info.last_active) continue
    const t = new Date(info.last_active).getTime()
    if (now - t <= LIT_FOR_MS) lit.add(agent as AgentName)
    if (!newest || t > newest.t) newest = { agent: agent as AgentName, t }
  }
  if (newest) lit.add(newest.agent)
  return lit
}

function latestResolved(timeline: TimelineItem[], tickets: Ticket[]): Ticket | null {
  for (let i = timeline.length - 1; i >= 0; i--) {
    const item = timeline[i]
    if (item.summary === 'ticket_tool: complete' && item.ticket_id !== null) {
      const ticket = tickets.find((t) => t.id === item.ticket_id)
      if (ticket?.state === 'resolved') return ticket
    }
  }
  return null
}

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [cash, setCash] = useState<CashAccount | null>(null)
  const [db, setDb] = useState<DatabaseState | null>(null)
  const [payments, setPayments] = useState<Approval[]>([])
  const [flags, setFlags] = useState<Flag[]>([])
  const [events, setEvents] = useState<EventSummary | null>(null)
  const [offline, setOffline] = useState(false)

  const [running, setRunning] = useState<{ ticketId: number | null } | null>(null)
  const [busy, setBusy] = useState(false)
  const [lit, setLit] = useState<Set<AgentName>>(new Set())
  const [caption, setCaption] = useState(IDLE_CAPTION)
  const [celebrate, setCelebrate] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [approved, setApproved] = useState<PaymentResult | null>(null)
  const poller = useRef<number | null>(null)

  const load = useCallback(async () => {
    try {
      const [t, c, d, p, f, e] = await Promise.all([
        api.tickets(), api.cash(), api.database(), api.pendingPayments(), api.flags(), api.events(),
      ])
      setTickets(t); setCash(c); setDb(d); setPayments(p); setFlags(f); setEvents(e)
      setOffline(false)
    } catch {
      setOffline(true)
    }
  }, [])

  useEffect(() => {
    load()
    const id = window.setInterval(() => { if (!poller.current) load() }, 15000)
    return () => window.clearInterval(id)
  }, [load])

  // While the agents run, watch the audit trail and light up whoever is working.
  const startWatching = () => {
    setCaption('Summoning the boss agent…')
    setLit(new Set<AgentName>(['boss']))
    poller.current = window.setInterval(async () => {
      try {
        const e = await api.events(120)
        setEvents(e)
        setLit(activeAgents(e))
        const last = e.timeline[e.timeline.length - 1]
        if (last) setCaption(`${agentName(last.agent)}: ${last.summary}`)
      } catch {
        // keep the last picture; the run itself reports errors
      }
    }, POLL_MS)
  }

  const stopWatching = () => {
    if (poller.current) window.clearInterval(poller.current)
    poller.current = null
    setLit(new Set())
    setCaption(IDLE_CAPTION)
  }

  const party = () => {
    setCelebrate(true)
    window.setTimeout(() => setCelebrate(false), 2200)
  }

  const runWith = async (ticketId: number | null, work: () => Promise<Ticket[]>) => {
    setRunning({ ticketId }); setBusy(true); setNotice(null)
    startWatching()
    try {
      const after = await work()
      const done = ticketId === null ? after : after.filter((t) => t.id === ticketId)
      const resolved = done.filter((t) => t.state === 'resolved').length
      if (ticketId !== null) {
        const t = done[0]
        if (t?.state === 'resolved') setNotice({ tone: 'success', text: `✓ Ticket #${ticketId} resolved. See what the agents did on the ticket below.` })
        else if (t?.status === 'waiting') setNotice({ tone: 'info', text: `Ticket #${ticketId} is waiting: check for a payment to approve, or a customer reply.` })
        else if (t?.status === 'flagged') setNotice({ tone: 'error', text: `Ticket #${ticketId} needs a person. See the flags section.` })
        else setNotice({ tone: 'info', text: `Ticket #${ticketId} is now ${t?.status ?? 'updated'}.` })
      } else {
        setNotice({ tone: 'success', text: `The team worked through the queue: ${resolved} ticket(s) resolved.` })
      }
      if (resolved) party()
    } catch (error) {
      setNotice({ tone: 'error', text: `The agents couldn't finish: ${(error as Error).message}` })
    } finally {
      stopWatching()
      setRunning(null); setBusy(false)
      await load()
    }
  }

  const startTicket = (id: number) => runWith(id, async () => [(await api.runTicket(id)).ticket])
  const startAll = () => runWith(null, async () => (await api.runAll()).tickets)

  const accept = async (id: number) => {
    setBusy(true); setNotice(null)
    try {
      setApproved(await api.approvePayment(id))
      party()
    } catch (error) {
      setNotice({ tone: 'error', text: `Payment couldn't be made: ${(error as Error).message}` })
    } finally {
      setBusy(false)
      await load()
    }
  }

  const reject = async (id: number) => {
    setBusy(true)
    try {
      const r = await api.rejectPayment(id)
      setNotice({ tone: 'info', text: `Payment rejected. Nothing was paid, and ticket #${r.ticket_id} is back on the tickets list.` })
    } catch (error) {
      setNotice({ tone: 'error', text: (error as Error).message })
    } finally {
      setBusy(false)
      await load()
    }
  }

  const resolveFlag = async (id: number) => {
    await api.resolveFlag(id)
    await load()
  }

  const reset = async () => {
    if (!window.confirm('Reset the database to the original data for a fresh run?')) return
    setBusy(true)
    try {
      await api.reset()
      setNotice({ tone: 'info', text: 'Database reset to the original data. Ready for a fresh run.' })
    } finally {
      setBusy(false)
      await load()
    }
  }

  const resolvedTicket = events ? latestResolved(events.timeline, tickets) : null
  const feed = events ? [...events.timeline].reverse().slice(0, 8) : []
  const openCount = tickets.filter((t) => t.status === 'open').length

  return (
    <div className={`app ${celebrate ? 'celebrate' : ''}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-y">Y</span>
          <div>
            <h1>Campus Customs Agent Desk</h1>
            <p>Five AI bulldogs run the Yale Bulldog Blue shop: you watch, and approve the money.</p>
          </div>
        </div>
        <div className="cash-box" aria-label="Checking balance">
          <span className="cash-label">Checking balance</span>
          <span className="cash-amount">{cash ? money(cash.balance) : '…'}</span>
          {cash && <span className="cash-date">as of {cash.date}</span>}
        </div>
      </header>

      {offline && (
        <div className="banner error">
          Can't reach the backend at {API_BASE}. Start it from the <code>backend</code> folder with{' '}
          <code>uvicorn main:app --reload --port 8000</code>, then refresh.
        </div>
      )}

      <main>
        <section className="how">
          <div><b>1</b> Pick a ticket and press <strong>Start agent team</strong>.</div>
          <div><b>2</b> Watch the bulldogs light up as each agent works.</div>
          <div><b>3</b> Approve any payment they prepare. Resolved tickets get a ✓.</div>
        </section>

        {notice && (
          <div className={`banner ${notice.tone}`} role="status">
            {notice.text}
            <button className="link" onClick={() => setNotice(null)}>Dismiss</button>
          </div>
        )}

        <section className="panel" aria-label="Tickets">
          <div className="panel-head">
            <h2>🎟️ Latest tickets</h2>
            <div className="toolbar">
              <button className="btn secondary" disabled={busy || !openCount} onClick={startAll}>
                Run all open tickets ({openCount})
              </button>
              <button className="btn ghost" disabled={busy} onClick={reset}>Reset database</button>
            </div>
          </div>
          <Tickets tickets={tickets} runningId={running?.ticketId ?? null} busy={busy} onStart={startTicket} />
        </section>

        <Payments payments={payments} busy={busy} onAccept={accept} onReject={reject} />
        <Flags flags={flags} onResolve={resolveFlag} />

        <section className="panel latest" aria-label="Latest agent action">
          <div className="latest-grid">
            <div>
              <h2>🐾 Latest from the team</h2>
              {resolvedTicket ? (
                <div className="latest-card">
                  <div className="latest-title"><span className="mini-check">✓</span> Ticket #{resolvedTicket.id} resolved: {resolvedTicket.subject}</div>
                  <p>{resolvedTicket.resolution}</p>
                </div>
              ) : (
                <p className="muted">No tickets resolved yet. Start the agent team on a ticket to see how it gets resolved.</p>
              )}
            </div>
            <div>
              <h2>📜 Recent activity</h2>
              {feed.length ? (
                <ol className="feed">
                  {feed.map((item, i) => (
                    <li key={`${item.ts}-${i}`}>
                      <span className="feed-time">{time(item.ts)}</span>
                      <span className={`chip agent-${item.agent}`}>{agentName(item.agent)}</span>
                      <span className="feed-text">
                        {item.ticket_id !== null && <span className="muted">#{item.ticket_id} </span>}
                        {item.summary}
                      </span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="muted">Nothing yet. Activity shows up here as the agents work.</p>
              )}
            </div>
          </div>
        </section>

        <AgentNetwork
          active={lit}
          running={!!running}
          ticketId={running?.ticketId ?? null}
          caption={caption}
          activity={events?.agents ?? {}}
        />

        <DatabaseView db={db} />
      </main>

      <footer className="footer">
        <img src="/bulldog.png" alt="Handsome Dan, the Yale bulldog, in a Y sweater" />
      </footer>

      {approved && (
        <PaymentApproved onClose={() => setApproved(null)}>
          <p>
            {money(Math.abs(approved.amount))} for ticket #{approved.ticket_id} has been paid.
          </p>
          <p>Checking is now <strong>{money(approved.checking.balance)}</strong>.</p>
        </PaymentApproved>
      )}
    </div>
  )
}
