// Shared presentational pieces used across the console screens.
import { money } from '../api'

export const DECISION_STYLE = {
  allow: 'bg-ok/15 text-ok',
  allowed: 'bg-ok/15 text-ok',
  deny: 'bg-bad/20 text-bad',
  block: 'bg-bad/20 text-bad',
  quarantine: 'bg-warn/15 text-warn',
  escalate: 'bg-warn/15 text-warn',
}

const STATUS_STYLE = {
  active: 'bg-ok/15 text-ok',
  throttled: 'bg-warn/15 text-warn',
  revoked: 'bg-bad/20 text-bad',
  halted: 'bg-bad/20 text-bad',
  suspended: 'bg-navy-700 text-ink-muted',
}

export function DecisionPill({ decision }) {
  const label = decision === 'allow' ? 'allowed' : decision
  return <span className={`pill ${DECISION_STYLE[decision] || 'bg-navy-700 text-ink-muted'}`}>{label}</span>
}

export function StatusPill({ status }) {
  return <span className={`pill ${STATUS_STYLE[status] || 'bg-navy-700 text-ink-muted'}`}>{status}</span>
}

export function Panel({ title, subtitle, right, children, className = '', bodyClass = '' }) {
  return (
    <section className={`panel flex flex-col min-h-0 ${className}`}>
      {(title || right) && (
        <header className="panel-header flex items-start justify-between gap-4 shrink-0">
          <div>
            {title && <h2 className="text-[15px] font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="text-xs text-ink-dim mt-0.5">{subtitle}</p>}
          </div>
          {right}
        </header>
      )}
      <div className={`flex-1 min-h-0 ${bodyClass}`}>{children}</div>
    </section>
  )
}

export function KpiTile({ label, value, valueClass = 'text-ink', sub, progress }) {
  return (
    <div className="panel px-5 py-4">
      <p className="label">{label}</p>
      <p className={`text-[28px] leading-tight font-semibold mt-1.5 ${valueClass}`}>
        {value}
        {sub && <span className="text-sm font-normal text-ink-dim ml-1.5">{sub}</span>}
      </p>
      {progress !== undefined && (
        <div className="mt-2.5 h-1.5 rounded-full bg-navy-750 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              progress > 85 ? 'bg-bad' : progress > 60 ? 'bg-warn' : 'bg-ok'
            }`}
            style={{ width: `${Math.min(progress, 100)}%` }}
          />
        </div>
      )}
    </div>
  )
}

export function SpendBar({ spent, cap }) {
  if (!cap) return <span className="text-ink-dim text-sm">—</span>
  const pct = Math.min((spent / cap) * 100, 100)
  return (
    <div className="flex items-center gap-3">
      <div className="h-1.5 w-32 rounded-full bg-navy-750 overflow-hidden shrink-0">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            pct > 85 ? 'bg-bad' : pct > 60 ? 'bg-warn' : 'bg-ok'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-ink-muted tabular-nums whitespace-nowrap">
        {money(spent, { compact: true })} / {money(cap, { compact: true })}
      </span>
    </div>
  )
}

export function Toggle({ checked, onChange, disabled, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange?.(!checked)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-40 ${
        checked ? 'bg-ok' : 'bg-navy-700'
      }`}
    >
      <span
        className={`absolute left-0 top-1/2 h-4 w-4 -translate-y-1/2 rounded-full bg-white shadow-sm
          transition-transform ${checked ? 'translate-x-[18px]' : 'translate-x-0.5'}`}
      />
    </button>
  )
}

export function Modal({ open, title, onClose, children, width = 'max-w-2xl' }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60" onClick={onClose}>
      <div
        className={`panel w-full ${width} max-h-[80vh] overflow-auto animate-slidein`}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="panel-header flex items-center justify-between sticky top-0 bg-navy-850">
          <h3 className="font-semibold">{title}</h3>
          <button className="text-ink-dim hover:text-ink text-xl leading-none" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

export function Empty({ children }) {
  return <div className="p-8 text-center text-sm text-ink-dim">{children}</div>
}
