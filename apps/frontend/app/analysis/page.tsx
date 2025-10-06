"use client";

/* eslint-disable @next/next/no-img-element */

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import { getJobStatus, runAnalysis, startAnalysisJob } from "@/lib/api";
import type {
  AnalysisPayload,
  AnalysisResponse,
  DecisionUi,
  JobStatus,
  ProspectusExtract,
} from "@/lib/types";

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

function jobStatusHeadline(status: JobStatus | null, stateKey: string | undefined): string {
  const key = (stateKey ?? "").toLowerCase();
  const message = stringOrNull(status?.message);
  switch (key) {
    case "queued":
      return "Forbereder analyse";
    case "running":
      return message ?? "Automatisk innhenting pågår";
    case "done":
      return "Analyse fullført";
    case "failed":
      return "Analysen feilet";
    default:
      return jobStatusLabel(key);
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
    case "yellow":
      return "score-chip yellow";
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
    case "yellow":
      return "key-value orange";
    case "green":
      return "key-value green";
    default:
      return "key-value neutral";
  }
}

function scoreFillColor(percent: number | null): string {
  if (percent === null) {
    return "rgba(148, 163, 184, 0.35)";
  }
  if (percent < 16) {
    return "#7f1d1d"; // mørkerød
  }
  if (percent < 32) {
    return "#dc2626"; // rødt
  }
  if (percent < 50) {
    return "#f97316"; // oransje
  }
  if (percent < 66) {
    return "#a3e635"; // grønn-oransje (lime)
  }
  if (percent < 84) {
    return "#22c55e"; // grønn
  }
  return "#14532d"; // mørkegrønn
}

function AnalysisPageContent() {
  const router = useRouter();
  const params = useSearchParams();
  const listing = params.get("listing") ?? "";
  const runToken = params.get("run") ?? "";

  const listingUrl = normaliseListingUrl(listing);

  const [form, setForm] = useState<AnalysisPayload>({ ...DEFAULT_FORM });
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [prospectus, setProspectus] = useState<ProspectusExtract | null>(null);
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
  const skipJobInitRef = useRef(process.env.NODE_ENV !== "production");

  useEffect(() => {
    if (runToken) {
      return;
    }
    const token = Date.now().toString(36);
    const paramsCopy = new URLSearchParams(Array.from(params.entries()));
    paramsCopy.set("run", token);
    router.replace(`?${paramsCopy.toString()}`);
  }, [runToken, params, router]);

  const decisionUi: DecisionUi | null = result?.decision_ui ?? null;

  const scoreValue = useMemo(() => {
    if (!decisionUi) return null;
    const scoreGauge = decisionUi.scorelinjal;
    const statusScore = decisionUi.status?.score;
    const gaugeValue = typeof scoreGauge?.value === "number" ? scoreGauge.value : undefined;
    return (gaugeValue ?? statusScore) ?? null;
  }, [decisionUi]);

  const scorePercent = useMemo(() => {
    const gauge = decisionUi?.scorelinjal;
    if (gauge && typeof gauge.value === "number" && Number.isFinite(gauge.value)) {
      return Math.max(0, Math.min(100, gauge.value));
    }
    if (typeof scoreValue === "number" && Number.isFinite(scoreValue)) {
      return Math.max(0, Math.min(100, scoreValue));
    }
    return null;
  }, [decisionUi, scoreValue]);

  const scoreColor = colorClass(decisionUi?.scorelinjal?.farge);
  const domLabel = decisionUi?.status?.dom ?? "";
  const statusSentence = decisionUi?.status?.setning ?? "";
  const tg2Items = useMemo(() => prospectus?.tg2 ?? [], [prospectus]);
  const tg3Items = useMemo(() => prospectus?.tg3 ?? [], [prospectus]);
  const tgDataAvailable = tg2Items.length > 0 || tg3Items.length > 0;
  const scoreBreakdownEntries = useMemo(() => {
    const entries = Array.isArray(decisionUi?.score_breakdown)
      ? decisionUi.score_breakdown
      : [];
    const defaults = [
      { id: "econ", label: "Økonomi" },
      { id: "tr", label: "Tilstand" },
    ];
    return defaults.map((template) => {
      const match = entries.find((entry) => entry && entry.id === template.id);
      const rawValue = typeof match?.value === "number" ? match.value : null;
      const value = rawValue === null ? null : Math.max(0, Math.min(100, Math.round(rawValue)));
      return {
        id: template.id,
        label: match?.label ?? template.label,
        value,
      };
    });
  }, [decisionUi]);
  const scoreFillStyle = useMemo(() => {
    const percentValue = scorePercent ?? 0;
    return {
      width: `${percentValue}%`,
      "--score-fill-color": scoreFillColor(scorePercent),
    } as CSSProperties;
  }, [scorePercent]);

  const jobInProgress = jobStarting || (jobStatus ? !["done", "failed"].includes((jobStatus.status ?? "").toLowerCase()) : false);
  const submitDisabled = analyzing || jobInProgress;
  const fieldsDisabled = submitDisabled;
  const submitLabel = analyzing ? "Oppdaterer..." : "Oppdater";
  const resourcePdfUrl = stringOrNull(jobStatus?.pdf_url);
  const resourceListingUrl = useMemo(() => {
    const candidate = stringOrNull(listingUrl);
    if (!candidate) {
      return null;
    }
    try {
      return new URL(candidate).toString();
    } catch {
      return null;
    }
  }, [listingUrl]);

  const handleChange = (field: keyof AnalysisPayload) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setForm((prev) => ({ ...prev, [field]: event.target.value }));
    };

  const jobCompleted = useMemo(() => Boolean(result), [result]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAnalyzing(true);
    setError(null);

    const payload: AnalysisPayload = {
      ...form,
      tg2_items: tg2Items,
      tg3_items: tg3Items,
      tg_data_available: tgDataAvailable,
      upgrades: prospectus?.upgrades ?? [],
      warnings: prospectus?.watchouts ?? [],
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
    if (skipJobInitRef.current) {
      skipJobInitRef.current = false;
      return;
    }
    const trimmed = listing.trim();
    if (!runToken) {
      return;
    }
    // eslint-disable-next-line no-console
    console.log("listing effect", { listing, trimmed, runToken });
    if (!trimmed) {
      jobListingRef.current = null;
      jobAppliedRef.current = null;
      setJobId(null);
      setJobStatus(null);
      setJobError(null);
      return;
    }

    const normalised = normaliseListingUrl(trimmed);
    const key = normalised ? `${normalised}::${runToken}` : runToken ? `::${runToken}` : normalised;
    if (jobListingRef.current === key) {
      return;
    }

    const finnkode = extractFinnkode(trimmed);
    // eslint-disable-next-line no-console
    console.log("job key", key);
    jobListingRef.current = key;
    jobAppliedRef.current = null;

    setForm({ ...DEFAULT_FORM });
    setResult(null);
    setError(null);
    setJobStatus(null);
    setJobId(null);
    setJobError(null);
    setProspectus(null);
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
      // eslint-disable-next-line no-console
      console.log("starting job", { finnkode, runToken });
      try {
        const job = await startAnalysisJob(finnkode);
        if (cancelled) {
          return;
        }
        // eslint-disable-next-line no-console
        console.log("job created", job.job_id, job.status);
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
      if (jobListingRef.current === key) {
        jobListingRef.current = null;
      }
    };
  }, [listing, runToken]);

  useEffect(() => {
    if (!listing) {
      setPreviewImage(null);
      setPreviewTitle(null);
      setPreviewError(null);
      setPreviewAddress(null);
      return;
    }

    // eslint-disable-next-line no-console
    console.log("preview effect", listing);

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

    // eslint-disable-next-line no-console
    console.log("jobId effect start", jobId);

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        // eslint-disable-next-line no-console
        console.log("polling job", jobId);
        const next = await getJobStatus(jobId);
        if (cancelled) {
          return;
        }
        // eslint-disable-next-line no-console
        console.log("job status", jobId, next.status);
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
        // eslint-disable-next-line no-console
        console.error("job status poll failed", jobId, err);
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
      const prospectusExtract = extractProspectusFromJob(jobStatus);
      setProspectus(prospectusExtract);
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
      const prospectusExtract = extractProspectusFromJob(jobStatus);
      setProspectus(prospectusExtract);
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
          statusCard={
            analyzing ? (
              <AnalysisUpdateCard />
            ) : (
              <JobStatusCard
                status={jobStatus}
                jobError={jobError}
                starting={jobStarting}
                completed={jobCompleted}
              />
            )
          }
        />

        <section className="analysis-form-card">
          <div className="form-card-header">
            <ResourceLinkGroup pdfUrl={resourcePdfUrl} listingUrl={resourceListingUrl} />
          </div>
          <form className="analysis-form" onSubmit={handleSubmit}>
            <div className="form-grid">
              <FormField
                label="Kjøpesum"
                value={form.price}
                placeholder="4 500 000"
                onChange={handleChange("price")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Egenkapital"
                value={form.equity}
                placeholder="675 000"
                onChange={handleChange("equity")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Rente % p.a."
                value={form.interest}
                placeholder="5.10"
                onChange={handleChange("interest")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Lånetid (år)"
                value={form.term_years}
                placeholder="30"
                onChange={handleChange("term_years")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Leie (mnd)"
                value={form.rent}
                placeholder="18 000"
                onChange={handleChange("rent")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Felleskost (mnd)"
                value={form.hoa}
                placeholder="3 000"
                onChange={handleChange("hoa")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Vedlikehold % av leie"
                value={form.maint_pct}
                placeholder="6.0"
                onChange={handleChange("maint_pct")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Andre kost (mnd)"
                value={form.other_costs}
                placeholder="800"
                onChange={handleChange("other_costs")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Ledighet %"
                value={form.vacancy_pct}
                placeholder="0.0"
                onChange={handleChange("vacancy_pct")}
                disabled={fieldsDisabled}
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
          <div className="analysis-results-grid">
            <div className="analysis-score-block">
              <h2 className="analysis-column-title">Resultat – forsterket av OpenAI</h2>
              <div className="score-card">
                <div className="score-card-header">
                  <div>
                    <p className="overline">Total score</p>
                    <div className="score-value">{scoreValue ?? "-"}</div>
                  </div>
                  <span className={scoreColor}>{domLabel || "N/A"}</span>
                </div>
                <div
                  className={`score-progress${scorePercent === 100 ? " complete" : ""}`}
                  role="progressbar"
                  aria-label="Total score"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={scorePercent ?? undefined}
                >
                  <span className="score-progress-fill" style={scoreFillStyle} />
                </div>
                <div className="score-breakdown">
                  {scoreBreakdownEntries.map((entry) => {
                    const percent = entry.value ?? 0;
                    const valueText = entry.value === null ? "–" : `${percent}%`;
                    const fillStyle = {
                      width: `${percent}%`,
                      "--score-fill-color": scoreFillColor(entry.value ?? null),
                    } as CSSProperties;
                    return (
                      <div className="score-breakdown-item" key={entry.id}>
                        <div className="score-breakdown-header">
                          <span className="score-breakdown-label">{entry.label}</span>
                          <span className="score-breakdown-value">{valueText}</span>
                        </div>
                        <div
                          className="score-breakdown-bar"
                          role="progressbar"
                          aria-label={entry.label}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={entry.value ?? undefined}
                        >
                          <span className="score-breakdown-fill" style={fillStyle} />
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="score-card-footer">
                  {statusSentence ? <p className="status-sentence">{statusSentence}</p> : null}
                </div>
              </div>
              {decisionUi?.dom_notat ? <p className="status-note">{decisionUi.dom_notat}</p> : null}
            </div>

            <div className="analysis-score-spacer" aria-hidden="true" />

            <div className="analysis-column analysis-column-economy">
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
              </div>
            </div>

            <div className="analysis-column analysis-column-prospectus">
              {prospectus ? (
                <div className="prospectus-grid">
                  <ProspectusCard
                    title="🛑 TG3 (alvorlig)"
                    badge={{ label: "Høy risiko", tone: "danger" }}
                    items={prospectus.tg3 ?? []}
                    empty="Ingen TG3-punkter funnet."
                  />
                  <ProspectusCard
                    title="🛠️ Tiltak / bør pusses opp"
                    items={prospectus.upgrades ?? []}
                    empty="Ingen oppgraderingsforslag registrert."
                  />
                  <ProspectusCard
                    title="⚠️ TG2"
                    badge={{ label: "Middels risiko", tone: "warn" }}
                    items={prospectus.tg2 ?? []}
                    empty="Ingen TG2-punkter funnet."
                  />
                  <ProspectusCard
                    title="👀 Vær oppmerksom på"
                    items={prospectus.watchouts ?? []}
                    empty="Ingen risikopunkter notert."
                  />
                  <ProspectusCard
                    title="❓ Spørsmål til megler"
                    items={prospectus.questions ?? []}
                    empty="Ingen spørsmål generert."
                    className="prospectus-card-span"
                  />
                </div>
              ) : (
                <div className="prospectus-empty">
                  <p>Ingen salgsoppgave analysert ennå.</p>
                  <p className="prospectus-empty-note">
                    Last opp eller hent salgsoppgaven for å se risiko- og tiltaksvurderinger.
                  </p>
                </div>
              )}
            </div>
          </div>
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
  statusCard?: ReactNode;
}

interface JobStatusCardProps {
  status: JobStatus | null;
  jobError: string | null;
  starting: boolean;
  completed: boolean;
}

function AnalysisUpdateCard() {
  return (
    <div className="job-card" role="status">
      <p className="job-label">Oppdaterer analyse</p>
      <p className="job-value">Beregner på nytt</p>
      <div className="job-progress">
        <span className="job-progress-fill indeterminate" />
      </div>
      <p className="job-message">Oppdaterer økonomitall basert på parametrene.</p>
    </div>
  );
}

function JobStatusCard({ status, jobError, starting, completed }: JobStatusCardProps) {
  if (!status && !starting && !jobError && !completed) {
    return null;
  }

  const derivedState = completed ? "done" : undefined;
  const stateKey = status?.status ?? derivedState ?? (jobError ? "failed" : starting ? "queued" : undefined);
  const label = jobStatusHeadline(status, stateKey);
  const progressValueRaw = typeof status?.progress === "number" ? Math.max(0, Math.min(100, status.progress)) : null;
  const isActive = stateKey !== "failed" && stateKey !== "done";
  const progressValue = isActive ? null : (stateKey === "done" ? 100 : progressValueRaw);
  const showIndeterminate = isActive;
  const showProgress = stateKey !== "failed";
  const failureMessage = stateKey === "failed"
    ? stringOrNull(status?.message) ?? stringOrNull(status?.error) ?? jobError
    : null;

  return (
    <div className="job-card">
      <p className="job-label">Automatisk innhenting</p>
      <p className="job-value">{label}</p>
      {showProgress ? (
        <div
          className={`job-progress${stateKey === "done" ? " complete" : ""}`}
          role="progressbar"
          aria-label="Fremdrift for automatisk innhenting"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={showIndeterminate ? undefined : progressValue ?? undefined}
        >
          <span
            className={`job-progress-fill${showIndeterminate ? " indeterminate" : ""}`}
            style={progressValue !== null ? { width: `${progressValue}%` } : undefined}
          />
        </div>
      ) : null}
      {failureMessage ? <p className="job-message">{failureMessage}</p> : null}
    </div>
  );
}

interface ResourceLinkGroupProps {
  pdfUrl: string | null;
  listingUrl: string | null;
}

function ResourceLinkGroup({ pdfUrl, listingUrl }: ResourceLinkGroupProps) {
  return (
    <div className="resource-links" aria-label="Ressurser">
      {pdfUrl ? (
        <a className="resource-chip" href={pdfUrl} target="_blank" rel="noreferrer">
          Salgsoppgave
        </a>
      ) : (
        <span className="resource-chip disabled" aria-disabled="true">
          Salgsoppgave
        </span>
      )}
      {listingUrl ? (
        <a className="resource-chip" href={listingUrl} target="_blank" rel="noreferrer">
          Annonse
        </a>
      ) : (
        <span className="resource-chip disabled" aria-disabled="true">
          Annonse
        </span>
      )}
      <span className="resource-chip disabled" aria-disabled="true">
        Alle detaljer
      </span>
    </div>
  );
}

function ListingPreviewCard({
  listingUrl,
  listingTitle,
  listingAddress,
  imageUrl,
  loading,
  error,
  statusCard,
}: ListingPreviewCardProps) {
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
      {statusCard ? <div className="listing-status-card">{statusCard}</div> : null}
    </aside>
  );
}

interface FormFieldProps {
  label: string;
  value: string;
  placeholder?: string;
  onChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  disabled?: boolean;
}

function FormField({ label, value, placeholder, onChange, disabled }: FormFieldProps) {
  const fieldClass = disabled ? "form-field form-field-disabled" : "form-field";
  return (
    <label className={fieldClass}>
      <span>{label}</span>
      <input value={value} placeholder={placeholder} onChange={onChange} disabled={disabled} />
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

interface ProspectusCardProps {
  title: string;
  items: string[];
  empty: string;
  badge?: { label: string; tone: "danger" | "warn" | "info" };
  className?: string;
}

function ProspectusCard({ title, items, empty, badge, className }: ProspectusCardProps) {
  const hasItems = items.length > 0;
  const cardClass = className ? `prospectus-card ${className}` : "prospectus-card";
  return (
    <div className={cardClass}>
      <div className="prospectus-card-header">
        <h3>{title}</h3>
        {badge ? <span className={`prospectus-badge ${badge.tone}`}>{badge.label}</span> : null}
      </div>
      {hasItems ? (
        <ul>
          {items.slice(0, 6).map((item, index) => (
            <li key={`${item}-${index}`}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="placeholder">{empty}</p>
      )}
    </div>
  );
}

function toStringArray(value: unknown, limit?: number): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const cleaned = value
    .map((item) => {
      if (typeof item === "string") {
        return item.trim();
      }
      if (item === null || item === undefined) {
        return "";
      }
      return String(item).trim();
    })
    .filter(Boolean);
  if (typeof limit === "number" && limit > 0) {
    return cleaned.slice(0, limit);
  }
  return cleaned;
}

function normaliseProspectusExtract(value: unknown): ProspectusExtract | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const tg3 = toStringArray(record.tg3);
  const tg2 = toStringArray(record.tg2);
  const upgrades = toStringArray(record.upgrades);
  const watchouts = toStringArray(record.watchouts);
  const questions = toStringArray(record.questions);
  const extract: ProspectusExtract = {
    summary_md: typeof record.summary_md === "string" ? record.summary_md : undefined,
    tg3,
    tg2,
    upgrades,
    watchouts,
    questions,
  };
  if (
    !extract.summary_md &&
    tg3.length === 0 &&
    tg2.length === 0 &&
    upgrades.length === 0 &&
    watchouts.length === 0 &&
    questions.length === 0
  ) {
    return null;
  }
  return extract;
}

function extractProspectusFromJob(job: JobStatus | null): ProspectusExtract | null {
  if (!job) {
    return null;
  }
  const resultExtract = job.result?.ai_extract;
  if (resultExtract) {
    const normalised = normaliseProspectusExtract(resultExtract);
    if (normalised) {
      return normalised;
    }
  }
  const artifacts = job.artifacts && typeof job.artifacts === "object" ? (job.artifacts as Record<string, unknown>) : null;
  if (artifacts && "ai_extract" in artifacts) {
    return normaliseProspectusExtract((artifacts as { ai_extract?: unknown }).ai_extract);
  }
  return null;
}
