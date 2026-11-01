import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import GraphView, { KIND_STYLE } from '../GraphView'
import { Badge, Empty, ErrorBox, KV, Loading, SensitivityBadge, ScoreBar, fmtDate, useAsync } from '../ui'

const link = (id, label) => <Link to={`/entities/${encodeURIComponent(id)}`}>{label}</Link>

// Why a document is listed although it does not name the entity itself (the catalog says the entity it names is part of this one).
const VIA = {
  Drug: (v) => `about the brand ${v.label}, which contains this drug`,
  DrugClass: (v) => `about the ${v.kind === 'Brand' ? 'brand' : 'drug'} ${v.label}, which belongs to this class`,
  Indication: (v) => `about the sub-indication ${v.label}`,
}

export default function EntityDetail() {
  const { id } = useParams()
  const ent = useAsync(() => api.l2Entity(id), [id])
  const graph = useAsync(() => api.l2Graph({ mode: 'entity', entity: id }), [id])

  if (ent.loading && !ent.data) return <Loading />
  if (ent.error) return <ErrorBox error={ent.error} onRetry={ent.reload} />
  const e = ent.data
  const alignments = Object.entries(e.alignment || {}).filter(([, a]) => a)

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <Link to="/entities" className="muted">All entities</Link>
          <h1 className="doc-title">{e.label}</h1>
          <p className="doc-sub">
            {KIND_STYLE[e.kind]?.label || e.kind}
            {e.grouping ? ' · grouping term' : ''}
            {e.aliases.length ? ` · also known as ${e.aliases.join(', ')}` : ''}
          </p>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <h3>Identity</h3>
          <KV
            rows={[
              ['Ontology IRI', <span className="mono">{e.iri}</span>],
              ['Catalog version', <span className="mono">{e.catalog_version}</span>, 'The graph is rebuilt from this version of the catalog'],
              ...(alignments.length
                ? alignments.map(([n, a]) => [n, <span className="mono">{a.id}{a.label ? ` (${a.label})` : ''}</span>, a.match ? `matched on ${a.match}` : undefined])
                : [['External IDs', 'none (this term has no exact match in the public vocabularies we looked up)']]),
            ]}
          />
        </div>
        <div className="card">
          <h3>Relationships in the catalog</h3>
          {e.relations.outgoing.length + e.relations.incoming.length === 0 && <p className="muted">No relationships recorded.</p>}
          <ul className="plain">
            {e.relations.outgoing.map((r) => (
              <li key={`o${r.relation}${r.id}`}><span className="muted">{r.relation}</span> {link(r.id, r.label)} <span className="muted">({KIND_STYLE[r.kind]?.label || r.kind})</span></li>
            ))}
            {e.relations.incoming.map((r) => (
              <li key={`i${r.relation}${r.id}`}>{link(r.id, r.label)} <span className="muted">{r.relation} this ({KIND_STYLE[r.kind]?.label || r.kind})</span></li>
            ))}
          </ul>
        </div>
      </div>

      <div className="card">
        <h3>Grounded in the data catalog</h3>
        <p className="muted">Where this entity's values live in the catalog. These are the columns the platform reads its name and attributes from.</p>
        {e.grounding.length === 0 ? (
          <Empty title="Not grounded">No catalog column holds this entity.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Column</th><th>Type</th><th>Value for this entity</th><th>Classification</th><th>Origin</th></tr></thead>
              <tbody>
                {e.grounding.map((g) => (
                  <tr key={g.fqn}>
                    <td className="mono">{g.fqn}</td>
                    <td className="mono">{g.data_type}</td>
                    <td>{String(g.value)}</td>
                    <td>{g.classification || <span className="muted">untagged</span>}</td>
                    <td><Badge tone={g.origin === 'upstream' ? 'info' : 'warn'} title={g.origin === 'upstream' ? 'Part of the supplied pharma model' : 'Added by Watson to hold what the model lacks'}>{g.origin === 'upstream' ? 'supplied model' : 'Watson extension'}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h3>Neighbourhood</h3>
        {graph.loading && !graph.data && <Loading label="Building graph" />}
        <ErrorBox error={graph.error} onRetry={graph.reload} />
        {graph.data && graph.data.nodes.length > 1 && <GraphView graph={graph.data} height={380} showEdgeLabels />}
        {graph.data && graph.data.nodes.length <= 1 && <p className="muted">Nothing is connected to this entity yet.</p>}
      </div>

      <div className="card">
        <h3>Documents that mention it ({e.documents.length})</h3>
        {e.documents.length === 0 ? (
          <Empty title="No readable document">No document you may read mentions this entity.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Document</th><th>Study type</th><th>Region</th><th>Date</th><th>Sensitivity</th></tr></thead>
              <tbody>
                {e.documents.map((d) => (
                  <tr key={d.document_uri}>
                    <td><Link to={`/documents/${encodeURIComponent(d.document_uri)}`}>{d.title || d.document_uri}</Link>{d.via && <div className="muted small">{VIA[e.kind]?.(d.via) || `counted through ${d.via.label}`}</div>}</td>
                    <td>{d.study_type}</td>
                    <td>{d.geography}</td>
                    <td>{fmtDate(d.date)}</td>
                    <td><SensitivityBadge level={d.sensitivity_level} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {e.sentiment.length > 0 && (
        <div className="card">
          <h3>Sentiment ({e.sentiment.length} statements{e.average_sentiment !== null ? `, average ${e.average_sentiment}` : ''})</h3>
          <p className="muted">Each row is one stated sentiment score (0 to 1), kept with the passage it was read from.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Brand</th><th>Indication</th><th>Region</th><th style={{ minWidth: 160 }}>Score</th><th>Source</th></tr></thead>
              <tbody>
                {e.sentiment.map((s) => (
                  <tr key={s.chunk_uri + s.date + s.brand + s.indication + s.geography}>
                    <td>{fmtDate(s.date)}</td>
                    <td>{s.brand}</td>
                    <td>{s.indication}</td>
                    <td>{s.geography}</td>
                    <td><ScoreBar value={s.score} label="Sentiment" /></td>
                    <td><Link to={`/documents/${encodeURIComponent(s.document_uri)}`}>{s.document_uri}</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {e.co_mentioned_brands.length > 0 && (
        <div className="card">
          <h3>Brands named in the same passages</h3>
          <ul className="plain">
            {e.co_mentioned_brands.map((b) => <li key={b.id}>{link(b.id, b.label)} <span className="muted">in {b.chunks} passage{b.chunks === 1 ? '' : 's'}</span></li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
