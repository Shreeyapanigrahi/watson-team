import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { KIND_STYLE } from '../GraphView'
import { Badge, Empty, ErrorBox, Loading, useAsync } from '../ui'

const KINDS = ['Brand', 'Drug', 'DrugClass', 'Indication', 'TherapeuticArea', 'Geography']

export default function Entities() {
  const [kind, setKind] = useState('')
  const [q, setQ] = useState('')
  const rows = useAsync(() => api.l2Entities({ kind, q }), [kind, q])

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
        </div>

        <ErrorBox error={rows.error} onRetry={rows.reload} />
        {rows.loading && !rows.data && <Loading />}
        {rows.data && rows.data.entities.length === 0 && <Empty title="No entity matches">Try a different name or clear the filter.</Empty>}
        {rows.data && rows.data.entities.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th><th>Kind</th><th>Also known as</th><th className="num">Documents</th>
                  <th className="num" title="Catalog columns this entity is stored in">Catalog columns</th><th>External IDs</th>
                </tr>
              </thead>
              <tbody>
                {rows.data.entities.map((e) => (
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
        {rows.data && <p className="doc-sub" style={{ marginTop: 8 }}>{rows.data.count} entities</p>}
      </div>
    </div>
  )
}
