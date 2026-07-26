import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { useConsole } from '../App'
import { api, clockTime, money, relativeTime } from '../api'
import { DecisionPill, Empty, Panel, SpendBar, StatusPill } from '../components/ui'

export default function Agents() {
  const { agents, refresh } = useConsole()
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [recent, setRecent] = useState([])
  const [busy, setBusy] = useState(false)

  const current = selectedId ?? agents[0]?.id

  useEffect(() => {
    if (!current) return
    let cancelled = false
    const load = async () => {
      const [d, a] = await Promise.all([
        api.agent(current).catch(() => null),
        api.audit({ agent_id: current, limit: 12 }).catch(() => []),
      ])
      if (!cancelled) {
        setDetail(d)
        setRecent(a)
      }
    }
    load()
    const timer = setInterval(load, 3000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [current])

  const act = async (fn) => {
    setBusy(true)
    try {
      await fn()
      await refresh()
      setDetail(await api.agent(current))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[280px_minmax(0,1fr)] gap-5 items-start">
      <Panel title="Agents" subtitle={`${agents.length} registered`}>
        <ul className="p-2">
          {agents.map((agent) => (
            <li key={agent.id}>
              <button
                onClick={() => setSelectedId(agent.id)}
                className={`w-full text-left rounded-lg px-3 py-2.5 transition-colors ${
                  agent.id === current ? 'bg-accent-soft/40 border border-accent/40' : 'hover:bg-navy-800 border border-transparent'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                      agent.effective_status === 'active'
                        ? 'bg-ok'
                        : agent.effective_status === 'throttled'
                          ? 'bg-warn'
                          : 'bg-bad'
                    }`}
                  />
                  <span className="text-sm font-medium truncate">{agent.name}</span>
                </div>
                <p className="text-[11px] text-ink-dim mt-0.5 ml-3.5 truncate">{agent.description}</p>
              </button>
            </li>
          ))}
        </ul>
      </Panel>

      {detail ? (
        <div className="space-y-5">
          <AgentHeader detail={detail} busy={busy} act={act} />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 items-start">
            <PolicySummary detail={detail} />
            <RecentDecisions entries={recent} />
          </div>
        </div>
      ) : (
        <Panel>
          <Empty>Select an agent.</Empty>
        </Panel>
      )}
    </div>
  )
}

function AgentHeader({ detail, busy, act }) {
  const { agent, counters } = detail
  const revoked = agent.status === 'revoked'

  return (
    <Panel
      title={agent.name}
      subtitle={`${agent.description} · owned by ${agent.owner} · id ${agent.id}`}
      right={
        <div className="flex items-center gap-3">
          <StatusPill status={agent.effective_status} />
          <button
            className={`btn text-xs ${revoked ? 'btn-primary' : 'btn-danger'}`}
            disabled={busy}
            onClick={() =>
              act(() =>
                revoked
                  ? api.reinstate(agent.id)
                  : api.revoke(agent.id, 'revoked from operator console')
              )
            }
          >
            {revoked ? 'Reinstate agent' : 'Revoke agent'}
          </button>
        </div>
      }
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-navy-750">
        <Metric label="Spend today" value={money(counters.spend_today_cents, { compact: true })} />
        <Metric label="Actions today" value={counters.actions_today} />
        <Metric label="Blocked today" value={counters.blocked_today} valueClass={counters.blocked_today ? 'text-bad' : ''} />
        <Metric label="Last action" value={relativeTime(counters.last_action_at)} />
      </div>
      {revoked && agent.revoke_reason && (
        <p className="px-5 py-3 text-xs text-bad bg-bad/5">
          Revoked by {agent.revoked_by} — {agent.revoke_reason}
        </p>
      )}
    </Panel>
  )
}

function Metric({ label, value, valueClass = '' }) {
  return (
    <div className="bg-navy-850 px-5 py-4">
      <p className="label">{label}</p>
      <p className={`text-xl font-semibold mt-1 ${valueClass}`}>{value}</p>
    </div>
  )
}

function PolicySummary({ detail }) {
  const { policy, agent } = detail
  if (!policy) return null
  const actions = Object.entries(policy.allowed_actions)

  return (
    <Panel
      title="Bound policy"
      subtitle={`version ${policy.version} · updated by ${policy.updated_by}`}
      right={
        <Link to="/policies" className="btn text-xs">
          Edit guardrails
        </Link>
      }
    >
      <div className="p-5 space-y-4">
        <div>
          <p className="label mb-2">Permitted actions</p>
          <div className="flex flex-wrap gap-1.5">
            {actions.map(([action, mode]) => (
              <span
                key={action}
                className={`pill font-mono normal-case tracking-normal ${
                  mode === 'deny'
                    ? 'bg-bad/15 text-bad line-through'
                    : mode === 'approval'
                      ? 'bg-warn/15 text-warn'
                      : 'bg-navy-750 text-ink-muted'
                }`}
              >
                {action}
                {mode === 'approval' && ' ⚑'}
              </span>
            ))}
          </div>
        </div>
        <div>
          <p className="label mb-2">Data scopes</p>
          <div className="flex flex-wrap gap-1.5">
            {policy.data_scopes.map((scope) => (
              <span key={scope} className="pill bg-navy-750 text-ink-muted font-mono normal-case tracking-normal">
                {scope}
              </span>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-y-2 text-sm">
          <Detail label="Per-transaction cap" value={money(policy.spend.per_txn_cap_cents)} />
          <Detail label="Daily cap" value={money(policy.spend.daily_cap_cents)} />
          <Detail label="Rate limit" value={`${policy.spend.rate_limit_per_min} actions/min`} />
          <Detail label="Payment rate" value={`${policy.spend.payment_rate_per_min} payments/min`} />
          <Detail label="Human approval above" value={money(policy.hitl.approval_above_cents)} />
          <Detail label="Escalation contact" value={policy.hitl.escalation_contact} />
        </div>
        <p className="text-[11px] text-ink-dim pt-1">
          {agent.name} cannot edit this policy — authorship is an operator capability.
        </p>
      </div>
    </Panel>
  )
}

function Detail({ label, value }) {
  return (
    <div>
      <p className="text-[11px] text-ink-dim">{label}</p>
      <p className="text-ink-muted">{value}</p>
    </div>
  )
}

function RecentDecisions({ entries }) {
  return (
    <Panel title="Recent decisions" subtitle="From the tamper-evident log">
      <ul className="divide-y divide-navy-800/60 max-h-[420px] overflow-y-auto">
        {entries.map((entry) => (
          <li key={entry.seq} className="px-5 py-3 flex items-center gap-3">
            <DecisionPill decision={entry.decision} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-mono truncate">{entry.action}</p>
              <p className="text-xs text-ink-dim truncate">{entry.reason}</p>
            </div>
            <span className="text-[11px] text-ink-dim tabular-nums">{clockTime(entry.ts)}</span>
          </li>
        ))}
        {entries.length === 0 && <Empty>No decisions recorded yet.</Empty>}
      </ul>
    </Panel>
  )
}
