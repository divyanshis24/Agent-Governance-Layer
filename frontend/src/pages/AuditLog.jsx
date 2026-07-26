import { useCallback, useEffect, useState } from 'react'

import { useConsole } from '../App'
import { api, clockTime, money } from '../api'
import { DecisionPill, Empty, Modal, Panel } from '../components/ui'

const DECISIONS = ['', 'allow', 'deny', 'block', 'quarantine', 'escalate']

export default function AuditLog() {
  const { agents } = useConsole()
  const [entries, setEntries] = useState([])
  const [filters, setFilters] = useState({ agent_id: '', decision: '' })
  const [verification, setVerification] = useState(null)
  const [verifying, setVerifying] = useState(false)
  const [proof, setProof] = useState(null)
  const [live, setLive] = useState(true)

  const load = useCallback(async () => {
    setEntries(await api.audit({ ...filters, limit: 100 }).catch(() => []))
  }, [filters])

  useEffect(() => {
    load()
    if (!live) return
    const timer = setInterval(load, 2500)
    return () => clearInterval(timer)
  }, [load, live])

  useEffect(() => {
    api.verifyChain().then(setVerification).catch(() => {})
  }, [])

  const verify = async () => {
    setVerifying(true)
    try {
      setVerification(await api.verifyChain())
    } finally {
      setVerifying(false)
    }
  }

  const runTamper = async () => {
    const result = await api.tamper()
    setVerification(result.verification)
    await load()
  }

  const runRestore = async () => {
    const result = await api.restore()
    setVerification(result.verification)
    await load()
  }

  const ok = verification?.ok

  return (
    <div className="space-y-5">
      <Panel
        title="Audit Log"
        subtitle="append-only · hash-chained · tamper-evident"
        right={
          <div className="flex items-center gap-3">
            {verification && (
              <span className={`pill ${ok ? 'bg-ok/15 text-ok' : 'bg-bad/20 text-bad'}`}>
                {ok ? '✓ Chain verified' : '✗ Chain broken'}
              </span>
            )}
            <button className="btn text-xs" onClick={verify} disabled={verifying}>
              {verifying ? 'Verifying…' : 'Verify integrity'}
            </button>
            <a className="btn text-xs" href="/v1/audit/export" download>
              Export CSV
            </a>
          </div>
        }
      >
        <div className="px-5 py-3 flex flex-wrap items-center gap-3 border-b border-navy-750">
          <select
            className="field w-auto py-1.5 text-xs"
            value={filters.agent_id}
            onChange={(e) => setFilters((f) => ({ ...f, agent_id: e.target.value }))}
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <select
            className="field w-auto py-1.5 text-xs"
            value={filters.decision}
            onChange={(e) => setFilters((f) => ({ ...f, decision: e.target.value }))}
          >
            {DECISIONS.map((d) => (
              <option key={d} value={d}>
                {d ? d[0].toUpperCase() + d.slice(1) : 'All decisions'}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-xs text-ink-muted cursor-pointer">
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
            Follow live
          </label>
          <span className="text-[11px] text-ink-dim ml-auto">
            {verification?.detail}
            {verification && ` · ${verification.duration_ms.toFixed(1)} ms`}
          </span>
        </div>

        {verification && !ok && (
          <div className="px-5 py-3 bg-bad/10 border-b border-bad/30 text-sm text-bad flex items-center gap-3">
            <span className="font-semibold">Integrity failure at entry #{verification.broken_at}.</span>
            <span className="text-ink-muted">{verification.detail}</span>
            <button className="btn text-xs ml-auto" onClick={runRestore}>
              Rebuild chain
            </button>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left label border-b border-navy-750">
                <th className="px-5 py-2.5 font-semibold">Time</th>
                <th className="px-3 py-2.5 font-semibold">Agent</th>
                <th className="px-3 py-2.5 font-semibold">Action</th>
                <th className="px-3 py-2.5 font-semibold">Resource</th>
                <th className="px-3 py-2.5 font-semibold">Decision</th>
                <th className="px-3 py-2.5 font-semibold">Reason</th>
                <th className="px-3 py-2.5 font-semibold text-right">Hash</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr
                  key={entry.seq}
                  className="border-b border-navy-800/60 last:border-0 row-hover cursor-pointer"
                  onClick={() => api.proof(entry.seq).then(setProof)}
                >
                  <td className="px-5 py-3 tabular-nums text-ink-muted whitespace-nowrap">{clockTime(entry.ts)}</td>
                  <td className="px-3 py-3 font-medium whitespace-nowrap">{entry.agent_name || entry.agent_id}</td>
                  <td className="px-3 py-3 font-mono text-[13px] whitespace-nowrap">{entry.action}</td>
                  <td className="px-3 py-3 text-ink-muted whitespace-nowrap">
                    {entry.amount_cents > 0 ? money(entry.amount_cents) : entry.resource || '—'}
                  </td>
                  <td className="px-3 py-3">
                    <DecisionPill decision={entry.decision} />
                  </td>
                  <td className="px-3 py-3 text-ink-muted max-w-[280px] truncate" title={entry.reason}>
                    {entry.reason}
                  </td>
                  <td className="px-3 py-3 text-right font-mono text-[12px] text-accent whitespace-nowrap">
                    {entry.hash.slice(0, 6)}…
                  </td>
                </tr>
              ))}
              {entries.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <Empty>No entries match these filters.</Empty>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <TamperPanel onTamper={runTamper} onRestore={runRestore} verification={verification} />

      <Modal open={!!proof} title={`Audit entry #${proof?.seq}`} onClose={() => setProof(null)}>
        {proof && <ProofView proof={proof} />}
      </Modal>
    </div>
  )
}

function ProofView({ proof }) {
  const entry = proof.entry
  return (
    <div className="space-y-4 text-sm">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Agent" value={entry.agent_id} />
        <Field label="Action" value={entry.action} mono />
        <Field label="Decision" value={entry.decision} />
        <Field label="Reason" value={entry.reason} />
      </div>
      <div>
        <p className="label mb-2">Chain links</p>
        <div className="space-y-1.5">
          {proof.links.map((link) => (
            <div
              key={link.seq}
              className={`rounded-lg border px-3 py-2 font-mono text-[11px] ${
                link.seq === proof.seq ? 'border-accent/50 bg-accent/5' : 'border-navy-750 bg-navy-900'
              }`}
            >
              <div className="flex items-center justify-between text-ink-muted">
                <span>#{link.seq}</span>
                <span>{link.action}</span>
              </div>
              <div className="text-ink-dim mt-1 break-all">prev {link.prev_hash}</div>
              <div className="text-accent break-all">hash {link.hash}</div>
            </div>
          ))}
        </div>
      </div>
      <p className="text-[11px] text-ink-dim">
        Each hash covers the entry's content and the hash before it. Changing entry #{proof.seq} would invalidate every
        hash after it — which is what "Verify integrity" checks.
      </p>
    </div>
  )
}

function Field({ label, value, mono }) {
  return (
    <div>
      <p className="label">{label}</p>
      <p className={`mt-0.5 ${mono ? 'font-mono text-[13px]' : ''}`}>{value}</p>
    </div>
  )
}

function TamperPanel({ onTamper, onRestore, verification }) {
  return (
    <Panel
      title="Prove it"
      subtitle="Tamper evidence is a property you can test, not a claim you have to take on trust"
    >
      <div className="p-5 flex flex-wrap items-center gap-4">
        <p className="text-sm text-ink-muted flex-1 min-w-[280px]">
          The tamper button reaches <em>past</em> the append-only API and rewrites a historical row directly in the
          database — the move an insider or a compromised process would make. The write succeeds. Verification catches
          it anyway, and names the entry.
        </p>
        <div className="flex gap-2">
          <button className="btn btn-danger text-xs" onClick={onTamper}>
            Tamper with a past entry
          </button>
          <button className="btn text-xs" onClick={onRestore} disabled={verification?.ok}>
            Rebuild chain
          </button>
        </div>
      </div>
    </Panel>
  )
}
