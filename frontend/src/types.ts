export type AgentName = 'boss' | 'inventory' | 'accounting' | 'facilities' | 'customer_service'

export interface Ticket {
  id: number
  type: string
  requester: string
  subject: string
  state: 'open' | 'resolved'
  status: 'open' | 'in_progress' | 'waiting' | 'flagged' | 'complete'
  priority: 'urgent' | 'normal' | null
  assigned_agent: string | null
  resolution: string | null
}

export interface TimelineItem {
  ts: string
  ticket_id: number | null
  agent: string
  event: string
  summary: string
}

export interface AgentActivity {
  steps: number
  tools_used: Record<string, number>
  tickets: number[]
  last_active: string | null
  recent_activity: string[]
}

export interface EventSummary {
  entries_considered: number
  agents: Record<string, AgentActivity>
  timeline: TimelineItem[]
}

export interface Approval {
  id: number
  ticket_id: number
  agent: string
  action: string
  details: string | null
  economic_impact: number
  status: string
}

export interface Flag {
  id: number
  ticket_id: number | null
  kind: string
  message: string
  status: string
  created_at: string
}

export interface CashAccount {
  name: string
  balance: number
  date: string
}

export interface InventoryItem {
  sku: string
  name: string
  location: string
  list_price: number
  stock_by_size: Record<string, number>
  total_in_stock: number
}

export interface Invoice {
  id: number
  vendor_name: string
  amount: number
  due_date: string
  status: string
  description: string | null
  days_overdue: number
}

export interface Payment {
  id: number
  kind: string
  ref_id: number | null
  amount: number
  account: string
  paid_at: string
  approved_by: string
}

export interface Lease {
  id: number
  space_name: string
  landlord: string
  monthly_rent: number
  next_due: string
  notes: string | null
  days_until_due: number
}

export interface Memo {
  id: number
  ticket_id: number | null
  recipient: string
  body: string
  status: string
  created_at: string
}

export interface DatabaseState {
  desk: { date_today: string; notes: string | null }
  cash_accounts: CashAccount[]
  inventory: InventoryItem[]
  invoices: Invoice[]
  payments: Payment[]
  leases: Lease[]
  memos: Memo[]
}

export interface PaymentResult {
  approval_id: number
  ticket_id: number
  paid: string
  amount: number
  checking: CashAccount
}
