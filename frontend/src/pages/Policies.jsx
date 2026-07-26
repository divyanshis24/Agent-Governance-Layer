import { useEffect, useMemo, useRef, useState } from 'react'

import { useConsole } from '../App'
import { api, money } from '../api'
import { Panel, StatusPill, Toggle } from '../components/ui'

const DATA_CONTROLS = [
  ['mask_pan_ssn', 'Mask PAN / SSN'],
  ['payee_allowlist', 'Payee allowlist'],
  ['sanctions_screening', 'Sanctions / AML screening'],
]

const AI_CONTROLS = [
  ['prompt_injection_screening', 'Prompt-injection screening'],
  ['output_validation', 'Output / action validation'],
  ['pii_leak_prevention', 'PII leak prevention'],
]

export default function Policies() {
  const { agents, refresh } = useConsole()
  const [selectedId, setSelectedId] = useState(null)
  const [policy, setPolicy] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const savedTimer = useRef(null)

  const current = selectedId ?? agents[0]?.id
  const agent = agents.find((a) => a.id === current)

  useEffect(() => {
    if (!current) return
    api.policy(current).then(setPolicy).catch(() => setPolicy(null))
  }, [current])

  const controlsActive = useMemo(() => {
    if (!policy) return 0
    const guardrails = Object.entries(policy.guardrails).filter(([k, v]) => k !== 'max_records_per_read' && v).length
    const permitted = Object.values(policy.allowed_actions).filter((m) => m !== 'deny').length
    return guardrails + permitted + 4 // + the four spend & rate limits
  }, [policy])

  const patch = async (body) => {
    setSaving(true)
    try {
      const updated = await api.patchPolicy(current, body)
      setPolicy(updated)
      setSaved(true)
      clearTimeout(savedTimer.current)
      savedTimer.current = setTimeout(() => setSaved(false), 1600)
      refresh()
    } finally {
      setSaving(false)
    }
  }

  const setAction = (action, mode) =>
    patch({ allowed_actions: { ...policy.allowed_actions, [action]: mode } })

  const removeAction = (action) => {
    const next = { ...policy.allowed_actions }
    delete next[action]
    return patch({ allowed_actions: next })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[280px_minmax(0,1fr)] gap-5 items-start">
      <Panel title="Agents">
        <ul className="p-2">
          {agents.map((a) => (
            <li key={a.id}>
              <button
                onClick={() => setSelectedId(a.id)}
                className={`w-full text-left rounded-lg px-3 py-2.5 transition-colors ${
                  a.id === current
                    ? 'bg-accent-soft/40 border border-accent/40'
                    : 'hover:bg-navy-800 border border-transparent'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                      a.effective_status === 'active' ? 'bg-ok' : a.effective_status === 'throttled' ? 'bg-warn' : 'bg-bad'
                    }`}
                  />
                  <span className="text-sm font-medium truncate">{a.name}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </Panel>

      {policy && agent ? (
        <Panel
          title={agent.name}
          subtitle={`Guardrails · deny-by-default · ${controlsActive} controls active · policy v${policy.version}`}
          right={
            <div className="flex items-center gap-3">
              {saving ? (
                <span className="text-[11px] text-ink-dim">saving…</span>
              ) : saved ? (
                <span className="text-[11px] text-ok">saved · live on next decision</span>
              ) : null}
              <StatusPill status={agent.effective_status} />
            </div>
          }
        >
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-7 p-5">
            <section className="space-y-3">
              <h3 className="label text-accent">Allowed actions</h3>
              <ul className="space-y-2.5">
                {Object.entries(policy.allowed_actions).map(([action, mode]) => (
                  <ActionRow
                    key={action}
                    action={action}
                    mode={mode}
                    onSet={(next) => setAction(action, next)}
                    onRemove={() => removeAction(action)}
                  />
                ))}
              </ul>
              <AddAction
                existing={Object.keys(policy.allowed_actions)}
                onAdd={(action) => setAction(action, 'allow')}
              />
            </section>

            <section className="space-y-3">
              <h3 className="label text-accent">Data &amp; counterparty</h3>
              <ul className="space-y-2.5">
                {DATA_CONTROLS.map(([key, label]) => (
                  <ToggleRow
                    key={key}
                    label={label}
                    checked={policy.guardrails[key]}
                    onChange={(v) => patch({ guardrails: { [key]: v } })}
                  />
                ))}
                <NumberRow
                  label="Max records per read"
                  value={policy.guardrails.max_records_per_read}
                  suffix="records"
                  onCommit={(v) => patch({ guardrails: { max_records_per_read: v } })}
                />
              </ul>

              <h3 className="label text-accent pt-4">AI safety &amp; oversight</h3>
              <ul className="space-y-2.5">
                {AI_CONTROLS.map(([key, label]) => (
                  <ToggleRow
                    key={key}
                    label={label}
                    checked={policy.guardrails[key]}
                    onChange={(v) => patch({ guardrails: { [key]: v } })}
                  />
                ))}
              </ul>
            </section>

            <section className="space-y-3">
              <h3 className="label text-accent">Spend &amp; rate limits</h3>
              <ul className="space-y-2.5">
                <MoneyRow
                  label="Per-transaction cap"
                  cents={policy.spend.per_txn_cap_cents}
                  onCommit={(cents) => patch({ spend: { per_txn_cap_cents: cents } })}
                />
                <MoneyRow
                  label="Daily cap"
                  cents={policy.spend.daily_cap_cents}
                  hint={
                    agent.daily_cap_cents
                      ? `${Math.round((agent.spend_today_cents / agent.daily_cap_cents) * 100)}% used`
                      : null
                  }
                  onCommit={(cents) => patch({ spend: { daily_cap_cents: cents } })}
                />
                <NumberRow
                  label="Rate limit"
                  value={policy.spend.rate_limit_per_min}
                  suffix="actions / min"
                  onCommit={(v) => patch({ spend: { rate_limit_per_min: v } })}
                />
                <NumberRow
                  label="Payment rate"
                  value={policy.spend.payment_rate_per_min}
                  suffix="payments / min"
                  onCommit={(v) => patch({ spend: { payment_rate_per_min: v } })}
                />
              </ul>
            </section>

            <section className="space-y-3">
              <h3 className="label text-accent">Human-in-the-loop</h3>
              <ul className="space-y-2.5">
                <MoneyRow
                  label="Require human approval above"
                  cents={policy.hitl.approval_above_cents}
                  valueClass="text-warn"
                  onCommit={(cents) => patch({ hitl: { approval_above_cents: cents } })}
                />
                <TextRow
                  label="Escalation contact"
                  value={policy.hitl.escalation_contact}
                  onCommit={(v) => patch({ hitl: { escalation_contact: v } })}
                />
                <ToggleRow
                  label="Always approve irreversible actions"
                  checked={policy.hitl.approve_irreversible}
                  onChange={(v) => patch({ hitl: { approve_irreversible: v } })}
                />
              </ul>

              <h3 className="label text-accent pt-4">Data scopes</h3>
              <ScopeEditor
                scopes={policy.data_scopes}
                onChange={(scopes) => patch({ data_scopes: scopes })}
              />

              <h3 className="label text-accent pt-4">Approved payees</h3>
              <ScopeEditor
                scopes={policy.payees}
                placeholder="add payee"
                onChange={(payees) => patch({ payees })}
              />
            </section>
          </div>
        </Panel>
      ) : (
        <Panel>
          <p className="p-8 text-center text-sm text-ink-dim">Select an agent to edit its guardrails.</p>
        </Panel>
      )}
    </div>
  )
}

function ActionRow({ action, mode, onSet, onRemove }) {
  const enabled = mode !== 'deny'
  const needsApproval = mode === 'approval'
  const condition = mode?.startsWith('allow:<=') ? Number(mode.split('<=')[1]) : null

  return (
    <li className="flex items-center gap-3 group">
      <span className={`font-mono text-[13px] flex-1 truncate ${enabled ? 'text-ink' : 'text-ink-dim line-through'}`}>
        {action}
        {condition ? <span className="text-ink-dim"> ≤ {money(condition, { compact: true })}</span> : null}
      </span>

      <button
        onClick={() => onSet(needsApproval ? 'allow' : 'approval')}
        title={needsApproval ? 'Remove the approval requirement' : 'Require human approval'}
        className={`pill transition-colors ${
          needsApproval ? 'bg-warn/90 text-navy-950' : 'opacity-0 group-hover:opacity-100 bg-navy-750 text-ink-dim'
        }`}
      >
        approval
      </button>
      <button
        onClick={onRemove}
        title="Remove from the permission set"
        className="opacity-0 group-hover:opacity-100 text-ink-dim hover:text-bad text-sm leading-none"
      >
        ×
      </button>
      <Toggle checked={enabled} label={action} onChange={(v) => onSet(v ? 'allow' : 'deny')} />
    </li>
  )
}

function AddAction({ existing, onAdd }) {
  const [value, setValue] = useState('')
  const submit = (e) => {
    e.preventDefault()
    const action = value.trim().toLowerCase().replace(/\s+/g, '_')
    if (!action || existing.includes(action)) return
    onAdd(action)
    setValue('')
  }
  return (
    <form onSubmit={submit} className="flex gap-2 pt-1">
      <input
        className="field font-mono text-[13px] py-1.5"
        placeholder="grant an action…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <button type="submit" className="btn text-xs">
        Add
      </button>
    </form>
  )
}

function ToggleRow({ label, checked, onChange }) {
  return (
    <li className="flex items-center gap-3">
      <span className="text-[13px] flex-1 text-ink-muted">{label}</span>
      <Toggle checked={!!checked} onChange={onChange} label={label} />
    </li>
  )
}

function InlineEdit({ display, value, onCommit, width = 'w-24', valueClass = 'text-ink' }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(String(value))

  useEffect(() => setDraft(String(value)), [value])

  const commit = () => {
    setEditing(false)
    if (draft !== String(value)) onCommit(draft)
  }

  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        className={`text-[13px] font-semibold tabular-nums hover:underline decoration-dotted ${valueClass}`}
      >
        {display}
      </button>
    )
  }
  return (
    <input
      autoFocus
      className={`field py-1 px-2 text-[13px] text-right tabular-nums ${width}`}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit()
        if (e.key === 'Escape') setEditing(false)
      }}
    />
  )
}

function MoneyRow({ label, cents, hint, onCommit, valueClass }) {
  return (
    <li className="flex items-center gap-3">
      <span className="text-[13px] flex-1 text-ink-muted">{label}</span>
      {hint && <span className="text-[11px] text-ink-dim">{hint}</span>}
      <InlineEdit
        display={cents ? money(cents) : 'no cap'}
        value={(cents / 100).toFixed(2)}
        valueClass={valueClass}
        onCommit={(v) => onCommit(Math.max(0, Math.round(parseFloat(v.replace(/[$,]/g, '') || 0) * 100)))}
      />
    </li>
  )
}

function NumberRow({ label, value, suffix, onCommit }) {
  return (
    <li className="flex items-center gap-3">
      <span className="text-[13px] flex-1 text-ink-muted">{label}</span>
      <InlineEdit
        display={`${value} ${suffix}`}
        value={value}
        width="w-20"
        onCommit={(v) => onCommit(Math.max(0, parseInt(v, 10) || 0))}
      />
    </li>
  )
}

function TextRow({ label, value, onCommit }) {
  return (
    <li className="flex items-center gap-3">
      <span className="text-[13px] flex-1 text-ink-muted">{label}</span>
      <InlineEdit display={value} value={value} width="w-40" valueClass="text-ink-muted" onCommit={onCommit} />
    </li>
  )
}

function ScopeEditor({ scopes, onChange, placeholder = 'add scope' }) {
  const [value, setValue] = useState('')
  const submit = (e) => {
    e.preventDefault()
    const scope = value.trim().toLowerCase()
    if (!scope || scopes.includes(scope)) return
    onChange([...scopes, scope])
    setValue('')
  }
  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {scopes.map((scope) => (
          <span key={scope} className="pill bg-navy-750 text-ink-muted font-mono normal-case tracking-normal gap-1.5">
            {scope}
            <button className="hover:text-bad" onClick={() => onChange(scopes.filter((s) => s !== scope))}>
              ×
            </button>
          </span>
        ))}
        {scopes.length === 0 && <span className="text-[11px] text-ink-dim">none — nothing is readable</span>}
      </div>
      <form onSubmit={submit} className="flex gap-2">
        <input
          className="field font-mono text-[13px] py-1.5"
          placeholder={placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="submit" className="btn text-xs">
          Add
        </button>
      </form>
    </div>
  )
}
