import type { ReactNode } from 'react'

export function PaymentApproved({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Payment approved" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <svg className="big-check" viewBox="0 0 100 100" aria-hidden="true">
          <circle cx="50" cy="50" r="46" />
          <path d="M28 52 L44 68 L74 36" />
        </svg>
        <h2>Payment approved</h2>
        <div className="modal-body">{children}</div>
        <button className="btn primary" onClick={onClose} autoFocus>Done</button>
      </div>
    </div>
  )
}
