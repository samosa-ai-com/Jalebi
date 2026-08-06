import { useEffect, useState } from "react";

interface HealthResponse {
  status: string;
}

function App() {
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/health")
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: HealthResponse) => {
        if (!cancelled) setStatus(data.status);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center bg-neutral-950 text-neutral-100">
      <div className="text-center">
        <h1 className="text-4xl font-bold">Jalebi</h1>
        <p className="mt-2 text-neutral-400">Coding-agent dashboard</p>
        <p className="mt-4" data-testid="server-status">
          {error ? `Server unreachable: ${error}` : status ? `Server: ${status}` : "Connecting…"}
        </p>
      </div>
    </div>
  );
}

export default App;
