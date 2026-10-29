import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import DocumentFactsCard from '../DocumentFactsCard'
import GraphView, { KIND_STYLE, Legend } from '../GraphView'
import { EMPTY_FILTERS, FILTER_KEY_BY_KIND, FilterBar, anyFilter, filterParams } from '../GraphFilters'
import { Badge, Empty, ErrorBox, KV, Loading, useAsync } from '../ui'

const MODES = [
  { id: 'knowledge', label: 'Knowledge: spaces, documents and the entities they mention' },
  { id: 'reference', label: 'Reference: the catalog entities and how they relate' },
]

export default function KnowledgeSpace() {
  const [mode, setMode] = useState('knowledge')
  const [space, setSpace] = useState('')
  const [edgeLabels, setEdgeLabels] = useState(false)
  const [selected, setSelected] = useState(null)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const spaces = useAsync(() => api.l2Spaces(), [])
  // what each filter can offer THIS user (only documents they may read), for the space chosen
  const options = useAsync(() => (mode === 'knowledge' ? api.l2GraphFilters({ space }) : Promise.resolve(null)), [mode, space])
  const filterKey = JSON.stringify(filters)
  const filtering = mode === 'knowledge' && anyFilter(filters)
  const graph = useAsync(
    () => api.l2Graph({ mode, space: mode === 'knowledge' ? space : undefined, limit: 80, ...(mode === 'knowledge' ? filterParams(filters) : {}) }),
    [mode, space, filterKey],
  )
  const addFilter = (key, id) => setFilters((f) => ({ ...f, [key]: f[key].includes(id) ? f[key] : [...f[key], id] }))
  // the document whose facts are listed: the document node you clicked, else the one document picked in the Document filter
  const selectedNode = graph.data?.nodes.find((n) => n.id === selected)
  const factsDoc = mode !== 'knowledge' ? null : selectedNode?.kind === 'Document' ? selectedNode.id : filters.documents.length === 1 ? filters.documents[0] : null

  const kinds = useMemo(() => new Set((graph.data?.nodes || []).map((n) => n.kind)), [graph.data])
  const list = useMemo(
    () => [...(graph.data?.nodes || [])].sort((a, b) => a.kind.localeCompare(b.kind) || a.label.localeCompare(b.label)),
    [graph.data],
  )

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Knowledge space</h1>
          <p>
            The knowledge graph built from approved documents. Each document is linked to the brands, indications, drugs, areas and regions it
            mentions, and every one of those entities is grounded in the data catalog. You see only the documents your access attributes allow.
          </p>
        </div>
      </div>

      <ErrorBox error={spaces.error} onRetry={spaces.reload} />
      {spaces.loading && !spaces.data && <Loading />}
      {spaces.data && (
        <div className="tiles">
          {spaces.data.spaces.map((s) => (
            <button
              key={s.id}
              className="tile"
              style={{ textAlign: 'left', font: 'inherit', cursor: 'pointer', outline: space === s.id ? '2px solid var(--accent)' : undefined }}
              onClick={() => {
                setMode('knowledge')
                setSpace(space === s.id ? '' : s.id)
                setSelected(null)
              }}
              aria-pressed={space === s.id}
            >
              <div className="tile-value">{s.documents}</div>
              <div className="tile-label">{s.name}</div>
              <div className="doc-sub">
                {s.average_quality !== null ? `avg quality ${s.average_quality}` : 'no readable documents'}
                {s.archived ? ` · ${s.archived} archived` : ''}
              </div>
            </button>
          ))}
        </div>
      )}

      <div className="card">
        <div className="row spread" style={{ marginBottom: 12 }}>
          <div className="row">
            <label className="check" htmlFor="mode" style={{ fontWeight: 600 }}>View</label>
            <select id="mode" value={mode} onChange={(e) => { setMode(e.target.value); setSelected(null) }} style={{ width: 'auto' }}>
              {MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
            {mode === 'knowledge' && space && (
              <Badge tone="info">
                {spaces.data?.spaces.find((s) => s.id === space)?.name}{' '}
                <button className="btn btn-small" style={{ padding: '0 6px', marginLeft: 4 }} onClick={() => setSpace('')} aria-label="Show all spaces">x</button>
              </Badge>
            )}
          </div>
          <label className="check">
            <input type="checkbox" checked={edgeLabels} onChange={(e) => setEdgeLabels(e.target.checked)} />
            Show relationship names
          </label>
        </div>

        {mode === 'knowledge' && (
          <div style={{ marginBottom: 12 }}>
            <ErrorBox error={options.error} onRetry={options.reload} />
            <FilterBar options={options.data} filters={filters} setFilters={setFilters} />
          </div>
        )}

        <ErrorBox error={graph.error} onRetry={graph.reload} />
        {graph.loading && !graph.data && <Loading label="Building graph" />}
        {graph.data && graph.data.nodes.length === 0 && filtering && (
          <Empty title="No document you may read matches these filters">
            Every filter has to hold, and only the documents your access allows are searched. Remove a filter to widen it.{' '}
            <button type="button" className="btn btn-small" onClick={() => setFilters(EMPTY_FILTERS)}>Clear all filters</button>
          </Empty>
        )}
        {graph.data && graph.data.nodes.length === 0 && !filtering && (
          <Empty title="Nothing to show">No document you may read is in the graph yet. Approved documents appear here after enrichment.</Empty>
        )}
        {graph.data && graph.data.nodes.length > 0 && (
          <div className="graph-layout">
            <div>
              <GraphView graph={graph.data} onSelect={(n) => setSelected(n?.id || null)} selected={selected} showEdgeLabels={edgeLabels} />
              <Legend kinds={kinds} />
              <p className="doc-sub" style={{ marginTop: 6 }}>
                {graph.data.stats.nodes} nodes, {graph.data.stats.edges} relationships
                {mode === 'knowledge' ? `, ${graph.data.stats.documents_shown} documents` : ''}
                {graph.data.stats.limited ? ` (the first ${graph.data.stats.documents_shown} of ${graph.data.stats.documents_matching} matching documents; narrow the filters to see the rest)` : ''}
                . Click a node to highlight its neighbours; scroll to zoom; drag to move.
                Dashed lines join brands named in the same passage.{filtering ? ' Entities you filtered by have a bold outline.' : ''}
              </p>
            </div>
            <NodePanel
              node={selectedNode}
              edges={graph.data.edges}
              nodes={graph.data.nodes}
              onPick={setSelected}
              onFilter={mode === 'knowledge' ? addFilter : null}
              setSpace={(id) => { setMode('knowledge'); setSpace(id) }}
            />
          </div>
        )}
      </div>

      {factsDoc ? (
        <DocumentFactsCard id={factsDoc} onFilter={addFilter} />
      ) : (
        mode === 'knowledge' && graph.data && graph.data.nodes.length > 0 && (
          <p className="doc-sub">Click a document node, or pick one document in the Document filter, to list the facts it holds: its entities, relationships and sentiment.</p>
        )
      )}

      {graph.data && graph.data.nodes.length > 0 && (
        <details className="card">
          <summary style={{ cursor: 'pointer', fontWeight: 600 }}>All {list.length} nodes as a list</summary>
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Kind</th><th>Name</th><th>Connected to</th></tr></thead>
              <tbody>
                {list.map((n) => (
                  <tr key={n.id} className="clickable" onClick={() => setSelected(n.id)}>
                    <td>{KIND_STYLE[n.kind]?.label || n.kind}</td>
                    <td>{n.label}</td>
                    <td className="num">{graph.data.edges.filter((e) => e.source === n.id || e.target === n.id).length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  )
}

function NodePanel({ node, edges, nodes, onPick, setSpace, onFilter }) {
  if (!node) {
    return (
      <div className="card" style={{ alignSelf: 'start' }}>
        <h3>Select a node</h3>
        <p className="muted">Choose a node in the graph to see what it is, where it is grounded and what it is connected to.</p>
      </div>
    )
  }
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]))
  const around = edges.filter((e) => e.source === node.id || e.target === node.id)
  const isEntity = !['Document', 'KnowledgeSpace'].includes(node.kind)
  return (
    <div className="card" style={{ alignSelf: 'start' }}>
      <div className="row">
        <Badge tone="info">{KIND_STYLE[node.kind]?.label || node.kind}</Badge>
      </div>
      <h3 style={{ marginTop: 8 }}>{node.title || node.label}</h3>
      {node.kind === 'Document' && (
        <KV rows={[['Document', <Link to={`/documents/${encodeURIComponent(node.id)}`}>{node.id}</Link>], ['Study type', node.study_type], ['Sensitivity', node.sensitivity]]} />
      )}
      {isEntity && (
        <KV rows={[['Open', <Link to={`/entities/${encodeURIComponent(node.id)}`}>entity page</Link>], ['Grounded in', `${node.grounded} catalog column${node.grounded === 1 ? '' : 's'}`]]} />
      )}
      {isEntity && onFilter && FILTER_KEY_BY_KIND[node.kind] && (
        <button type="button" className="btn btn-small" style={{ marginTop: 8 }} onClick={() => onFilter(FILTER_KEY_BY_KIND[node.kind], node.id)}>
          Filter the graph by this {(KIND_STYLE[node.kind]?.label || node.kind).toLowerCase()}
        </button>
      )}
      {node.kind === 'Document' && onFilter && (
        <button type="button" className="btn btn-small" style={{ marginTop: 8 }} onClick={() => onFilter('documents', node.id)}>
          Show only this document
        </button>
      )}
      {node.kind === 'KnowledgeSpace' && (
        <div className="stack" style={{ gap: 8 }}>
          <KV rows={[['Documents shown', node.documents]]} />
          <button className="btn btn-small" onClick={() => setSpace(node.id)}>Show only this space</button>
        </div>
      )}
      <h3 style={{ marginTop: 14 }}>Connections ({around.length})</h3>
      <ul className="plain" style={{ maxHeight: 220, overflow: 'auto' }}>
        {around.slice(0, 40).map((e) => {
          const other = byId[e.source === node.id ? e.target : e.source]
          return (
            <li key={e.id}>
              <span className="muted">{e.source === node.id ? e.label : `is ${e.label} of`}</span>{' '}
              <button className="btn btn-small" style={{ padding: '0 6px' }} onClick={() => onPick(other.id)}>{other.label}</button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
