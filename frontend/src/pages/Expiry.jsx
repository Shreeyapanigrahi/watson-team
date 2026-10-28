import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { Badge, Empty, ErrorBox, Loading, Notice, fmtDate, useAsync } from '../ui'

export default function Expiry() {
  const { can } = useAuth()
  const [days, setDays] = useState(90)
  const { data, error, loading, reload } = useAsync(() => api.expiryUpcoming(days), [days])
  const [scan, setScan] = useState(null)
  const [scanErr, setScanErr] = useState(null)
  const [busy, setBusy] = useState(false)

  const runScan = async () => {
    setBusy(true)
    setScan(null)
    setScanErr(null)
    try {
      setScan(await api.expiryScan())
      reload()
    } catch (e) {
      setScanErr(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Expiry and retention</h1>
          <p>
            Each ACTIVE document has an expiry date set by its study type (HCP survey 730 days, KOL interview 365, competitive landscape
            1825, others 1095) unless the reviewer overrode it. The scan sends expired documents back to review.
          </p>
        </div>
        <div className="row">
          <select aria-label="Window" value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: 170 }}>
            <option value={30}>Next 30 days</option>
            <option value={90}>Next 90 days</option>
            <option value={365}>Next 365 days</option>
          </select>
          {can('expiry_scan') && (
            <button className="btn btn-primary" onClick={runScan} disabled={busy}>
              {busy ? 'Scanning...' : 'Run expiry scan'}
            </button>
          )}
        </div>
      </div>
      {scan && (
        <Notice tone={scan.moved_to_under_review.length ? 'warn' : 'ok'} title="Scan complete">
          {scan.moved_to_under_review.length
            ? `${scan.moved_to_under_review.length} expired document(s) moved back to UNDER REVIEW: ${scan.moved_to_under_review.join(', ')}`
            : 'No ACTIVE document has passed its expiry date.'}
        </Notice>
      )}
      <ErrorBox error={scanErr} />
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && data.documents.length === 0 && <Empty title="Nothing expiring">No ACTIVE document expires within {days} days.</Empty>}
      {data && data.documents.length > 0 && (
        <div className="card card-flush">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Study type</th>
                  <th>Area</th>
                  <th>Expires</th>
                  <th className="num">Days left</th>
                </tr>
              </thead>
              <tbody>
                {data.documents.map((d) => (
                  <tr key={d.document_uri}>
                    <td>
                      <Link to={`/documents/${encodeURIComponent(d.document_uri)}`}>{d.document_name}</Link>
                      <div className="doc-sub">{d.document_uri}</div>
                    </td>
                    <td>{d.study_type}</td>
                    <td>{d.therapeutic_area}</td>
                    <td>{fmtDate(d.expiry_date)}</td>
                    <td className="num">
                      {d.days_left < 0 ? <Badge tone="bad">expired {-d.days_left} d ago</Badge> : <Badge tone="warn">{d.days_left} d</Badge>}
                    </td>
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
