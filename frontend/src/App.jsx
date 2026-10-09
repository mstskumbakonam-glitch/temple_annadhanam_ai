import { useEffect, useState } from "react";
import { api } from "./services/api.js";

// Phase 2 placeholder: reports backend and PostgreSQL status.
// The real dashboard is built in Phase 10.
export default function App() {
  const [backend, setBackend] = useState({ state: "checking" });
  const [database, setDatabase] = useState({ state: "checking" });

  useEffect(() => {
    api
      .health()
      .then((data) => setBackend({ state: "online", data }))
      .catch((err) => setBackend({ state: "offline", message: err.message }));

    api
      .databaseHealth()
      .then((data) => setDatabase({ state: "online", data }))
      .catch((err) =>
        setDatabase({
          state: "offline",
          message: err.body?.detail || err.message,
        })
      );
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 680 }}>
      <h1>Temple Annadhanam Hall - AI CCTV</h1>
      <p>Project scaffold is running. The dashboard arrives in Phase 10.</p>

      <dl>
        <dt style={{ fontWeight: 600 }}>Backend</dt>
        <dd style={{ margin: "0 0 1rem" }}>
          {backend.state === "checking" && "Checking..."}
          {backend.state === "online" &&
            `Online - v${backend.data.version} (${backend.data.environment})`}
          {backend.state === "offline" && `Not reachable - ${backend.message}`}
        </dd>

        <dt style={{ fontWeight: 600 }}>Database</dt>
        <dd style={{ margin: 0 }}>
          {database.state === "checking" && "Checking..."}
          {database.state === "online" &&
            `PostgreSQL ${database.data.server_version} - ${database.data.database_name}`}
          {database.state === "offline" && `Not reachable - ${database.message}`}
        </dd>
      </dl>
    </main>
  );
}
