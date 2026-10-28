import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { ErrorBox, Loading, useAsync } from '../ui'

const ORDER = ['ACTIVE', 'UNDER_REVIEW', 'REJECTED', 'ARCHIVED', 'DRAFT']

export default function Dashboard() {
  const { user, can } = useAuth()
  const { data, error, loading, reload } = useAsync(() => api.stats(), [])

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Welcome, {user.name.split(' ')[0]}</h1>
          <p>
            Every document passes parsing, duplicate checks, PII detection and a human review before it becomes ACTIVE. Counts below show
            only what your role may see.
          </p>
        </div>
        <Link className="btn btn-primary" to="/upload">
          Upload a document
        </Link>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && (
        <>
          <div className="tiles">
            <Link className="tile" to="/documents">
              <div className="tile-value">{data.total}</div>
              <div className="tile-label">Documents you can see</div>
            </Link>
            <Link className="tile" to="/documents?state=ACTIVE">
              <div className="tile-value">{data.by_state.ACTIVE}</div>
              <div className="tile-label">Active</div>
            </Link>
            <Link className={`tile ${data.pending_review ? 'tile-warn' : ''}`} to={can('review') ? '/review' : '/documents?state=UNDER_REVIEW'}>
              <div className="tile-value">{data.pending_review}</div>
              <div className="tile-label">Waiting for review</div>
            </Link>
            <Link className={`tile ${data.expiring_90_days ? 'tile-warn' : ''}`} to={can('review') ? '/expiry' : '/documents'}>
              <div className="tile-value">{data.expiring_90_days}</div>
              <div className="tile-label">Expiring within 90 days</div>
            </Link>
            <div className="tile">
              <div className="tile-value">{data.pii_flagged}</div>
              <div className="tile-label">With PII flags</div>
            </div>
            <div className="tile">
              <div className="tile-value">{data.avg_quality ?? '-'}</div>
              <div className="tile-label">Average quality score</div>
            </div>
            <div className="tile">
              <div className="tile-value">{data.chunks_active}</div>
              <div className="tile-label">Chunks in active documents</div>
            </div>
          </div>

          <div className="grid-2">
            <div className="card">
              <h2>Documents by state</h2>
              {data.total === 0 ? (
                <p className="muted">Nothing to show yet.</p>
              ) : (
                <>
                  <div className="stackbar" role="img" aria-label="Documents by state">
                    {ORDER.filter((s) => data.by_state[s]).map((s) => (
                      <div key={s} className={`s-${s}`} style={{ width: `${(data.by_state[s] / data.total) * 100}%` }} title={`${s}: ${data.by_state[s]}`} />
                    ))}
                  </div>
                  <div className="legend">
                    {ORDER.map((s) => (
                      <span key={s}>
                        <i className={`dot s-${s}`} />
                        {s.replace('_', ' ')} <strong>{data.by_state[s]}</strong>
                      </span>
                    ))}
                  </div>
                </>
              )}
            </div>
            <div className="card">
              <h2>How a document moves</h2>
              <ol className="plain" style={{ margin: 0, paddingLeft: 20, display: 'grid', gap: 6 }}>
                <li>Upload. The file is parsed and checked for duplicates and PII.</li>
                <li>It waits in <strong>Under review</strong> with the findings attached.</li>
                <li>A reviewer sets sensitivity, therapeutic area and region, then approves or rejects.</li>
                <li>Approved documents become <strong>Active</strong>, with an expiry date and quality score.</li>
              </ol>
              {can('review') && (
                <p style={{ marginTop: 12 }}>
                  <Link to="/review">Open the review queue</Link>
                </p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
