import { useCallback, useEffect, useRef, useState } from 'react'

export function useAsync(fn, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const seq = useRef(0)
  const run = useCallback(() => {
    const id = ++seq.current
    setState((s) => ({ ...s, loading: true, error: null }))
    fn()
      .then((data) => id === seq.current && setState({ data, error: null, loading: false }))
      .catch((error) => id === seq.current && setState({ data: null, error, loading: false }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(run, [run])
  return { ...state, reload: run }
}

const STATE_TONE = {
  DRAFT: 'neutral',
  UNDER_REVIEW: 'warn',
  ACTIVE: 'ok',
  ARCHIVED: 'muted',
  REJECTED: 'bad',
}
export const StateBadge = ({ state }) => (
  <span className={`badge badge-${STATE_TONE[state] || 'neutral'}`}>{state?.replace('_', ' ')}</span>
)
export const Badge = ({ tone = 'neutral', children, title }) => (
  <span className={`badge badge-${tone}`} title={title}>
    {children}
  </span>
)
export const SensitivityBadge = ({ level }) => {
  if (!level) return <span className="muted">-</span>
  const tone = { PUBLIC: 'ok', INTERNAL: 'info', RESTRICTED: 'warn', CONFIDENTIAL: 'bad' }[level]
  return <Badge tone={tone}>{level}</Badge>
}

export const Loading = ({ label = 'Loading' }) => <div className="loading">{label}...</div>

export function ErrorBox({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="alert alert-bad" role="alert">
      <strong>{error.status === 403 ? 'Access denied' : 'Something went wrong'}</strong>
      <div>{error.message}</div>
      {onRetry && (
        <button className="btn btn-small" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

export const Notice = ({ tone = 'info', title, children }) => (
  <div className={`alert alert-${tone}`}>
    {title && <strong>{title}</strong>}
    <div>{children}</div>
  </div>
)

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          className={`tab ${value === t.id ? 'tab-active' : ''}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.count !== undefined && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function ScoreBar({ value, label }) {
  const pct = Math.round((value ?? 0) * 100)
  return (
    <div className="scorebar" title={`${label}: ${value}`}>
      <div className="scorebar-fill" style={{ width: `${pct}%` }} />
    </div>
  )
}

export const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '-')
export const fmtDateTime = (iso) => (iso ? new Date(iso).toLocaleString() : '-')

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      <div>{children}</div>
    </div>
  )
}

export function KV({ rows }) {
  return (
    <dl className="kv">
      {rows.map(([k, v, hint]) => (
        <div key={k} className="kv-row">
          <dt>
            {k}
            {hint && <span className="hint">{hint}</span>}
          </dt>
          <dd>{v === null || v === undefined || v === '' ? <span className="muted">not set</span> : v}</dd>
        </div>
      ))}
    </dl>
  )
}
