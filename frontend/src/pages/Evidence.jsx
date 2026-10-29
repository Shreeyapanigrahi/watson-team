import { api } from '../api'
import { Badge, ErrorBox, Loading, Notice, useAsync } from '../ui'

export default function Evidence() {
  const { data, error, loading, reload } = useAsync(() => api.chunking(), [])
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Design evidence</h1>
          <p>
            The brief asks us to justify the chunking strategy with query results, not opinion. This is the comparison we ran on our own
            corpus.
          </p>
        </div>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && (
        <>
          <Notice tone="warn" title="Limits of this evidence">
            Retrieval used {data.retrieval}. Six queries and {data.documents_indexed} documents is a small sample, and the differences are
            a few queries wide. We ran four corpus lengths (see results/) and the winner held, but this should be re-run with embeddings
            in a later step.
          </Notice>
          <div className="card card-flush">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th className="num">Chunks</th>
                    <th className="num">Mean tokens</th>
                    <th className="num">Range</th>
                    <th className="num">Top-1 relevant</th>
                    <th className="num">Top-3 relevant</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.strategies).map(([name, r]) => (
                    <tr key={name}>
                      <td>
                        <strong>{name}</strong>
                        {name === 'A_fixed_256' && (
                          <>
                            {' '}
                            <Badge tone="ok">chosen</Badge>
                          </>
                        )}
                      </td>
                      <td className="num">{r.chunk_count}</td>
                      <td className="num">{r.mean_tokens}</td>
                      <td className="num">
                        {r.min_tokens}-{r.max_tokens}
                      </td>
                      <td className="num">{r.top1_relevant}</td>
                      <td className="num">{r.top3_relevant}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="card card-flush">
            <div style={{ padding: '16px 18px 0' }}>
              <h2>Top result for each query</h2>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Query</th>
                    {Object.keys(data.strategies).map((n) => (
                      <th key={n}>{n}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.values(data.strategies)[0].per_query.map((q, i) => (
                    <tr key={q.query_id}>
                      <td>
                        <strong>{q.query_id}</strong> {q.query}
                      </td>
                      {Object.values(data.strategies).map((s, j) => {
                        const pq = s.per_query[i]
                        return (
                          <td key={j}>
                            <Badge tone={pq.top1_relevant ? 'ok' : 'bad'}>{pq.top1_relevant ? 'relevant' : 'not relevant'}</Badge>
                            <div className="doc-sub">{pq.top1_document}</div>
                          </td>
                        )
                      })}
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
