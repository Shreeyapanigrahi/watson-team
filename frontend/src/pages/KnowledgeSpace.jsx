import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import GraphView, { KIND_STYLE, Legend } from '../GraphView'
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
  const spaces = useAsync(() => api.l2Spaces(), [])
  const graph = useAsync(() => api.l2Graph({ mode, space: mode === 'knowledge' ? space : undefined, limit: 80 }), [mode, space])

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

        <ErrorBox error={graph.error} onRetry={graph.reload} />
        {graph.loading && !graph.data && <Loading label="Building graph" />}
        {graph.data && graph.data.nodes.length === 0 && (
          <Empty title="Nothing to show">No document you may read is in the graph yet. Approved documents appear here after enrichment.</Empty>
        )}
        {graph.data && graph.data.nodes.length > 0 && (
          <div className="graph-layout">
            <div>
              <GraphView graph={graph.data} onSelect={(n) => setSelected(n?.id || null)} selected={selected} showEdgeLabels={edgeLabels} />
              <Legend kinds={kinds} />
              <p className="doc-sub" style={{ marginTop: 6 }}>
                {graph.data.stats.nodes} nodes, {graph.data.stats.edges} relationships
                {mode === 'knowledge' ? `, ${graph.data.stats.documents_shown} documents` : ''}. Click a node to highlight its neighbours; scroll to zoom; drag to move.
                Dashed lines join brands named in the same passage.
              </p>
            </div>
            <NodePanel node={graph.data.nodes.find((n) => n.id === selected)} edges={graph.data.edges} nodes={graph.data.nodes} onPick={setSelected} setSpace={(id) => { setMode('knowledge'); setSpace(id) }} />
          </div>
        )}
      </div>

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

function NodePanel({ node, edges, nodes, onPick, setSpace }) {
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
