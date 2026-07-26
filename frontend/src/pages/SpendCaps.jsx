import { useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { useConsole } from '../App'
import { api, money } from '../api'
import { KpiTile, Panel } from '../components/ui'

const DECISION_COLOURS = {
  allow: '#2ee6a8',
  deny: '#f2545b',
  block: '#e0484f',
  quarantine: '#f2a93b',
  escalate: '#f0c05a',
}

export default function SpendCaps() {
  const { stats, agents, refresh } = useConsole()
  const [policies, setPolicies] = useState({})
  const [capDraft, setCapDraft] = useState('')

  useEffect(() => {
    api.policies().then(setPolicies).catch(() => {})
  }, [agents.length])

  useEffect(() => {
    if (stats?.fleet_cap_cents !== undefined) setCapDraft((stats.fleet_cap_cents / 100).toFixed(0))
  }, [stats?.fleet_cap_cents])

  const spendData = agents
    .filter((a) => a.daily_cap_cents > 0)
    .map((a) => ({
      name: a.name.replace(/ (Agent|Engine|Bot|Concierge|Resolver)$/, ''),
      spent: a.spend_today_cents / 100,
      headroom: Math.max(a.daily_cap_cents - a.spend_today_cents, 0) / 100,
      cap: a.daily_cap_cents / 100,
    }))

  const decisions = Object.entries(stats?.decisions_today ?? {})
    .filter(([, v]) => v > 0)
    .map(([name, value]) => ({ name, value }))

  const saveFleetCap = async () => {
    const cents = Math.max(0, Math.round(parseFloat(capDraft.replace(/[$,]/g, '') || 0) * 100))
    await api.setFleetCap(cents)
    refresh()
  }

  // `value` is already in the field's own unit — cents for caps, a count for rates.
  const patchSpend = async (agentId, field, value) => {
    await api.patchPolicy(agentId, { spend: { [field]: Math.max(0, Math.round(value)) } })
    setPolicies(await api.policies())
    refresh()
  }

  const fleetPct = stats?.fleet_cap_cents ? (stats.spend_today_cents / stats.fleet_cap_cents) * 100 : 0

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-5">
        <KpiTile
          label="Fleet spend today"
          value={money(stats?.spend_today_cents ?? 0, { compact: true })}
          valueClass="text-warn"
          sub={`/ ${money(stats?.fleet_cap_cents ?? 0, { compact: true })}`}
          progress={fleetPct}
        />
        <KpiTile label="Blocked on caps today" value={stats?.decisions_today?.block ?? 0} valueClass="text-bad" />
        <KpiTile label="Awaiting approval" value={stats?.pending_approvals ?? 0} valueClass="text-warn" />
        <div className="panel px-5 py-4">
          <p className="label">Fleet-wide daily cap</p>
          <div className="flex items-center gap-2 mt-2">
            <span className="text-ink-dim">$</span>
            <input
              className="field py-1.5 tabular-nums"
              value={capDraft}
              onChange={(e) => setCapDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && saveFleetCap()}
            />
            <button className="btn btn-primary text-xs" onClick={saveFleetCap}>
              Save
            </button>
          </div>
          <p className="text-[11px] text-ink-dim mt-2">Bounds the whole fleet, whatever per-agent caps allow.</p>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-5 items-start">
        <Panel title="Spend against daily cap" subtitle="Live, per agent">
          <div className="p-5 h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={spendData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                <XAxis dataKey="name" tick={{ fill: '#8fa8c6', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis
                  tick={{ fill: '#5d7798', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${v >= 1000 ? `${v / 1000}k` : v}`}
                />
                <Tooltip
                  cursor={{ fill: '#13304f55' }}
                  contentStyle={{
                    background: '#0c2039',
                    border: '1px solid #17395c',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(v, n) => [`$${v.toLocaleString()}`, n === 'spent' ? 'Spent' : 'Headroom']}
                />
                <Legend wrapperStyle={{ fontSize: 11, color: '#8fa8c6' }} />
                <Bar dataKey="spent" stackId="a" fill="#2f8cf5" radius={[0, 0, 3, 3]} />
                <Bar dataKey="headroom" stackId="a" fill="#17395c" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="Decisions today" subtitle="Every outcome, from the audit log">
          <div className="p-5 h-[300px]">
            {decisions.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={decisions} dataKey="value" nameKey="name" innerRadius={58} outerRadius={92} paddingAngle={2}>
                    {decisions.map((entry) => (
                      <Cell key={entry.name} fill={DECISION_COLOURS[entry.name] ?? '#17395c'} stroke="none" />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      background: '#0c2039',
                      border: '1px solid #17395c',
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-center text-sm text-ink-dim pt-16">No decisions recorded today yet.</p>
            )}
          </div>
        </Panel>
      </div>

      <Panel title="Per-agent caps" subtitle="Click any value to edit — it applies to the next decision">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left label border-b border-navy-750">
                <th className="px-5 py-2.5 font-semibold">Agent</th>
                <th className="px-3 py-2.5 font-semibold text-right">Per-transaction</th>
                <th className="px-3 py-2.5 font-semibold text-right">Daily cap</th>
                <th className="px-3 py-2.5 font-semibold text-right">Spent today</th>
                <th className="px-3 py-2.5 font-semibold text-right">Actions / min</th>
                <th className="px-3 py-2.5 font-semibold text-right">Payments / min</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => {
                const policy = policies[agent.id]
                if (!policy) return null
                const pct = agent.daily_cap_cents
                  ? (agent.spend_today_cents / agent.daily_cap_cents) * 100
                  : 0
                return (
                  <tr key={agent.id} className="border-b border-navy-800/60 last:border-0 row-hover">
                    <td className="px-5 py-3">
                      <div className="font-medium">{agent.name}</div>
                      <div className="text-[11px] text-ink-dim">{agent.description}</div>
                    </td>
                    <EditCell
                      value={policy.spend.per_txn_cap_cents / 100}
                      display={money(policy.spend.per_txn_cap_cents)}
                      onCommit={(dollars) => patchSpend(agent.id, 'per_txn_cap_cents', dollars * 100)}
                    />
                    <EditCell
                      value={policy.spend.daily_cap_cents / 100}
                      display={money(policy.spend.daily_cap_cents)}
                      onCommit={(dollars) => patchSpend(agent.id, 'daily_cap_cents', dollars * 100)}
                    />
                    <td className="px-3 py-3 text-right tabular-nums">
                      <span className={pct > 85 ? 'text-bad' : pct > 60 ? 'text-warn' : 'text-ink-muted'}>
                        {money(agent.spend_today_cents)}
                      </span>
                      <span className="text-[11px] text-ink-dim ml-1.5">{pct ? `${Math.round(pct)}%` : ''}</span>
                    </td>
                    <EditCell
                      value={policy.spend.rate_limit_per_min}
                      display={policy.spend.rate_limit_per_min}
                      onCommit={(v) => patchSpend(agent.id, 'rate_limit_per_min', v)}
                    />
                    <EditCell
                      value={policy.spend.payment_rate_per_min}
                      display={policy.spend.payment_rate_per_min}
                      onCommit={(v) => patchSpend(agent.id, 'payment_rate_per_min', v)}
                    />
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

/** Inline editor. `onCommit` receives the number as typed, in the cell's own unit. */
function EditCell({ value, display, onCommit }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(String(value))

  useEffect(() => setDraft(String(value)), [value])

  const commit = () => {
    setEditing(false)
    const parsed = parseFloat(draft.replace(/[$,]/g, ''))
    if (!Number.isNaN(parsed) && parsed !== value) onCommit(parsed)
  }

  return (
    <td className="px-3 py-3 text-right tabular-nums">
      {editing ? (
        <input
          autoFocus
          className="field py-1 px-2 text-right w-24 text-[13px]"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') setEditing(false)
          }}
        />
      ) : (
        <button className="hover:underline decoration-dotted" onClick={() => setEditing(true)}>
          {display}
        </button>
      )}
    </td>
  )
}
