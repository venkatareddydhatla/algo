import { useEffect, useState } from "react";
import { NewsRow, api } from "../api/client";

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "short",
    });
  } catch {
    return iso;
  }
}

export default function NewsPage() {
  const [rows, setRows] = useState<NewsRow[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.news();
        if (cancelled) return;
        setRows(data.items);
        const badge =
          data.source === "live"
            ? data.items.length
              ? `live · ${data.items.length} headlines`
              : "live · waiting for RSS poll"
            : data.source;
        setNote(badge);
        setError("");
      } catch {
        if (cancelled) return;
        setNote("");
        setError("API offline — cannot load news feed");
        setRows([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section>
      <div className="section-head">
        <h1>News · sentiment</h1>
        <p className="muted">{error || note || "Loading…"}</p>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Sent</th>
              <th>Symbols</th>
              <th>Title</th>
              <th>Source</th>
              <th>Time (IST)</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="muted">
                  {error
                    ? "—"
                    : "No live headlines yet. The API polls RSS on startup (local profile); or start the worker with -WithWorker."}
                </td>
              </tr>
            ) : (
              rows.map((n) => (
                <tr key={n.id}>
                  <td className="mono">{n.score.toFixed(2)}</td>
                  <td>{n.symbols.join(", ") || "—"}</td>
                  <td>{n.title}</td>
                  <td className="muted">{n.source}</td>
                  <td className="muted mono">{fmtTime(n.published_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
