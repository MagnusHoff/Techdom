"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "./components/chrome";
import { fetchStats } from "@/lib/api";

const INITIAL_ANALYSED_COUNT = 48;

export default function LandingPage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [totalAnalyses, setTotalAnalyses] = useState<number>(INITIAL_ANALYSED_COUNT);

  useEffect(() => {
    let cancelled = false;
    fetchStats()
      .then((stats) => {
        const value = stats?.total_analyses;
        if (!cancelled && typeof value === "number" && value >= 0) {
          setTotalAnalyses(value);
        }
      })
      .catch(() => {
        /* behold fallback */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) {
      setError("Lim inn en FINN-lenke først.");
      return;
    }

    setError(null);
    const encoded = encodeURIComponent(trimmed);
    router.push(`/analysis?listing=${encoded}`);
  };

  return (
    <main className="page-gradient">
      <PageContainer variant="narrow">
        <SiteHeader />

        <section className="landing-section">
          <div className="landing-intro">
            <h1>Lim inn FINN-lenken din</h1>
          </div>

          <form className="landing-form" onSubmit={handleSubmit}>
            <label className="sr-only" htmlFor="listing-url">
              FINN-lenke
            </label>
            <input
              id="listing-url"
              name="listing"
              inputMode="url"
              placeholder="finn.no"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              className="landing-input"
            />
            <button type="submit" className="landing-button">
              Kjør analyse
            </button>
          </form>
          {error ? <p className="error-text">{error}</p> : <div className="error-spacer" />}

          <hr className="divider" />

          <div className="stat-block">
            <span className="stat-label">Eiendommer analysert</span>
            <strong className="stat-value">{totalAnalyses}</strong>
          </div>
        </section>

        <SiteFooter />
      </PageContainer>
    </main>
  );
}
