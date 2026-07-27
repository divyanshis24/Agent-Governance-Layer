import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useConsole } from '../App'
import { api, clockTime, money, relativeTime } from '../api'
import { DecisionPill, Empty, KpiTile, Panel, SpendBar, StatusPill } from '../components/ui'

export default function Dashboard() {
  const { stats, agents, approvals, events, refresh, halted } = useConsole()

  const spendPct = stats?.fleet_cap_cents ? (stats.spend_today_cents / stats.fleet_cap_cents) * 100 : 0

  return (
    <div className="space-y-5">
      {halted && <HaltBanner stats={stats} />}

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-5">
        <KpiTile
          label="Agents active"
          value={halted ? `0 / ${stats?.agents_total ?? 0}` : (stats?.agents_active ?? 0)}
          valueClass={halted ? 'text-bad' : 'text-accent'}
        />
        <KpiTile label="Actions today" value={(stats?.actions_today ?? 0).toLocaleString()} />
        <KpiTile label="Blocked today" value={stats?.blocked_today ?? 0} valueClass="text-bad" />
        <KpiTile
          label="Spend used"
          value={money(stats?.spend_today_cents ?? 0, { compact: true })}
          valueClass="text-warn"
          sub={`/ ${money(stats?.fleet_cap_cents ?? 0, { compact: true })}`}
          progress={spendPct}
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] gap-5 items-start">
        <FleetTable agents={agents} />
        <LiveActivity events={events} halted={halted} />
      </div>

      {approvals.length > 0 && <ApprovalQueue approvals={approvals} onDone={refresh} />}
    </div>
  )
}

function HaltBanner({ stats }) {
  return (
    <div className="rounded-xl border border-bad/50 bg-bad/10 px-5 py-4 flex items-start gap-3 animate-slidein">
      <span className="mt-0.5 h-5 w-1.5 rounded-full bg-bad shrink-0" />
      <div>
        <p className="font-bold text-bad tracking-wide">FLEET EMERGENCY STOP ACTIVE</p>
        <p className="text-sm text-ink-muted mt-0.5">
          All {stats?.agents_total ?? 0} agents halted
          {stats?.halted_by ? ` by ${stats.halted_by}` : ''}
          {stats?.halted_at ? ` · ${clockTime(stats.halted_at)}` : ''} · no action can execute until resumed
        </p>
      </div>
    </div>
  )
}

function FleetTable({ agents }) {
  return (
    <Panel title="Agent fleet" bodyClass="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left label border-b border-navy-750">
            <th className="px-5 py-2.5 font-semibold">Agent</th>
            <th className="px-3 py-2.5 font-semibold">Status</th>
            <th className="px-3 py-2.5 font-semibold">Spend of daily cap</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <tr key={agent.id} className="border-b border-navy-800/60 last:border-0 row-hover">
              <td className="px-5 py-3.5">
                <Link to="/agents" className="font-medium hover:text-accent transition-colors">
                  {agent.name}
                </Link>
                <div className="text-xs text-ink-dim mt-0.5">{agent.description}</div>
              </td>
              <td className="px-3 py-3.5">
                <StatusPill status={agent.effective_status} />
              </td>
              <td className="px-3 py-3.5">
                {agent.effective_status === 'halted' ? (
                  <span className="text-sm text-ink-dim">suspended</span>
                ) : agent.effective_status === 'revoked' ? (
                  <span className="text-sm text-ink-dim">—</span>
                ) : (
                  <SpendBar spent={agent.spend_today_cents} cap={agent.daily_cap_cents} />
                )}
              </td>
            </tr>
          ))}
          {agents.length === 0 && (
            <tr>
              <td colSpan={3}>
                <Empty>No agents registered.</Empty>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Panel>
  )
}

function LiveActivity({ events, halted }) {
  return (
    <Panel
      title="Live activity"
      right={
        <span className="flex items-center gap-1.5 text-[11px]">
          {halted ? (
            <span className="text-bad font-medium">— frozen —</span>
          ) : (
            <>
              <span className="h-1.5 w-1.5 rounded-full bg-ok animate-pulsedot" />
              <span className="text-ink-dim">live</span>
            </>
          )}
        </span>
      }
      bodyClass="overflow-y-auto max-h-[520px]"
    >
      <ul className="divide-y divide-navy-800/60">
        {events.map((event) => (
          <li key={`${event.seq}-${event.request_id}`} className="px-5 py-3 flex items-start gap-3 animate-slidein">
            <DecisionPill decision={event.decision} />
            <div className="min-w-0 flex-1">
              <p className="text-sm truncate">
                <span className="text-ink-muted">{event.agent_name}</span>
                <span className="text-ink-dim mx-1.5">·</span>
                <span className="font-medium">{event.action.replace(/_/g, ' ')}</span>
                {event.amount_cents > 0 && (
                  <span className="text-ink-muted"> · {money(event.amount_cents)}</span>
                )}
              </p>
              <p className="text-xs text-ink-dim mt-0.5 truncate">{event.reason}</p>
            </div>
            <span className="text-[11px] text-ink-dim tabular-nums shrink-0">{clockTime(event.ts)}</span>
          </li>
        ))}
        {events.length === 0 && (
          <Empty>
            Waiting for agent traffic. Start the stub fleet from <Link className="text-accent" to="/settings">Settings</Link>.
          </Empty>
        )}
      </ul>
    </Panel>
  )
}

function ApprovalQueue({ approvals, onDone }) {
  const [busy, setBusy] = useState(null)

  const decide = async (id, approve) => {
    setBusy(id)
    try {
      await (approve ? api.approve(id, 'reviewed in console') : api.reject(id, 'rejected in console'))
      await onDone()
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel
      title="Awaiting human approval"
      subtitle="High-value or irreversible actions held for a second pair of eyes"
      right={<span className="pill bg-warn/15 text-warn">{approvals.length} pending</span>}
    >
      <ul className="divide-y divide-navy-800/60">
        {approvals.map((approval) => (
          <li key={approval.id} className="px-5 py-3.5 flex items-center gap-4">
            <div className="min-w-0 flex-1">
              <p className="text-sm">
                <span className="font-medium">{approval.agent_name}</span>
                <span className="text-ink-dim mx-1.5">·</span>
                <span className="font-mono text-[13px]">{approval.action}</span>
                {approval.amount_cents > 0 && (
                  <span className="text-warn font-medium"> · {money(approval.amount_cents)}</span>
                )}
              </p>
              <p className="text-xs text-ink-dim mt-0.5">
                {approval.reason} · {relativeTime(approval.created_at)}
              </p>
            </div>
            <div className="flex gap-2 shrink-0">
              <button className="btn text-xs" disabled={busy === approval.id} onClick={() => decide(approval.id, false)}>
                Reject
              </button>
              <button
                className="btn btn-primary text-xs"
                disabled={busy === approval.id}
                onClick={() => decide(approval.id, true)}
              >
                Approve
              </button>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  )
}
