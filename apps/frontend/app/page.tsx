"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

export default function LandingPage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

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
    <main>
      <section className="landing-card">
        <header>
          <h1>Techdom Boliganalyse</h1>
          <p className="strapline">
            Lim inn FINN-lenken din og få samme vurdering som i Streamlit, nå som SaaS.
          </p>
        </header>
        <form className="landing-form" onSubmit={handleSubmit}>
          <label className="sr-only" htmlFor="listing-url">
            FINN-lenke
          </label>
          <input
            id="listing-url"
            name="listing"
            inputMode="url"
            placeholder="https://www.finn.no/..."
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            className="landing-input"
          />
          <button type="submit" className="landing-button">
            Kjør analyse
          </button>
        </form>
        {error ? <p className="error-text">{error}</p> : null}
      </section>
    </main>
  );
}
