// Thin client for the Agent Governance Layer control plane, plus the live-stream hook.
import { useEffect, useRef, useState } from 'react'

const OPERATOR = 'Risk Operator'

async function request(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Operator': OPERATOR },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(`${res.status} ${detail}`)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  stats: () => request('/v1/stats'),
  agents: () => request('/v1/agents'),
  agent: (id) => request(`/v1/agents/${id}`),
  revoke: (id, reason) => request(`/v1/agents/${id}/revoke`, { method: 'POST', body: { reason } }),
  reinstate: (id) => request(`/v1/agents/${id}/reinstate`, { method: 'POST' }),

  fleet: () => request('/v1/fleet'),
  halt: (reason) => request('/v1/fleet/halt', { method: 'POST', body: { reason } }),
  resume: () => request('/v1/fleet/resume', { method: 'POST' }),
  setFleetCap: (cents) => request('/v1/fleet/cap', { method: 'PUT', body: { daily_cap_cents: cents } }),

  policies: () => request('/v1/policies'),
  policy: (id) => request(`/v1/policies/${id}`),
  patchPolicy: (id, patch) => request(`/v1/policies/${id}`, { method: 'PATCH', body: patch }),
  policyHistory: (id) => request(`/v1/policies/${id}/history`),

  audit: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== ''))
    return request(`/v1/audit?${q}`)
  },
  verifyChain: () => request('/v1/audit/verify'),
  auditHead: () => request('/v1/audit/head'),
  proof: (seq) => request(`/v1/audit/proof/${seq}`),
  tamper: (seq) => request('/v1/audit/_tamper', { method: 'POST', body: seq ? { seq } : {} }),
  restore: () => request('/v1/audit/_restore', { method: 'POST' }),

  approvals: (status) => request(`/v1/approvals${status ? `?status=${status}` : ''}`),
  approve: (id, note) => request(`/v1/approvals/${id}/approve`, { method: 'POST', body: { note } }),
  reject: (id, note) => request(`/v1/approvals/${id}/reject`, { method: 'POST', body: { note } }),

  activity: (limit = 40) => request(`/v1/activity?limit=${limit}`),
  operatorEvents: (limit = 30) => request(`/v1/operator-events?limit=${limit}`),

  simulator: () => request('/v1/simulator'),
  simStart: (rate) => request('/v1/simulator/start', { method: 'POST', body: { rate_per_sec: rate } }),
  simStop: () => request('/v1/simulator/stop', { method: 'POST' }),
  simBurst: (count) => request('/v1/simulator/burst', { method: 'POST', body: { count } }),
  resetCounters: () => request('/v1/simulator/reset-counters', { method: 'POST' }),

  health: () => request('/health'),
}

/**
 * Subscribes to the control plane's decision stream.
 * Reconnects on drop so the console survives a backend restart.
 */
export function useLiveStream({ onEvent } = {}) {
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState([])
  const handler = useRef(onEvent)
  handler.current = onEvent

  // Hydrate over REST so the feed is populated on first paint, and stays
  // useful if the socket is slow or blocked.
  useEffect(() => {
    api
      .activity(40)
      .then((recent) => setEvents((prev) => (prev.length ? prev : recent)))
      .catch(() => {})
  }, [])

  useEffect(() => {
    let socket
    let retry
    let closed = false

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${proto}://${location.host}/v1/stream`)

      socket.onopen = () => setConnected(true)
      socket.onclose = () => {
        setConnected(false)
        if (!closed) retry = setTimeout(connect, 1500)
      }
      socket.onerror = () => socket.close()
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data)
        if (event.type === 'snapshot') {
          setEvents((event.events || []).filter((e) => e.type === 'decision'))
        } else if (event.type === 'decision') {
          setEvents((prev) => [event, ...prev].slice(0, 60))
        }
        handler.current?.(event)
      }
    }

    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      socket?.close()
    }
  }, [])

  return { connected, events }
}

// --- formatting helpers ----------------------------------------------------
export const money = (cents, { compact = false } = {}) => {
  const dollars = (cents || 0) / 100
  if (compact) {
    if (Math.abs(dollars) >= 1000) return `$${(dollars / 1000).toFixed(dollars % 1000 === 0 ? 0 : 1)}k`
    return `$${dollars.toFixed(dollars % 1 === 0 ? 0 : 2)}`
  }
  return dollars.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
}

export const clockTime = (ts) =>
  new Date((ts || 0) * 1000).toLocaleTimeString('en-GB', { hour12: false })

export const relativeTime = (ts) => {
  if (!ts) return '—'
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}
