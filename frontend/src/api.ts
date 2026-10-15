import type {
  Approval, CashAccount, DatabaseState, EventSummary, Flag, PaymentResult, Ticket,
} from './types'

export const API_BASE = 'http://localhost:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      // keep the status text
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return response.json() as Promise<T>
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  tickets: () => request<Ticket[]>('/tickets'),
  ticket: (id: number) => request<Ticket>(`/tickets/${id}`),
  runTicket: (id: number) => post<{ ticket: Ticket }>(`/tickets/${id}/run`),
  runAll: () => post<{ processed: number; tickets: Ticket[] }>('/run'),
  events: (limit = 300) => request<EventSummary>(`/events?limit=${limit}`),
  pendingPayments: () => request<Approval[]>('/payments/pending'),
  approvePayment: (id: number) => post<PaymentResult>(`/payments/${id}/approve`, { approver: 'dashboard user' }),
  rejectPayment: (id: number) => post<{ ticket_id: number }>(`/payments/${id}/reject`, { approver: 'dashboard user' }),
  cash: () => request<CashAccount>('/cash'),
  database: () => request<DatabaseState>('/database'),
  flags: () => request<Flag[]>('/flags'),
  resolveFlag: (id: number) => post<{ ticket_reopened: boolean }>(`/flags/${id}/resolve`),
  reset: () => post<{ reset: boolean }>('/reset'),
}
