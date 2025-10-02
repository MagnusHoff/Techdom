"use client";

import { useSearchParams } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";

import { runAnalysis } from "@/lib/api";
import type { AnalysisPayload, AnalysisResponse, DecisionUi } from "@/lib/types";

const DEFAULT_FORM: AnalysisPayload = {
  price: "",
  equity: "",
  interest: "5.0",
  term_years: "25",
  rent: "",
  hoa: "",
  maint_pct: "6.0",
  vacancy_pct: "0",
  other_costs: "0",
  tg2_items: [],
  tg3_items: [],
};

function parseTextList(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function colorClass(farge?: string): string {
  switch ((farge ?? "").toLowerCase()) {
    case "red":
      return "score-chip red";
    case "orange":
      return "score-chip orange";
    case "green":
      return "score-chip green";
    default:
      return "score-chip neutral";
  }
}

function keyColorClass(farge?: string): string {
  switch ((farge ?? "").toLowerCase()) {
    case "red":
      return "key-value red";
    case "orange":
      return "key-value orange";
    case "green":
      return "key-value green";
    default:
      return "key-value neutral";
  }
}

export default function AnalysisPage() {
  const params = useSearchParams();
  const listing = params.get("listing") ?? "";

  const [form, setForm] = useState<AnalysisPayload>({ ...DEFAULT_FORM });
  const [tg2Text, setTg2Text] = useState("");
  const [tg3Text, setTg3Text] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalysisResponse | null>(null);

  const decisionUi: DecisionUi | null = result?.decision_ui ?? null;

  const scoreValue = useMemo(() => {
    if (!decisionUi) return null;
    const scoreGauge = decisionUi.scorelinjal;
    const statusScore = decisionUi.status?.score;
    const gaugeValue = typeof scoreGauge?.value === "number" ? scoreGauge.value : undefined;
    return (gaugeValue ?? statusScore) ?? null;
  }, [decisionUi]);

  const scoreColor = colorClass(decisionUi?.scorelinjal?.farge);
  const domLabel = decisionUi?.status?.dom ?? "";
  const statusSentence = decisionUi?.status?.setning ?? "";

  const handleChange = (field: keyof AnalysisPayload) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setForm((prev) => ({ ...prev, [field]: event.target.value }));
    };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);

    const payload: AnalysisPayload = {
      ...form,
      tg2_items: parseTextList(tg2Text),
      tg3_items: parseTextList(tg3Text),
      tg_data_available: Boolean(tg2Text.trim() || tg3Text.trim()),
    };

    try {
      const analysis = await runAnalysis(payload);
      setResult(analysis);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Klarte ikke å hente analyse.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="analysis-main">
      <section className="analysis-shell">
        <header className="analysis-header">
          <div>
            <p className="overline">SaaS beta</p>
            <h1>Analyse av {listing || "eiendom"}</h1>
            <p className="lede">
              Fyll inn tallene (midlertidig manuelt) og send til Techdom-API-et.
              Når scraping-API-et er klart fylles dette automatisk.
            </p>
          </div>
        </header>

        <form className="analysis-form" onSubmit={handleSubmit}>
          <div className="form-grid">
            <FormField
              label="Kjøpesum"
              value={form.price}
              placeholder="4 500 000"
              onChange={handleChange("price")}
            />
            <FormField
              label="Egenkapital"
              value={form.equity}
              placeholder="675 000"
              onChange={handleChange("equity")}
            />
            <FormField
              label="Rente % p.a."
              value={form.interest}
              placeholder="5.10"
              onChange={handleChange("interest")}
            />
            <FormField
              label="Lånetid (år)"
              value={form.term_years}
              placeholder="30"
              onChange={handleChange("term_years")}
            />
            <FormField
              label="Leie (mnd)"
              value={form.rent}
              placeholder="18 000"
              onChange={handleChange("rent")}
            />
            <FormField
              label="Felleskost (mnd)"
              value={form.hoa}
              placeholder="3 000"
              onChange={handleChange("hoa")}
            />
            <FormField
              label="Vedlikehold % av leie"
              value={form.maint_pct}
              placeholder="6.0"
              onChange={handleChange("maint_pct")}
            />
            <FormField
              label="Andre kost (mnd)"
              value={form.other_costs}
              placeholder="800"
              onChange={handleChange("other_costs")}
            />
            <FormField
              label="Ledighet %"
              value={form.vacancy_pct}
              placeholder="0.0"
              onChange={handleChange("vacancy_pct")}
            />
          </div>

          <div className="textareas">
            <TextAreaField
              label="TG2 (én per linje)"
              placeholder="TG2 Bad: Ventilasjon"
              value={tg2Text}
              onChange={(event) => setTg2Text(event.target.value)}
            />
            <TextAreaField
              label="TG3 (én per linje)"
              placeholder="TG3 Tak: Krever utskiftning"
              value={tg3Text}
              onChange={(event) => setTg3Text(event.target.value)}
            />
          </div>

          <button type="submit" className="analysis-button" disabled={loading}>
            {loading ? "Beregner..." : "Kjør analyse"}
          </button>
        </form>

        {error ? <div className="error-banner">{error}</div> : null}

        {result ? (
          <section className="analysis-results">
            <div className="score-card">
              <div>
                <p className="overline">Total score</p>
                <div className="score-value">{scoreValue ?? "-"}</div>
              </div>
              <span className={scoreColor}>{domLabel || "N/A"}</span>
            </div>

            {statusSentence ? <p className="status-sentence">{statusSentence}</p> : null}
            {decisionUi?.dom_notat ? (
              <p className="status-note">{decisionUi.dom_notat}</p>
            ) : null}

            <div className="key-grid">
              {(decisionUi?.nokkel_tall ?? []).map((item, index) => {
                const navn = typeof item.navn === "string" ? item.navn : "";
                const verdi = typeof item.verdi === "string" ? item.verdi : String(item.verdi ?? "");
                const farge = typeof item.farge === "string" ? item.farge : undefined;
                return (
                  <div className="key-card" key={`${navn}-${index}`}>
                    <p className="key-name">{navn}</p>
                    <p className={keyColorClass(farge)}>{verdi}</p>
                  </div>
                );
              })}
            </div>

            <div className="list-grid">
              <DecisionList
                title="🔧 Tiltak"
                items={decisionUi?.tiltak ?? []}
                empty="Ingen tiltak anbefalt."
              />
              <DecisionList
                title="✅ Det som er bra"
                items={decisionUi?.positivt ?? []}
                empty="Ingen positive funn ennå."
              />
              <DecisionList
                title="⚠️ Risiko"
                items={decisionUi?.risiko ?? []}
                empty="Ingen risikopunkter registrert."
              />
            </div>

            <article className="ai-copy">
              <h2>AI-oppsummering</h2>
              <p>{result.ai_text}</p>
            </article>
          </section>
        ) : null}
      </section>
    </main>
  );
}

interface FormFieldProps {
  label: string;
  value: string;
  placeholder?: string;
  onChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
}

function FormField({ label, value, placeholder, onChange }: FormFieldProps) {
  return (
    <label className="form-field">
      <span>{label}</span>
      <input value={value} placeholder={placeholder} onChange={onChange} />
    </label>
  );
}

interface TextAreaFieldProps {
  label: string;
  placeholder?: string;
  value: string;
  onChange: (event: React.ChangeEvent<HTMLTextAreaElement>) => void;
}

function TextAreaField({ label, placeholder, value, onChange }: TextAreaFieldProps) {
  return (
    <label className="textarea-field">
      <span>{label}</span>
      <textarea value={value} placeholder={placeholder} onChange={onChange} rows={6} />
    </label>
  );
}

interface DecisionListProps {
  title: string;
  items: string[];
  empty: string;
}

function DecisionList({ title, items, empty }: DecisionListProps) {
  const hasItems = items.length > 0;
  return (
    <div className="decision-card">
      <h3>{title}</h3>
      {hasItems ? (
        <ul>
          {items.slice(0, 6).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="placeholder">{empty}</p>
      )}
    </div>
  );
}
