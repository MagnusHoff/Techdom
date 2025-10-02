"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { getJobStatus } from "@/lib/api";
import type { JobStatus } from "@/lib/types";

const POLL_INTERVAL = 2_500;

export default function JobStatusPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = params.jobId;
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const next = await getJobStatus(jobId);
        if (!active) return;
        setStatus(next);
        setError(null);
        if (next.status === "done" || next.status === "failed") {
          return;
        }
        timer = setTimeout(tick, POLL_INTERVAL);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Ukjent feil");
        timer = setTimeout(tick, POLL_INTERVAL * 2);
      }
    }

    tick();

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  return (
    <main className="analysis-main">
      <section className="analysis-shell">
        <header className="analysis-header">
          <p className="overline">Jobbstatus</p>
          <h1>Jobb #{jobId}</h1>
          <p className="lede">Vi henter prospektet og oppdaterer status når jobben er ferdig.</p>
        </header>

        {error ? <div className="error-banner">{error}</div> : null}

        <div className="job-card">
          <p className="job-label">Status</p>
          <p className="job-value">{status?.status ?? "ukjent"}</p>
          {status?.progress !== undefined ? (
            <p className="progress">Fremdrift: {Math.round(status.progress)}%</p>
          ) : null}
          {status?.message ? <p className="job-message">{status.message}</p> : null}
          {status?.pdf_url ? (
            <p className="job-message">
              Prospekt: <a href={status.pdf_url}>last ned</a>
            </p>
          ) : null}
        </div>
      </section>
    </main>
  );
}
