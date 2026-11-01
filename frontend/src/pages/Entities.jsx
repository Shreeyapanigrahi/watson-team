import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { KIND_STYLE } from '../GraphView'
import { Badge, Empty, ErrorBox, Loading, useAsync } from '../ui'

const KINDS = ['Brand', 'Drug', 'DrugClass', 'Indication', 'TherapeuticArea', 'Geography']

export default function Entities() {
  const [kind, setKind] = useState('')
  const [q, setQ] = useState('')
  const [used, setUsed] = useState(false)
  const rows = useAsync(() => api.l2Entities({ kind, q }), [kind, q])
  // Hides the entities that no readable document mentions (they stay in the catalog; this only changes what is listed here).
  const list = rows.data ? rows.data.entities.filter((e) => !used || e.documents > 0) : []
  const hidden = rows.data ? rows.data.entities.length - list.length : 0

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Entities</h1>
          <p>
            Every brand, drug, class, indication, therapeutic area and region that the platform knows. Each one comes from the data catalog, and
            the document counts include only the documents you may read.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          <label htmlFor="kind" style={{ fontWeight: 600 }}>Kind</label>
          <select id="kind" value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 'auto' }}>
            <option value="">All kinds</option>
            {KINDS.map((k) => <option key={k} value={k}>{KIND_STYLE[k].label}</option>)}
          </select>
          <label htmlFor="q" style={{ fontWeight: 600 }}>Search</label>
          <input id="q" type="search" value={q} placeholder="Name or synonym" onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} />
          <label className="check" htmlFor="used">
            <input id="used" type="checkbox" checked={used} onChange={(e) => setUsed(e.target.checked)} /> Only entities with documents
          </label>
        </div>

        <ErrorBox error={rows.error} onRetry={rows.reload} />
        {rows.loading && !rows.data && <Loading />}
        {rows.data && list.length === 0 && (
          <Empty title="No entity matches">
            {hidden ? 'None of the matching entities has a document you may read. Clear "Only entities with documents" to see them.' : 'Try a different name or clear the filter.'}
          </Empty>
        )}
        {rows.data && list.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th><th>Kind</th><th>Also known as</th><th className="num">Documents</th>
                  <th className="num" title="Catalog columns this entity is stored in">Catalog columns</th><th>External IDs</th>
                </tr>
              </thead>
              <tbody>
                {list.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <Link to={`/entities/${encodeURIComponent(e.id)}`}>{e.label}</Link>
                      {e.grouping && <> <Badge tone="neutral" title="A grouping term; it has narrower indications beneath it">group</Badge></>}
                    </td>
                    <td>{KIND_STYLE[e.kind]?.label || e.kind}</td>
                    <td>{e.aliases.length ? e.aliases.join(', ') : <span className="muted">-</span>}</td>
                    <td className="num">{e.documents}</td>
                    <td className="num">{e.grounded_in}</td>
                    <td className="mono">
                      {Object.keys(e.alignment).length
                        ? Object.entries(e.alignment).map(([n, id]) => <div key={n}>{n}: {id}</div>)
                        : <span className="muted">none</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.data && <p className="doc-sub" style={{ marginTop: 8 }}>{list.length} entities{hidden ? ` (${hidden} with no document hidden)` : ''}</p>}
      </div>
    </div>
  )
}
