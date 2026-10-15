import type { AgentActivity, AgentName } from '../types'
import { Bulldog } from './Bulldog'

export const AGENTS: { id: AgentName; label: string; role: string }[] = [
  { id: 'boss', label: 'Boss Agent', role: 'Routes tickets, signs off on every change' },
  { id: 'inventory', label: 'Inventory Agent', role: 'Checks stock and fills orders' },
  { id: 'accounting', label: 'Accounting Agent', role: 'Invoices, payments and cash' },
  { id: 'facilities', label: 'Facilities Agent', role: 'Rent and the lease' },
  { id: 'customer_service', label: 'Customer Service Agent', role: 'Overrides and customer memos' },
]

const HELPERS = AGENTS.slice(1)
// Where each helper sits along the bottom row (percent of the width).
const HELPER_X = [12.5, 37.5, 62.5, 87.5]

interface Props {
  active: Set<AgentName>
  running: boolean
  ticketId: number | null
  caption: string
  activity: Record<string, AgentActivity>
}

export function AgentNetwork({ active, running, ticketId, caption, activity }: Props) {
  const bubble = (agent: AgentName) => {
    const recent = activity[agent]?.recent_activity
    return recent && recent.length ? recent[recent.length - 1] : null
  }

  return (
    <section className={`panel network-panel ${running ? 'is-running' : ''}`} aria-label="The agent team">
      <div className="panel-head">
        <h2>Meet the agent team</h2>
        <span className={`run-pill ${running ? 'on' : ''}`}>
          {running ? `Working on ticket #${ticketId ?? '…'}` : 'Resting'}
        </span>
      </div>
      <p className="network-caption" aria-live="polite">{caption}</p>

      <div className="network">
        <svg className="network-lines" viewBox="0 0 1000 540" preserveAspectRatio="none" aria-hidden="true">
          {HELPERS.map((helper, i) => {
            const lit = active.has(helper.id)
            return (
              <line
                key={helper.id}
                x1="500" y1="232" x2={HELPER_X[i] * 10} y2="336"
                className={`network-line ${lit ? 'lit' : ''}`}
              />
            )
          })}
        </svg>

        {AGENTS.map((agent) => {
          const isBoss = agent.id === 'boss'
          const lit = active.has(agent.id)
          const left = isBoss ? 50 : HELPER_X[HELPERS.indexOf(agent)]
          const said = lit ? bubble(agent.id) : null
          return (
            <div
              key={agent.id}
              className={`agent-node ${isBoss ? 'boss' : 'helper'} ${lit ? 'lit' : ''}`}
              style={{ left: `${left}%` }}
            >
              {said && <div className="speech">{said}</div>}
              <div className="dog-frame">
                <span className="halo" />
                <Bulldog agent={agent.id} />
              </div>
              <div className="agent-label">{agent.label}</div>
              <div className="agent-role">{agent.role}</div>
            </div>
          )
        })}
      </div>

      <div className="legend">
        <span><i className="dot lit" /> Lit up = that agent is working right now</span>
        <span><i className="dash" /> Glowing line = the boss and a helper are handing off work</span>
      </div>
    </section>
  )
}
