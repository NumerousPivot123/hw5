export const money = (value: number) =>
  value.toLocaleString('en-US', { style: 'currency', currency: 'USD' })

export const time = (iso: string) =>
  new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' })

export const agentName = (agent: string) =>
  ({
    boss: 'Boss',
    inventory: 'Inventory',
    accounting: 'Accounting',
    facilities: 'Facilities',
    customer_service: 'Customer Service',
    runner: 'System',
    human: 'You',
  })[agent] ?? agent
