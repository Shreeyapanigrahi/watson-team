import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import DeleteDocument from '../DeleteDocument'
import { Empty, ErrorBox, Loading, SensitivityBadge, StateBadge, Tabs, fmtDate, useAsync } from '../ui'

const STATES = ['ALL', 'ACTIVE', 'UNDER_REVIEW', 'REJECTED', 'ARCHIVED']

export default function Documents() {
  const nav = useNavigate()
  const { user, can } = useAuth()
  const [params, setParams] = useSearchParams()
  const state = params.get('state') || 'ALL'
  const [q, setQ] = useState('')
  const { data, error, loading, reload } = useAsync(
    () => api.documents({ state: state === 'ALL' ? undefined : state, q }),
    [state, q],
  )
  const all = useAsync(() => api.documents({ q }), [q])
  const counts = {}
  all.data?.documents.forEach((d) => (counts[d.state] = (counts[d.state] || 0) + 1))
  const deleted = () => {
    reload()
    all.reload()
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Documents</h1>
          <p>
            Signed in as {user.role}. Documents outside your sensitivity level, therapeutic area or region are not listed at all. They are
            never shown redacted.
          </p>
        </div>
        <input
          type="search"
          placeholder="Search title, brand, indication"
          aria-label="Search documents"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ width: 280 }}
        />
      </div>

      <Tabs
        tabs={STATES.map((s) => ({ id: s, label: s === 'ALL' ? 'All' : s.replace('_', ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase()), count: s === 'ALL' ? all.data?.count : counts[s] || 0 }))}
        value={state}
        onChange={(s) => setParams(s === 'ALL' ? {} : { state: s })}
      />

      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && data.documents.length === 0 && (
        <Empty title="No documents here">Nothing matches this filter for your access level. Try another tab, or upload a document.</Empty>
      )}
      {data && data.documents.length > 0 && (
        <div className="card card-flush">
          <div className="table-wrap">
            <table className="table-tight">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Study type</th>
                  <th>Area</th>
                  <th>Region</th>
                  <th>Sensitivity</th>
                  <th className="num">Quality</th>
                  <th>Expires</th>
                  <th>State</th>
                  {can('delete') && <th className="sticky-end" aria-label="Delete" />}
                </tr>
              </thead>
              <tbody>
                {data.documents.map((d) => (
                  <tr key={d.document_uri} className="clickable" onClick={() => nav(`/documents/${encodeURIComponent(d.document_uri)}`)}>
                    <td>
                      <div className="doc-title">{d.title || d.source_filename}</div>
                      <div className="doc-sub">
                        {d.document_uri} · {d.source_filename}
                        {d.pii_detected && ' · PII flagged'}
                      </div>
                    </td>
                    <td>{d.study_type || '-'}</td>
                    <td>{d.therapeutic_area || '-'}</td>
                    <td>{d.geography || '-'}</td>
                    <td>
                      <SensitivityBadge level={d.sensitivity_level} />
                    </td>
                    <td className="num">{d.quality_score ?? '-'}</td>
                    <td>{fmtDate(d.expiry_date)}</td>
                    <td>
                      <StateBadge state={d.state} />
                    </td>
                    {can('delete') && (
                      <td className="sticky-end">
                        <DeleteDocument uri={d.document_uri} onDeleted={deleted} />
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
