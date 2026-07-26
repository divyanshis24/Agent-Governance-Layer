import { useEffect, useState } from 'react'

import { useConsole } from '../App'
import { api, clockTime } from '../api'
import { Empty, Panel } from '../components/ui'

export default function Settings() {
  const { stats, refresh } = useConsole()
  const [sim, setSim] = useState(null)
  const [rate, setRate] = useState(1.5)
  const [events, setEvents] = useState([])
  const [health, setHealth] = useState(null)

  const loadSim = () => api.simulator().then(setSim).catch(() => {})

  useEffect(() => {
    loadSim()
    api.health().then(setHealth).catch(() => {})
    api.operatorEvents(20).then(setEvents).catch(() => {})
    const timer = setInterval(() => {
      loadSim()
      api.operatorEvents(20).then(setEvents).catch(() => {})
    }, 3000)
    return () => clearInterval(timer)
  }, [])

  const toggleSim = async () => {
    if (sim?.running) await api.simStop()
    else await api.simStart(rate)
    await loadSim()
    refresh()
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-start">
      <Panel title="Stub agent fleet" subtitle="Generates traffic so the console has decisions to govern">
        <div className="p-5 space-y-4">
          <div className="flex items-center gap-3">
            <span
              className={`h-2 w-2 rounded-full ${sim?.running ? 'bg-ok animate-pulsedot' : 'bg-ink-dim'}`}
            />
            <span className="text-sm">
              {sim?.running ? `Running at ${sim.rate_per_sec}/s` : 'Stopped'}
              {sim?.decisions ? (
                <span className="text-ink-dim"> · {sim.decisions.toLocaleString()} attempts this session</span>
              ) : null}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <label className="text-xs text-ink-muted">Rate</label>
            <input
              type="range"
              min="0.5"
              max="12"
              step="0.5"
              value={rate}
              onChange={(e) => setRate(parseFloat(e.target.value))}
              className="flex-1 accent-[#2f8cf5]"
            />
            <span className="text-xs tabular-nums w-16">{rate}/sec</span>
          </div>
          <div className="flex gap-2">
            <button className={`btn text-xs ${sim?.running ? 'btn-danger' : 'btn-primary'}`} onClick={toggleSim}>
              {sim?.running ? 'Stop fleet' : 'Start fleet'}
            </button>
            <button
              className="btn text-xs"
              onClick={async () => {
                await api.simBurst(40)
                refresh()
              }}
            >
              Fire 40 actions
            </button>
            <button
              className="btn text-xs"
              title="Clears today's spend and rate counters. The audit log is append-only and is not touched."
              onClick={async () => {
                await api.resetCounters()
                refresh()
              }}
            >
              Reset today's counters
            </button>
          </div>
          <p className="text-[11px] text-ink-dim">
            The stubs attempt routine work plus a steady minority of over-cap, out-of-scope, injected and high-value
            actions — so every decision type appears in normal operation.
          </p>
        </div>
      </Panel>

      <Panel title="Control plane" subtitle="What is actually running behind this console">
        <dl className="p-5 space-y-2.5 text-sm">
          <Row label="Policy engine" value={stats?.policy_engine} />
          <Row label="Hot-path state" value={stats?.state_backend} />
          <Row label="Durable store" value={stats?.db_backend} />
          <Row label="Audit chain height" value={stats?.audit_height?.toLocaleString()} />
          <Row label="Chain head" value={stats?.audit_head?.slice(0, 20) + '…'} mono />
          <Row label="Fleet halted" value={stats?.fleet_halted ? 'yes' : 'no'} />
          <Row label="Uptime" value={`${stats?.uptime_s ?? 0}s`} />
          <Row label="Policy engine healthy" value={health?.policy_engine?.healthy ? 'yes' : '—'} />
        </dl>
        <p className="px-5 pb-5 text-[11px] text-ink-dim">
          Redis and PostgreSQL are picked up automatically when REDIS_URL / DATABASE_URL are set; otherwise the same
          interfaces run in-process on SQLite.
        </p>
      </Panel>

      <Panel title="Operator actions" subtitle="Who pulled which lever, and when" className="xl:col-span-2">
        <ul className="divide-y divide-navy-800/60 max-h-[320px] overflow-y-auto">
          {events.map((event) => (
            <li key={event.id} className="px-5 py-3 flex items-center gap-4 text-sm">
              <span className="pill bg-navy-750 text-ink-muted">{event.kind}</span>
              <span className="text-ink-muted flex-1 truncate">
                {event.actor}
                {event.target ? ` · ${event.target}` : ''} — {event.detail}
              </span>
              <span className="text-[11px] text-ink-dim tabular-nums">{clockTime(event.ts)}</span>
            </li>
          ))}
          {events.length === 0 && <Empty>No operator actions recorded yet.</Empty>}
        </ul>
      </Panel>
    </div>
  )
}

function Row({ label, value, mono }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-ink-muted">{label}</dt>
      <dd className={`text-ink ${mono ? 'font-mono text-[12px]' : ''}`}>{value ?? '—'}</dd>
    </div>
  )
}
