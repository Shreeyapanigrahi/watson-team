import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api'
import GraphView, { KIND_STYLE, Legend } from './GraphView'
import { Badge, ErrorBox, KV, Loading, Notice, fmtDate, useAsync } from './ui'

const kindLabel = (k) => KIND_STYLE[k]?.label || k
const VERSION_TEXT = {
  new_version: 'A new version of',
  near_duplicate: 'A near duplicate of',
}

/** Where an approved document sits in the knowledge graph: a picture, the link to the Knowledge space, the documents that share its
 *  entities, and what it added. Everything comes from the graph (GET /api/l2/documents/{uri}/position) and counts only documents the
 *  signed-in user may read. Just after approval, enrichment runs in the background, so this waits and refreshes until it is done. */
export default function DocumentPosition({ uri, onReady }) {
  const p = useAsync(() => api.l2Position(uri), [uri])
  const [selected, setSelected] = useState(null)
  const d = p.data
  const [tries, setTries] = useState(0)
  // Right after approval the enrichment job may not have started (no status yet): wait for it, but not for ever (about a minute).
  const waiting = d && d.state === 'ACTIVE' && !d.enriched && (['RUNNING', 'QUEUED'].includes(d.enrichment?.state) || (!d.enrichment && tries < 20))
  const wasWaiting = useRef(false)

  useEffect(() => {
    if (!waiting) return undefined
    wasWaiting.current = true
    const t = setInterval(() => { setTries((n) => n + 1); p.reload() }, 3000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting, uri])

  useEffect(() => {                                              // enrichment just finished: let the page around this panel refresh too
    if (d?.enriched && wasWaiting.current) {
      wasWaiting.current = false
      onReady?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d?.enriched])

  const kinds = useMemo(() => new Set((d?.graph?.nodes || []).map((n) => n.kind)), [d])
  if (p.loading && !d) return <Loading />
  if (p.error) return <ErrorBox error={p.error} onRetry={p.reload} />
  if (d.state !== 'ACTIVE') return null                         // not approved yet: nothing is in the graph, and the Knowledge tab says so
  if (waiting) {
    return (
      <div className="card" role="status">
        <h3>Position in the knowledge graph</h3>
        <p><span className="spinner" aria-hidden="true" /> This document was approved and is being added to the graph. This page updates when it is done.</p>
      </div>
    )
  }
  if (!d.enriched) {
    return (
      <Notice tone="warn" title="Not in the graph">
        {d.enrichment?.state === 'FAILED' ? `Enrichment stopped at ${d.enrichment.stage}: ${d.enrichment.error}` : 'Enrichment has not run for this document.'}
      </Notice>
    )
  }

  const a = d.added
  const open = `/knowledge?documents=${encodeURIComponent(uri)}`
  const picked = d.graph.nodes.find((n) => n.id === selected)
  return (
    <div className="stack">
      <div className="card">
        <div className="row spread">
          <div>
            <h3 style={{ margin: 0 }}>Position in the knowledge graph</h3>
            <p className="doc-sub">
              In {d.spaces.map((s) => s.name).join(' and ') || 'no knowledge space'}, linked to {a.entities_named} entities
              {a.entities_implied ? ` (and ${a.entities_implied} more that they belong to)` : ''}, sharing entities with {d.shared_total} other document{d.shared_total === 1 ? '' : 's'} you may read.
            </p>
          </div>
          <Link className="btn btn-small" to={open}>Open in Knowledge space</Link>
        </div>
        {d.version && (
          <Notice tone="info" title={`${VERSION_TEXT[d.version.verdict]} ${d.version.original_uri ? '' : 'another document'}`.trim()}>
            {d.version.original_uri
              ? <Link to={`/documents/${encodeURIComponent(d.version.original_uri)}`}>{d.version.original_uri}</Link>
              : 'You may not read that document.'}
            {d.version.similarity ? ` (similarity ${d.version.similarity})` : ''}
          </Notice>
        )}
        <div className="graph-layout" style={{ marginTop: 10 }}>
          <div>
            <GraphView graph={d.graph} onSelect={(n) => setSelected(n?.id || null)} selected={selected} height={460} labelWidth={72} spread={1.6} />
            <Legend kinds={kinds} />
            <p className="doc-sub" style={{ marginTop: 6 }}>
              The bold document is this one. The others share some of its brands, drugs, indications or drug classes.
              {d.graph.omitted.entities || d.graph.omitted.documents
                ? ` Not drawn: ${d.graph.omitted.entities} entit${d.graph.omitted.entities === 1 ? 'y' : 'ies'} and ${d.graph.omitted.documents} document${d.graph.omitted.documents === 1 ? '' : 's'} (see the tables below, or open the Knowledge space).`
                : ''}
            </p>
          </div>
          <div className="card" style={{ alignSelf: 'start' }}>
            {picked ? (
              <>
                <Badge tone="info">{kindLabel(picked.kind)}</Badge>
                <h3 style={{ marginTop: 8 }}>{picked.title || picked.label}</h3>
                {picked.kind === 'Document' && picked.id !== uri && <Link to={`/documents/${encodeURIComponent(picked.id)}`}>Open this document</Link>}
                {!['Document', 'KnowledgeSpace'].includes(picked.kind) && <Link to={`/entities/${encodeURIComponent(picked.id)}`}>Open the entity page</Link>}
              </>
            ) : <p className="muted">Click a node to see what it is.</p>}
          </div>
        </div>
      </div>

      <div className="card">
        <h3>What it added</h3>
        <div className="grid-2">
          <KV rows={[
            ['Triples written', a.triples],
            ['Passages (chunks) indexed', a.chunks],
            ['Entity mentions found', a.mentions],
            ['Sentiment statements', a.sentiments],
            ['Entities it names', a.entities_named],
            ['Entities it also counts for', a.entities_implied],
            ['New provisional entities', a.provisional.length ? a.provisional.map((x) => x.text).join(', ') : 'none'],
          ]} />
          <div className="table-wrap">
            <table>
              <thead><tr><th>Knowledge space</th><th className="num">Documents before</th><th className="num">After</th></tr></thead>
              <tbody>
                {d.spaces.map((s) => <tr key={s.id}><td>{s.name}</td><td className="num">{s.documents_before}</td><td className="num">{s.documents_after}</td></tr>)}
              </tbody>
            </table>
          </div>
        </div>
        <p className="doc-sub" style={{ marginTop: 8 }}>Counts are the documents you may read. An entity with 0 before is new to your view of the graph.</p>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Entity</th><th>Kind</th><th className="num">Documents before</th><th className="num">After</th></tr></thead>
            <tbody>
              {d.entities.map((e) => (
                <tr key={e.id}>
                  <td>
                    <Link to={`/entities/${encodeURIComponent(e.id)}`}>{e.label}</Link>
                    {e.via && <div className="muted small">counted through {kindLabel(e.via.kind).toLowerCase()} {e.via.label}</div>}
                  </td>
                  <td>{kindLabel(e.kind)}</td>
                  <td className="num">{e.documents_before}</td>
                  <td className="num">{e.documents_after}{e.documents_before === 0 && <> <Badge tone="info">first</Badge></>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h3>Documents that share its entities ({d.shared_total})</h3>
        {d.shared.length === 0 ? <p className="muted">No other document you may read names the same brands, drugs, drug classes or indications.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Document</th><th>Study type</th><th>Region</th><th>Date</th><th>Shares</th></tr></thead>
              <tbody>
                {d.shared.map((s) => (
                  <tr key={s.document_uri}>
                    <td><Link to={`/documents/${encodeURIComponent(s.document_uri)}`}>{s.title || s.document_uri}</Link><div className="muted small">{s.document_uri}</div></td>
                    <td>{s.study_type}</td>
                    <td>{s.geography}</td>
                    <td>{fmtDate(s.date)}</td>
                    <td>{s.shared.map((x) => <Badge key={x.id} tone="info" title={kindLabel(x.kind)}>{x.label}</Badge>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {d.shared_total > d.shared.length && (
          <p className="doc-sub" style={{ marginTop: 8 }}>
            The {d.shared.length} that share the most. <Link to={open}>Open the Knowledge space</Link> to explore the rest.
          </p>
        )}
      </div>
    </div>
  )
}
