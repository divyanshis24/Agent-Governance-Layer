import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'

import { api, useLiveStream } from './api'
import Agents from './pages/Agents'
import AuditLog from './pages/AuditLog'
import Dashboard from './pages/Dashboard'
import Policies from './pages/Policies'
import Settings from './pages/Settings'
import SpendCaps from './pages/SpendCaps'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/agents', label: 'Agents' },
  { to: '/policies', label: 'Policies' },
  { to: '/spend-caps', label: 'Spend Caps' },
  { to: '/audit', label: 'Audit Log' },
  { to: '/settings', label: 'Settings' },
]

const ConsoleContext = createContext(null)
export const useConsole = () => useContext(ConsoleContext)

export default function App() {
  const [stats, setStats] = useState(null)
  const [agents, setAgents] = useState([])
  const [approvals, setApprovals] = useState([])
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    const [s, a, p] = await Promise.all([
      api.stats().catch(() => null),
      api.agents().catch(() => []),
      api.approvals('pending').catch(() => []),
    ])
    if (s) setStats(s)
    setAgents(a)
    setApprovals(p)
  }, [])

  // The stream drives the feed; a light poll keeps counters and caps honest.
  const { connected, events } = useLiveStream({
    onEvent: (event) => {
      if (event.type !== 'decision') refresh()
    },
  })

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 2500)
    return () => clearInterval(timer)
  }, [refresh])

  const halted = stats?.fleet_halted ?? false

  const toggleHalt = async () => {
    setBusy(true)
    try {
      if (halted) await api.resume()
      else await api.halt('operator emergency stop')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const value = { stats, agents, approvals, refresh, events, connected, halted }

  return (
    <ConsoleContext.Provider value={value}>
      <div className="flex h-full">
        <Sidebar approvals={approvals.length} />
        <div className="flex-1 flex flex-col min-w-0">
          <TopBar halted={halted} busy={busy} onToggleHalt={toggleHalt} connected={connected} />
          <main className="flex-1 min-h-0 overflow-auto p-6">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/agents" element={<Agents />} />
              <Route path="/policies" element={<Policies />} />
              <Route path="/spend-caps" element={<SpendCaps />} />
              <Route path="/audit" element={<AuditLog />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </main>
        </div>
      </div>
    </ConsoleContext.Provider>
  )
}

function Sidebar({ approvals }) {
  return (
    <aside className="w-[196px] shrink-0 bg-navy-900 border-r border-navy-800 flex flex-col">
      <div className="px-5 py-5">
        <div className="text-xl font-bold tracking-tight">AEGIS</div>
        <div className="label mt-0.5">Control Plane</div>
      </div>
      <nav className="px-3 space-y-1 flex-1">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                isActive ? 'bg-accent-soft/50 text-ink font-medium' : 'text-ink-muted hover:bg-navy-850 hover:text-ink'
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className={`h-3.5 w-3.5 rounded border ${
                    isActive ? 'border-accent bg-accent/30' : 'border-navy-700'
                  }`}
                />
                <span className="flex-1">{item.label}</span>
                {item.label === 'Dashboard' && approvals > 0 && (
                  <span className="pill bg-warn/15 text-warn">{approvals}</span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="px-5 py-4 text-[11px] text-ink-dim">v0.1 · demo build</div>
    </aside>
  )
}

function TopBar({ halted, busy, onToggleHalt, connected }) {
  const { pathname } = useLocation()
  const title = NAV.find((n) => (n.end ? n.to === pathname : pathname.startsWith(n.to)))?.label ?? 'Dashboard'

  return (
    <header className="h-[60px] shrink-0 border-b border-navy-800 bg-navy-900/60 px-6 flex items-center justify-between">
      <h1 className="text-lg font-semibold">{title}</h1>
      <div className="flex items-center gap-4">
        <span className="flex items-center gap-1.5 text-[11px] text-ink-dim" title="live decision stream">
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? 'bg-ok animate-pulsedot' : 'bg-ink-dim'}`} />
          {connected ? 'live' : 'reconnecting'}
        </span>
        <button
          onClick={onToggleHalt}
          disabled={busy}
          className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-xs font-bold uppercase tracking-wider
            transition-colors disabled:opacity-50 ${
              halted ? 'bg-ok/90 hover:bg-ok text-navy-950' : 'bg-bad/90 hover:bg-bad text-white'
            }`}
        >
          <span className={`h-2.5 w-2.5 ${halted ? 'rounded-full' : 'rounded-[2px]'} bg-white/90`} />
          {halted ? 'Resume Fleet' : 'Emergency Stop'}
        </button>
        <div
          className="h-8 w-8 rounded-full bg-navy-750 border border-navy-700 grid place-items-center text-[11px] font-semibold"
          title="Risk Operator"
        >
          RO
        </div>
      </div>
    </header>
  )
}
