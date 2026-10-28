import { Link } from 'react-router-dom'
import { api } from '../api'
import { Badge, Empty, ErrorBox, Loading, fmtDateTime, useAsync } from '../ui'

export default function ReviewQueue() {
  const { data, error, loading, reload } = useAsync(() => api.documents({ state: 'UNDER_REVIEW' }), [])
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Review queue</h1>
          <p>
            Documents that finished the automatic checks and need a human decision. Open one to see what was found, set its access
            attributes, and approve or reject it.
          </p>
        </div>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && data.documents.length === 0 && <Empty title="The queue is empty">Upload a document to see it here.</Empty>}
      <div className="stack">
        {data?.documents.map((d) => (
          <Link
            key={d.document_uri}
            to={`/documents/${encodeURIComponent(d.document_uri)}`}
            className="card"
            style={{ textDecoration: 'none', color: 'inherit' }}
          >
            <div className="row spread">
              <div>
                <div className="doc-title">{d.title || d.source_filename}</div>
                <div className="doc-sub">
                  {d.document_uri} · {d.source_filename} · uploaded {fmtDateTime(d.created_at)} by {d.uploaded_by}
                </div>
              </div>
              <div className="row">
                {d.pii_detected && <Badge tone="bad">PII flagged</Badge>}
                {d.dedup_verdict && d.dedup_verdict !== 'unique' && <Badge tone="warn">{d.dedup_verdict.replace('_', ' ')}</Badge>}
                {d.word_count === 0 && <Badge tone="bad">no text</Badge>}
                <Badge tone="info">{d.study_type || 'type unknown'}</Badge>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
