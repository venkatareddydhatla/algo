import { useEffect, useState } from "react";
import { NewsRow, api } from "../api/client";

export default function NewsPage() {
  const [rows, setRows] = useState<NewsRow[]>([]);
  const [note, setNote] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const data = await api.news();
        setRows(data.items);
        setNote(data.source);
      } catch {
        setNote("API offline — empty feed");
        setRows([]);
      }
    })();
  }, []);

  return (
    <section>
      <div className="section-head">
        <h1>News · sentiment</h1>
        <p className="muted">{note || "Loading…"}</p>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Sent</th>
              <th>Symbols</th>
              <th>Title</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={4} className="muted">
                  No news items yet. Worker RSS poller will populate this.
                </td>
              </tr>
            ) : (
              rows.map((n) => (
                <tr key={n.id}>
                  <td className="mono">{n.score.toFixed(2)}</td>
                  <td>{n.symbols.join(", ") || "—"}</td>
                  <td>{n.title}</td>
                  <td className="muted">{n.source}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
