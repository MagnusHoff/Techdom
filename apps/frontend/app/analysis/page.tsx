"use client";

/* eslint-disable @next/next/no-img-element */

import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useMemo, useRef, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import { getJobStatus, runAnalysis, startAnalysisJob } from "@/lib/api";
import type { AnalysisPayload, AnalysisResponse, DecisionUi, JobStatus } from "@/lib/types";

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
};

const JOB_POLL_INTERVAL = 2_500;

function extractFinnkode(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }
  if (/^\d{6,}$/.test(trimmed)) {
    return trimmed;
  }

  try {
    const url = new URL(normaliseListingUrl(trimmed));
    const param = url.searchParams.get("finnkode") ?? url.searchParams.get("finnCode");
    if (param) {
      const match = param.match(/\d{6,}/);
      if (match) {
        return match[0];
      }
    }
    const pathMatch = url.pathname.match(/(\d{6,})/);
    if (pathMatch) {
      return pathMatch[1];
    }
  } catch {
    /* ignore invalid URL */
  }

  const fallback = trimmed.match(/(\d{6,})/);
  return fallback ? fallback[1] : null;
}

function parseNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const normalised = value.replace(/[\s\u00a0\u202f]/g, "").replace(/,/g, ".");
    if (!normalised) {
      return null;
    }
    const parsed = Number(normalised);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatNumberWithSpaces(value: number): string {
  return new Intl.NumberFormat("nb-NO")
    .format(Math.round(value))
    .replace(/\u00a0|\u202f/g, " ");
}

function formatCurrency(value: unknown): string {
  const parsed = parseNumber(value);
  if (parsed === null) {
    return "";
  }
  if (parsed === 0) {
    return "0";
  }
  return formatNumberWithSpaces(parsed);
}

function formatFloat(value: unknown): string {
  const parsed = parseNumber(value);
  if (parsed === null) {
    return "";
  }
  const rounded = Math.round(parsed * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : (rounded.toFixed(2).replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1"));
}

function formatInteger(value: unknown): string {
  const parsed = parseNumber(value);
  if (parsed === null) {
    return "";
  }
  return String(Math.round(parsed));
}

function buildFormFromParams(params: Record<string, unknown> | null | undefined): AnalysisPayload {
  return {
    price: params ? formatCurrency(params.price) || DEFAULT_FORM.price : DEFAULT_FORM.price,
    equity: params ? formatCurrency(params.equity) || DEFAULT_FORM.equity : DEFAULT_FORM.equity,
    interest: params ? formatFloat(params.interest) || DEFAULT_FORM.interest : DEFAULT_FORM.interest,
    term_years: params ? formatInteger(params.term_years) || DEFAULT_FORM.term_years : DEFAULT_FORM.term_years,
    rent: params ? formatCurrency(params.rent) || DEFAULT_FORM.rent : DEFAULT_FORM.rent,
    hoa: params ? formatCurrency(params.hoa) || DEFAULT_FORM.hoa : DEFAULT_FORM.hoa,
    maint_pct: params ? formatFloat(params.maint_pct) || DEFAULT_FORM.maint_pct : DEFAULT_FORM.maint_pct,
    vacancy_pct: params ? formatFloat(params.vacancy_pct) || DEFAULT_FORM.vacancy_pct : DEFAULT_FORM.vacancy_pct,
    other_costs: params ? formatCurrency(params.other_costs) || DEFAULT_FORM.other_costs : DEFAULT_FORM.other_costs,
  };
}

function stringOrNull(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  return null;
}

function extractAnalysisParams(job: JobStatus | null): Record<string, unknown> | null {
  if (!job) {
    return null;
  }
  const resultParams = job.result?.analysis?.input_params;
  if (resultParams && typeof resultParams === "object") {
    return resultParams as Record<string, unknown>;
  }
  const artifacts = job.artifacts && typeof job.artifacts === "object" ? (job.artifacts as Record<string, unknown>) : null;
  const artifactParams = artifacts?.analysis_params;
  if (artifactParams && typeof artifactParams === "object") {
    return artifactParams as Record<string, unknown>;
  }
  return null;
}

function extractListingInfo(job: JobStatus | null): Record<string, unknown> | null {
  if (!job) {
    return null;
  }
  const resultListing = job.result?.listing;
  if (resultListing && typeof resultListing === "object") {
    return resultListing as Record<string, unknown>;
  }
  const artifacts = job.artifacts && typeof job.artifacts === "object" ? (job.artifacts as Record<string, unknown>) : null;
  const artifactListing = artifacts?.listing;
  if (artifactListing && typeof artifactListing === "object") {
    return artifactListing as Record<string, unknown>;
  }
  return null;
}

function pickListingImage(listing: Record<string, unknown>): string | null {
  const direct = stringOrNull(listing.image ?? listing.image_url ?? listing.cover_image ?? listing.main_image ?? listing.primary_image);
  if (direct) {
    return direct;
  }
  const images = listing.images;
  if (Array.isArray(images)) {
    for (const entry of images) {
      if (typeof entry === "string") {
        const candidate = stringOrNull(entry);
        if (candidate) {
          return candidate;
        }
      } else if (entry && typeof entry === "object") {
        const record = entry as Record<string, unknown>;
        const candidate = stringOrNull(record.url ?? record.large ?? record.src);
        if (candidate) {
          return candidate;
        }
      }
    }
  }
  return null;
}

function pickListingTitle(listing: Record<string, unknown>): string | null {
  const keys = ["heading", "title", "name", "summary", "ad_heading"] as const;
  for (const key of keys) {
    const candidate = stringOrNull(listing[key]);
    if (candidate) {
      return candidate;
    }
  }
  return null;
}

function pickListingAddress(listing: Record<string, unknown>): string | null {
  const directKeys = ["address", "full_address", "location", "address_text", "short_address", "display_address"] as const;
  for (const key of directKeys) {
    const candidate = stringOrNull(listing[key]);
    if (candidate) {
      return candidate;
    }
  }
  const addr = listing.address;
  if (addr && typeof addr === "object") {
    const record = addr as Record<string, unknown>;
    const parts = [
      stringOrNull(record.streetAddress ?? record.street ?? record.street_name),
      stringOrNull(record.postalCode ?? record.postal_code ?? record.zip),
      stringOrNull(record.addressLocality ?? record.city ?? record.municipality),
    ].filter(Boolean) as string[];
    if (parts.length) {
      return parts.join(", ");
    }
  }
  return null;
}

function jobStatusLabel(status: string | undefined): string {
  switch ((status ?? "").toLowerCase()) {
    case "queued":
      return "I kø";
    case "running":
      return "Henter data";
    case "done":
      return "Ferdig";
    case "failed":
      return "Feilet";
    default:
      return status ?? "Ukjent";
  }
}

function normaliseListingUrl(value: string): string {
  if (!value) {
    return "";
  }
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
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

function AnalysisPageContent() {
  const params = useSearchParams();
  const listing = params.get("listing") ?? "";

  const listingUrl = normaliseListingUrl(listing);

  const [form, setForm] = useState<AnalysisPayload>({ ...DEFAULT_FORM });
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [previewTitle, setPreviewTitle] = useState<string | null>(null);
  const [previewAddress, setPreviewAddress] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [jobStarting, setJobStarting] = useState(false);
  const jobListingRef = useRef<string | null>(null);
  const jobAppliedRef = useRef<string | null>(null);

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

  const jobInProgress = jobStarting || (jobStatus ? !["done", "failed"].includes((jobStatus.status ?? "").toLowerCase()) : false);
  const submitDisabled = analyzing || jobInProgress;
  const submitLabel = jobInProgress ? "Henter data..." : analyzing ? "Beregner..." : "Kjør analyse";

  const handleChange = (field: keyof AnalysisPayload) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setForm((prev) => ({ ...prev, [field]: event.target.value }));
    };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAnalyzing(true);
    setError(null);
    setResult(null);

    const payload: AnalysisPayload = {
      ...form,
    };

    try {
      const analysis = await runAnalysis(payload);
      setResult(analysis);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Klarte ikke å hente analyse.");
    } finally {
      setAnalyzing(false);
    }
  };

  useEffect(() => {
    const trimmed = listing.trim();
    if (!trimmed) {
      jobListingRef.current = null;
      jobAppliedRef.current = null;
      setJobId(null);
      setJobStatus(null);
      setJobError(null);
      return;
    }

    const normalised = normaliseListingUrl(trimmed);
    if (jobListingRef.current === normalised) {
      return;
    }

    const finnkode = extractFinnkode(trimmed);
    jobListingRef.current = normalised;
    jobAppliedRef.current = null;

    setForm({ ...DEFAULT_FORM });
    setResult(null);
    setError(null);
    setJobStatus(null);
    setJobId(null);
    setJobError(null);
    setPreviewImage(null);
    setPreviewTitle(null);
    setPreviewAddress(null);
    setPreviewError(null);

    if (!finnkode) {
      setJobError("Fant ikke FINN-kode i lenken.");
      return;
    }

    let cancelled = false;
    setJobStarting(true);

    (async () => {
      try {
        const job = await startAnalysisJob(finnkode);
        if (cancelled) {
          return;
        }
        setJobId(job.job_id);
        setJobStatus({ id: job.job_id, status: job.status ?? "queued", finnkode });
      } catch (err) {
        if (!cancelled) {
          setJobError(err instanceof Error ? err.message : "Kunne ikke starte analysen.");
        }
      } finally {
        if (!cancelled) {
          setJobStarting(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [listing]);

  useEffect(() => {
    if (!listing) {
      setPreviewImage(null);
      setPreviewTitle(null);
      setPreviewError(null);
      setPreviewAddress(null);
      return;
    }

    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);

    fetch(`/api/listing-preview?url=${encodeURIComponent(listing)}`)
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        return res.json() as Promise<{ image?: string | null; title?: string | null; address?: string | null }>;
      })
      .then((data) => {
        if (cancelled) {
          return;
        }
        const imageValue = typeof data?.image === "string" && data.image ? data.image : null;
        const titleValue = typeof data?.title === "string" && data.title ? data.title : null;
        const addressValue = typeof data?.address === "string" && data.address ? data.address : null;
        setPreviewImage(imageValue);
        setPreviewTitle(titleValue);
        setPreviewAddress(addressValue);
        if (!imageValue) {
          setPreviewError("Fant ikke bilde for denne annonsen.");
        }
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setPreviewImage(null);
        setPreviewTitle(null);
        setPreviewAddress(null);
        setPreviewError("Kunne ikke hente bilde fra annonsen.");
      })
      .finally(() => {
        if (!cancelled) {
          setPreviewLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [listing]);

  useEffect(() => {
    if (!jobId) {
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const next = await getJobStatus(jobId);
        if (cancelled) {
          return;
        }
        setJobStatus(next);
        if (next.status === "failed") {
          setJobError(next.message ?? next.error ?? "Analysen feilet.");
          return;
        }
        setJobError(null);
        if (next.status === "done") {
          return;
        }
        timer = setTimeout(poll, JOB_POLL_INTERVAL);
      } catch (err) {
        if (cancelled) {
          return;
        }
        setJobError(err instanceof Error ? err.message : "Klarte ikke å hente jobbstatus.");
        timer = setTimeout(poll, JOB_POLL_INTERVAL * 2);
      }
    };

    poll();

    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [jobId]);

  useEffect(() => {
    if (!jobStatus || !jobId) {
      return;
    }

    const statusKey = (jobStatus.status ?? "").toLowerCase();
    const listingInfo = extractListingInfo(jobStatus);

    if (statusKey === "done" && jobAppliedRef.current !== jobId) {
      const paramsFromJob = extractAnalysisParams(jobStatus);
      if (paramsFromJob) {
        setForm(buildFormFromParams(paramsFromJob));
      }
      if (jobStatus.result?.analysis) {
        setResult(jobStatus.result.analysis);
      }
      if (listingInfo) {
        const imageValue = pickListingImage(listingInfo);
        if (imageValue) {
          setPreviewImage(imageValue);
          setPreviewError(null);
        }
        const addressValue = pickListingAddress(listingInfo);
        if (addressValue) {
          setPreviewAddress(addressValue);
        }
        const titleValue = pickListingTitle(listingInfo);
        if (titleValue) {
          setPreviewTitle(titleValue);
        }
      }
      setPreviewLoading(false);
      jobAppliedRef.current = jobId;
    }

    if (statusKey === "failed" && jobAppliedRef.current !== `${jobId}:failed`) {
      const paramsFromJob = extractAnalysisParams(jobStatus);
      if (paramsFromJob) {
        setForm(buildFormFromParams(paramsFromJob));
      }
      if (jobStatus.result?.analysis) {
        setResult(jobStatus.result.analysis);
      }
      if (listingInfo) {
        const addressValue = pickListingAddress(listingInfo);
        if (addressValue) {
          setPreviewAddress(addressValue);
        }
        const titleValue = pickListingTitle(listingInfo);
        if (titleValue) {
          setPreviewTitle(titleValue);
        }
        const imageValue = pickListingImage(listingInfo);
        if (imageValue) {
          setPreviewImage(imageValue);
          setPreviewError(null);
        }
      }
      setPreviewLoading(false);
      jobAppliedRef.current = `${jobId}:failed`;
    }
  }, [jobStatus, jobId]);

  return (
    <>
      <section className="analysis-inputs">
        <ListingPreviewCard
          listingUrl={listingUrl}
          imageUrl={previewImage}
          listingTitle={previewTitle}
          listingAddress={previewAddress}
          loading={previewLoading}
          error={previewError}
        />

        <section className="analysis-form-card">
          <JobStatusCard status={jobStatus} jobError={jobError} starting={jobStarting} />
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

            <div className="analysis-actions">
              <button type="submit" className="analysis-button" disabled={submitDisabled}>
                {submitLabel}
              </button>
            </div>
          </form>
          {error ? <div className="error-banner">{error}</div> : null}
        </section>
      </section>

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
          {decisionUi?.dom_notat ? <p className="status-note">{decisionUi.dom_notat}</p> : null}

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
    </>
  );
}

export default function AnalysisPage() {
  return (
    <main className="page-gradient">
      <PageContainer>
        <SiteHeader showAction actionHref="/" />
        <Suspense
          fallback={
            <section className="analysis-hero">
              <p className="lede">Laster analyse …</p>
            </section>
          }
        >
          <AnalysisPageContent />
        </Suspense>
        <SiteFooter />
      </PageContainer>
    </main>
  );
}

interface ListingPreviewCardProps {
  listingUrl: string;
  listingTitle: string | null;
  listingAddress: string | null;
  imageUrl: string | null;
  loading: boolean;
  error: string | null;
}

interface JobStatusCardProps {
  status: JobStatus | null;
  jobError: string | null;
  starting: boolean;
}

function JobStatusCard({ status, jobError, starting }: JobStatusCardProps) {
  if (!status && !starting && !jobError) {
    return null;
  }

  const stateKey = status?.status ?? (jobError ? "failed" : starting ? "queued" : undefined);
  const label = jobStatusLabel(stateKey);
  const progress = typeof status?.progress === "number" ? Math.round(status.progress) : null;
  const effectiveMessage = stringOrNull(status?.message) ?? (stateKey === "failed" ? stringOrNull(status?.error) : null) ?? jobError;
  const pdfUrl = stringOrNull(status?.pdf_url);
  const finnkode = stringOrNull(status?.finnkode);
  const showProgress = progress !== null && stateKey !== "done" && stateKey !== "failed";

  return (
    <div className="job-card">
      <p className="job-label">Automatisk innhenting</p>
      <p className="job-value">{label}</p>
      {finnkode ? <p className="job-message">FINN-kode: {finnkode}</p> : null}
      {showProgress ? <p className="progress">Fremdrift: {progress}%</p> : null}
      {effectiveMessage ? <p className="job-message">{effectiveMessage}</p> : null}
      {pdfUrl ? (
        <p className="job-message">
          Prospekt: <a href={pdfUrl} target="_blank" rel="noreferrer">last ned</a>
        </p>
      ) : null}
    </div>
  );
}

function ListingPreviewCard({ listingUrl, listingTitle, listingAddress, imageUrl, loading, error }: ListingPreviewCardProps) {
  const hasListing = Boolean(listingUrl);
  const heading = (() => {
    const trimmedAddress = listingAddress?.trim();
    if (trimmedAddress) {
      return trimmedAddress;
    }
    const trimmedTitle = listingTitle?.trim();
    if (trimmedTitle) {
      return trimmedTitle;
    }
    if (!hasListing) {
      return null;
    }
    try {
      const url = new URL(listingUrl);
      const pathname = decodeURIComponent(url.pathname.replace(/\/+$/, ""));
      const segments = pathname.split("/").filter(Boolean);
      if (segments.length > 0 && !segments[segments.length - 1].includes(".")) {
        return segments.join(" ");
      }
      return url.hostname;
    } catch {
      return listingUrl.replace(/^https?:\/\//i, "");
    }
  })();
  const srStatus = (() => {
    if (loading) {
      return "Laster forhåndsvisning fra FINN";
    }
    if (!hasListing) {
      return "Ingen FINN-lenke valgt";
    }
    if (!imageUrl) {
      return error ?? "Fant ikke bilde for denne annonsen";
    }
    return listingAddress ?? listingTitle ?? "Bilde fra FINN-annonsen";
  })();

  return (
    <aside className="listing-preview-card">
      {heading ? <h2 className="preview-heading">{heading}</h2> : null}
      <div className="preview-frame">
        {imageUrl ? (
          <img src={imageUrl} alt="Første bilde fra FINN-annonsen" className="listing-image" />
        ) : (
          <div className="listing-placeholder">
            <span>{srStatus}</span>
          </div>
        )}
      </div>
      {imageUrl ? <span className="sr-only">{srStatus}</span> : null}
    </aside>
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
