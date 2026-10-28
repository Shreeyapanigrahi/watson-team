import { Link } from 'react-router-dom'
import { api } from '../api'
import { Badge, ErrorBox, Loading, fmtDateTime, useAsync } from '../ui'

export default function Catalog() {
  const { data, error, loading, reload } = useAsync(() => api.catalog(), [])
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Data catalog</h1>
          <p>
            What Watson knows about its documents: the metadata fields, where each one comes from, and the entities found in active
            documents. Layer 2 builds the ontology from this catalog, so every class and property traces back to something listed here.
            Filtered to what your role may see.
          </p>
        </div>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && (
        <>
          <div className="tiles">
            <div className="tile">
              <div className="tile-value">{data.summary.documents}</div>
              <div className="tile-label">Catalogued documents</div>
            </div>
            <div className="tile">
              <div className="tile-value">{data.summary.active}</div>
              <div className="tile-label">Active</div>
            </div>
            <div className="tile">
              <div className="tile-value">{data.entities.brands.length}</div>
              <div className="tile-label">Brands found</div>
            </div>
            <div className="tile">
              <div className="tile-value">{data.entities.indications.length}</div>
              <div className="tile-label">Indications found</div>
            </div>
          </div>
          <p className="doc-sub">Generated {fmtDateTime(data.generated_at)}</p>

          <div className="card card-flush">
            <div style={{ padding: '16px 18px 0' }}>
              <h2>Metadata fields</h2>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Source</th>
                    <th>How</th>
                    <th>Needed for ACTIVE</th>
                  </tr>
                </thead>
                <tbody>
                  {data.fields.map((f) => (
                    <tr key={f.field}>
                      <td className="mono">{f.field}</td>
                      <td>{f.source}</td>
                      <td className="muted">{f.method}</td>
                      <td>{f.required_for_active ? <Badge tone="warn">required</Badge> : <span className="muted">optional</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid-2">
            <div className="card card-flush">
              <div style={{ padding: '16px 18px 0' }}>
                <h2>Brands (active documents)</h2>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Brand</th>
                      <th>Area</th>
                      <th>Drug class</th>
                      <th className="num">Docs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.entities.brands.map((b) => (
                      <tr key={b.name}>
                        <td>
                          <strong>{b.name}</strong>
                          <div className="doc-sub">{b.drug}</div>
                        </td>
                        <td>{b.therapeutic_area}</td>
                        <td>{b.drug_class}</td>
                        <td className="num">{b.active_documents}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="card card-flush">
              <div style={{ padding: '16px 18px 0' }}>
                <h2>Indications (active documents)</h2>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Indication</th>
                      <th>Area</th>
                      <th className="num">Docs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.entities.indications.map((i) => (
                      <tr key={i.name}>
                        <td>{i.name}</td>
                        <td>{i.therapeutic_area}</td>
                        <td className="num">{i.active_documents}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="card card-flush">
            <div style={{ padding: '16px 18px 0' }}>
              <h2>Documents</h2>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Document</th>
                    <th>Study type</th>
                    <th>Area</th>
                    <th>Region</th>
                    <th>State</th>
                    <th className="num">Chunks</th>
                  </tr>
                </thead>
                <tbody>
                  {data.documents.map((d) => (
                    <tr key={d.document_uri}>
                      <td>
                        <Link to={`/documents/${encodeURIComponent(d.document_uri)}`}>{d.title || d.source_filename}</Link>
                        <div className="doc-sub">{d.document_uri}</div>
                      </td>
                      <td>{d.study_type}</td>
                      <td>{d.therapeutic_area}</td>
                      <td>{d.geography}</td>
                      <td>{d.state}</td>
                      <td className="num">{d.chunk_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
