import { useEffect, useState } from "react";
import { api } from "../api/client";

export default function HealthPage() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setData(await api.health());
      } catch {
        setData({
          status: "degraded",
          mode: "dry_run",
          components: [{ name: "api", status: "down", detail: "unreachable" }],
        });
      }
    })();
  }, []);

  const components = (data?.components as Array<Record<string, unknown>>) || [];

  return (
    <section>
      <div className="section-head">
        <h1>Health</h1>
        <p className="muted">
          Overall: <strong>{String(data?.status ?? "…")}</strong> · mode{" "}
          {String(data?.mode ?? "dry_run")} · instrument{" "}
          <strong>{String(data?.instrument ?? "equity")}</strong> dry_run · market data{" "}
          <strong>{String(data?.market_data_source ?? data?.data_source ?? "…")}</strong>
        </p>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Component</th>
              <th>Status</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {components.map((c, i) => (
              <tr key={i}>
                <td className="mono">{String(c.name || c.component)}</td>
                <td>{String(c.status)}</td>
                <td className="muted">{String(c.detail ?? "")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
