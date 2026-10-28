import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { Badge, ErrorBox, KV, Notice, StateBadge } from '../ui'

export default function Upload() {
  const { meta } = useAuth()
  const input = useRef(null)
  const [file, setFile] = useState(null)
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const choose = (f) => {
    setFile(f || null)
    setResult(null)
    setError(null)
  }

  const submit = async () => {
    if (!file) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult(await api.upload(file))
      setFile(null)
      if (input.current) input.current.value = ''
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const exts = meta?.allowed_extensions?.join(', ') || '.pdf, .docx, .pptx, .txt'

  return (
    <div className="stack" style={{ maxWidth: 860 }}>
      <div className="page-head">
        <div>
          <h1>Upload a document</h1>
          <p>
            The file goes through the full Layer 1 pipeline: parsing, duplicate check, PII scan, metadata extraction and chunking. It then
            waits for a reviewer. Nothing becomes ACTIVE without a human decision.
          </p>
        </div>
      </div>

      <label
        className={`drop ${over ? 'drop-over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          choose(e.dataTransfer.files?.[0])
        }}
      >
        <input ref={input} id="file-input" type="file" accept={meta?.allowed_extensions?.join(',')} onChange={(e) => choose(e.target.files?.[0])} />
        <strong>{file ? file.name : 'Drop a file here, or click to choose one'}</strong>
        <span className="muted">
          {file ? `${(file.size / 1024).toFixed(0)} KB` : `Accepted: ${exts}. Up to ${meta?.max_upload_mb ?? 20} MB.`}
        </span>
      </label>

      <div className="row">
        <button className="btn btn-primary" onClick={submit} disabled={!file || busy}>
          {busy ? 'Processing...' : 'Upload and process'}
        </button>
        {file && !busy && (
          <button className="btn" onClick={() => choose(null)}>
            Clear
          </button>
        )}
      </div>

      <ErrorBox error={error} />
      {result && <Result r={result} />}
    </div>
  )
}

function Result({ r }) {
  const rejected = r.state === 'REJECTED'
  return (
    <div className="card stack">
      <div className="row spread">
        <div className="row">
          <h2>{r.source_filename}</h2>
          <StateBadge state={r.state} />
        </div>
        <Link className="btn" to={`/documents/${encodeURIComponent(r.document_uri)}`}>
          Open {r.document_uri}
        </Link>
      </div>

      {rejected ? (
        <Notice tone="bad" title="Rejected by the pipeline">
          {r.rejection_reason}
        </Notice>
      ) : (
        <Notice tone="warn" title="Waiting for review">
          The automatic checks are complete. A reviewer must approve this document before it becomes ACTIVE.
        </Notice>
      )}
      {r.notes?.length > 0 && (
        <ul className="plain">
          {r.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}

      <div className="grid-2">
        <div>
          <h3>Checks</h3>
          <KV
            rows={[
              ['Duplicate check', <Badge tone={r.dedup?.verdict === 'unique' ? 'ok' : 'warn'}>{r.dedup?.verdict?.replace('_', ' ')}</Badge>],
              [
                'PII scan',
                r.pii?.pii_detected ? (
                  <Badge tone="bad">flagged: {r.pii.pii_types.join(', ')}</Badge>
                ) : (
                  <Badge tone="ok">none found</Badge>
                ),
              ],
              ['Chunks', r.chunking ? `${r.chunking.chunk_count} (${r.chunking.strategy})` : null],
              ['Parse warnings', r.parse_warnings?.length ? r.parse_warnings.length : 'none'],
            ]}
          />
          {r.parse_warnings?.length > 0 && (
            <ul className="plain" style={{ marginTop: 8 }}>
              {r.parse_warnings.map((w) => (
                <li key={w} className="doc-sub">
                  {w}
                </li>
              ))}
            </ul>
          )}
        </div>
        {r.metadata && (
          <div>
            <h3>What we extracted</h3>
            <KV
              rows={[
                ['Title', r.metadata.extracted_title],
                ['Author', r.metadata.extracted_author],
                ['Words', r.metadata.word_count],
                ['Language', r.metadata.language],
                ['Study type', r.metadata.study_type, 'suggested'],
                ['Therapeutic area', r.metadata.therapeutic_area, 'suggested'],
                ['Region', r.metadata.geography, 'suggested'],
                ['Brands', r.metadata.brands_mentioned?.join(', ')],
                ['Indications', r.metadata.indications_mentioned?.join(', ')],
              ]}
            />
          </div>
        )}
      </div>
    </div>
  )
}
