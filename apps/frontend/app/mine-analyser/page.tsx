"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import { deleteSavedAnalysis, fetchSavedAnalyses } from "@/lib/api";
import type { StoredAnalysis } from "@/lib/types";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpDown,
  Eye,
  FileDown,
  Filter,
  Search,
  Share2,
  Trash2,
  X,
} from "lucide-react";

const DATE_FORMATTER = new Intl.DateTimeFormat("nb-NO", {
  year: "numeric",
  month: "short",
  day: "numeric",
});

const NOK_FORMATTER = new Intl.NumberFormat("nb-NO", {
  style: "currency",
  currency: "NOK",
  maximumFractionDigits: 0,
});

const ITEMS_PER_PAGE = 8;
const SKELETON_ROWS = 6;

type SortOption = "date_desc" | "score_desc" | "risk_asc";
type RiskFilterValue = "alle" | "low" | "medium" | "high";

interface ScoreTuple {
  economy: number | null;
  condition: number | null;
}

type RiskVariant = "low" | "medium" | "high" | "unknown";

const RISK_FILTER_LABELS: Record<RiskFilterValue, string> = {
  alle: "Alle nivåer",
  low: "Lav risiko",
  medium: "Middels risiko",
  high: "Høy risiko",
};

const SORT_LABELS: Record<SortOption, string> = {
  date_desc: "Nyest først",
  score_desc: "Høyest score",
  risk_asc: "Lavest risiko",
};

function formatDate(value: string | null): string {
  if (!value) {
    return "Ukjent dato";
  }
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return "Ukjent dato";
  }
  return DATE_FORMATTER.format(new Date(timestamp));
}

function formatPrice(value: number | null): string | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return null;
  }
  try {
    return NOK_FORMATTER.format(Math.round(value));
  } catch {
    return `${Math.round(value).toLocaleString("nb-NO")} kr`;
  }
}

function normaliseScore(value: number | null): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round(value)));
}

function getScoreAccent(value: number | null): string {
  const percent = normaliseScore(value);
  const hue = Math.round((percent * 120) / 100);
  return `hsl(${hue}, 82%, 58%)`;
}

function riskRank(label: string | null): number {
  if (!label) {
    return 999;
  }
  const lower = label.toLowerCase();
  if (lower.includes("lav")) {
    return 1;
  }
  if (lower.includes("moderat") || lower.includes("medium") || lower.includes("middels")) {
    return 2;
  }
  if (lower.includes("h\u00f8y") || lower.includes("hoy") || lower.includes("stor")) {
    return 3;
  }
  return 500;
}

function getRiskVariant(label: string | null): RiskVariant {
  if (!label) {
    return "unknown";
  }
  const lower = label.toLowerCase();
  if (lower.includes("lav")) {
    return "low";
  }
  if (lower.includes("moderat") || lower.includes("medium") || lower.includes("middels")) {
    return "medium";
  }
  if (lower.includes("h\u00f8y") || lower.includes("hoy") || lower.includes("stor")) {
    return "high";
  }
  return "unknown";
}

function buildAnalysisLink(analysis: StoredAnalysis): string | null {
  if (analysis.sourceUrl) {
    try {
      const encoded = encodeURIComponent(analysis.sourceUrl);
      return `/analysis?listing=${encoded}`;
    } catch {
      /* ignore invalid URL */
    }
  }
  if (analysis.finnkode) {
    const finnUrl = `https://www.finn.no/realestate/homes/ad.html?finnkode=${analysis.finnkode}`;
    const encoded = encodeURIComponent(finnUrl);
    return `/analysis?listing=${encoded}`;
  }
  return null;
}

function extractScores(summary: string | null): ScoreTuple {
  if (!summary) {
    return { economy: null, condition: null };
  }

  const economyPattern = /\b(?:\u00f8konomi|okonomi|economy|\u00f8kono)[^0-9]{0,16}(\d{1,3})/i;
  const tgPattern = /\b(?:tg|tilstandsgrad)[^0-9]{0,16}(\d{1,3})/i;

  const economyMatch = summary.match(economyPattern);
  const tgMatch = summary.match(tgPattern);

  const economy = economyMatch ? Number.parseInt(economyMatch[1], 10) : null;
  const condition = tgMatch ? Number.parseInt(tgMatch[1], 10) : null;

  return {
    economy: Number.isFinite(economy) ? Math.max(0, Math.min(100, economy as number)) : null,
    condition: Number.isFinite(condition) ? Math.max(0, Math.min(100, condition as number)) : null,
  };
}

function EmptyIllustration() {
  return (
    <svg
      className="analyses-empty-illustration"
      role="img"
      aria-hidden="true"
      viewBox="0 0 200 140"
    >
      <defs>
        <linearGradient id="emptyGradient" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="rgba(59,130,246,0.6)" />
          <stop offset="50%" stopColor="rgba(14,165,233,0.3)" />
          <stop offset="100%" stopColor="rgba(56,189,248,0.45)" />
        </linearGradient>
      </defs>
      <rect x="12" y="22" width="176" height="106" rx="18" fill="rgba(15,23,42,0.55)" />
      <rect
        x="28"
        y="38"
        width="144"
        height="74"
        rx="14"
        stroke="url(#emptyGradient)"
        strokeWidth="2"
        fill="rgba(30,41,59,0.35)"
      />
      <circle cx="66" cy="62" r="18" fill="rgba(59,130,246,0.35)" />
      <rect x="96" y="52" width="48" height="8" rx="4" fill="rgba(148,163,184,0.45)" />
      <rect x="96" y="68" width="32" height="8" rx="4" fill="rgba(148,163,184,0.32)" />
      <rect x="46" y="94" width="108" height="10" rx="5" fill="rgba(59,130,246,0.24)" />
    </svg>
  );
}

export default function MyAnalysesPage() {
  const [analyses, setAnalyses] = useState<StoredAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [riskFilter, setRiskFilter] = useState<RiskFilterValue>("alle");
  const [sortOption, setSortOption] = useState<SortOption>("date_desc");
  const [currentPage, setCurrentPage] = useState(0);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(() => new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const fetchAnalyses = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetchSavedAnalyses();
      setAnalyses(response.items ?? []);
    } catch {
      setError("Kunne ikke hente analyser nå. Prøv igjen senere.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAnalyses();
  }, [fetchAnalyses]);

  useEffect(() => {
    setSelectedIds((prev) => prev.filter((id) => analyses.some((analysis) => analysis.id === id)));
  }, [analyses]);

  useEffect(() => {
    if (previewId && !analyses.some((analysis) => analysis.id === previewId)) {
      setPreviewId(null);
    }
  }, [analyses, previewId]);

  const normalisedSearch = search.trim().toLowerCase();

  const filteredAnalyses = useMemo(() => {
    const filtered = analyses.filter((item) => {
      if (riskFilter !== "alle") {
        const rank = riskRank(item.riskLevel);
        if (riskFilter === "low" && rank !== 1) {
          return false;
        }
        if (riskFilter === "medium" && rank !== 2) {
          return false;
        }
        if (riskFilter === "high" && rank !== 3) {
          return false;
        }
        if (rank === 500 || rank === 999) {
          return false;
        }
      }

      if (!normalisedSearch) {
        return true;
      }

      const haystack = [item.address, item.finnkode ?? "", item.summary ?? "", item.title]
        .join(" ")
        .toLowerCase();
      return haystack.includes(normalisedSearch);
    });

    const sorted = filtered.slice().sort((a, b) => {
      switch (sortOption) {
        case "score_desc": {
          const aScore = typeof a.totalScore === "number" ? a.totalScore : -1;
          const bScore = typeof b.totalScore === "number" ? b.totalScore : -1;
          if (bScore !== aScore) {
            return bScore - aScore;
          }
          break;
        }
        case "risk_asc": {
          const riskDiff = riskRank(a.riskLevel) - riskRank(b.riskLevel);
          if (riskDiff !== 0) {
            return riskDiff;
          }
          break;
        }
        case "date_desc":
        default: {
          const aTime = a.savedAt ? Date.parse(a.savedAt) : 0;
          const bTime = b.savedAt ? Date.parse(b.savedAt) : 0;
          if (bTime !== aTime) {
            return bTime - aTime;
          }
          break;
        }
      }
      const fallbackScore = (typeof b.totalScore === "number" ? b.totalScore : 0) -
        (typeof a.totalScore === "number" ? a.totalScore : 0);
      if (fallbackScore !== 0) {
        return fallbackScore;
      }
      return (b.savedAt ? Date.parse(b.savedAt) : 0) - (a.savedAt ? Date.parse(a.savedAt) : 0);
    });

    return sorted;
  }, [analyses, normalisedSearch, riskFilter, sortOption]);

  useEffect(() => {
    setCurrentPage(0);
  }, [search, riskFilter, analyses.length]);

  const totalPages = Math.max(1, Math.ceil(filteredAnalyses.length / ITEMS_PER_PAGE));
  useEffect(() => {
    setCurrentPage((prev) => Math.min(prev, totalPages - 1));
  }, [totalPages]);

  const safePage = Math.min(currentPage, totalPages - 1);

  const pagedAnalyses = useMemo(() => {
    const start = safePage * ITEMS_PER_PAGE;
    const end = start + ITEMS_PER_PAGE;
    return filteredAnalyses.slice(start, end);
  }, [filteredAnalyses, safePage]);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const hasSelections = selectedIds.length > 0;
  const allSelectedOnPage = pagedAnalyses.length > 0 && pagedAnalyses.every((item) => selectedSet.has(item.id));

  const previewAnalysis = useMemo(
    () => analyses.find((item) => item.id === previewId) ?? null,
    [analyses, previewId],
  );

  useEffect(() => {
    if (!previewAnalysis) {
      return;
    }
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPreviewId(null);
      }
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [previewAnalysis]);

  const handleToggleSelectAllOnPage = () => {
    setSelectedIds((prev) => {
      if (pagedAnalyses.length === 0) {
        return prev;
      }
      const next = new Set(prev);
      const everySelected = pagedAnalyses.every((item) => next.has(item.id));
      pagedAnalyses.forEach((item) => {
        if (everySelected) {
          next.delete(item.id);
        } else {
          next.add(item.id);
        }
      });
      return Array.from(next);
    });
  };

  const handleToggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) {
        return prev.filter((item) => item !== id);
      }
      return [...prev, id];
    });
  };

  const handleBulkDelete = async () => {
    if (!hasSelections) {
      return;
    }
    const confirmed = window.confirm(
      "Slett de valgte analysene fra oversikten? Dette kan ikke angres uten å laste siden på nytt.",
    );
    if (!confirmed) {
      return;
    }
    setBulkDeleting(true);
    setDeletingIds((prev) => {
      const next = new Set(prev);
      selectedIds.forEach((id) => next.add(id));
      return next;
    });
    const targets = analyses.filter((item) => selectedIds.includes(item.id));
    const failures: string[] = [];
    const succeededIds = new Set<string>();
    await Promise.all(
      targets.map(async (analysis) => {
        try {
          await deleteSavedAnalysis(analysis.id);
          succeededIds.add(analysis.id);
        } catch (error) {
          const message = error instanceof Error ? error.message : "Kunne ikke slette analysen.";
          failures.push(`${analysis.address || analysis.title || analysis.id}: ${message}`);
        }
      }),
    );
    if (failures.length) {
      window.alert(`Noen analyser kunne ikke slettes:\n${failures.join("\n")}`);
    }
    if (succeededIds.size > 0) {
      setAnalyses((prev) => prev.filter((item) => !succeededIds.has(item.id)));
      setSelectedIds((prevSelected) => prevSelected.filter((id) => !succeededIds.has(id)));
      setPreviewId((prev) => (prev && succeededIds.has(prev) ? null : prev));
    }
    setDeletingIds((prev) => {
      const next = new Set(prev);
      selectedIds.forEach((id) => next.delete(id));
      return next;
    });
    setBulkDeleting(false);
    void fetchAnalyses();
  };

  const handleSingleDelete = async (analysisId: string) => {
    const confirmed = window.confirm("Fjern denne analysen fra listen? Du kan hente den igjen ved å laste siden.");
    if (!confirmed) {
      return;
    }
    setDeletingIds((prev) => {
      const next = new Set(prev);
      next.add(analysisId);
      return next;
    });
    try {
      await deleteSavedAnalysis(analysisId);
      setAnalyses((prev) => prev.filter((item) => item.id !== analysisId));
      setSelectedIds((prevSelected) => prevSelected.filter((id) => id !== analysisId));
      setPreviewId((prev) => (prev === analysisId ? null : prev));
      void fetchAnalyses();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Kunne ikke slette analysen.";
      window.alert(message);
    } finally {
      setDeletingIds((prev) => {
        const next = new Set(prev);
        next.delete(analysisId);
        return next;
      });
    }
  };

  const handleDuplicate = (analysis: StoredAnalysis) => {
    const now = new Date();
    const duplicate: StoredAnalysis = {
      ...analysis,
      id: `${analysis.id}-copy-${now.getTime()}`,
      savedAt: now.toISOString(),
      address: `${analysis.address} (kopi)`,
      title: `${analysis.title ?? analysis.address} (kopi)`,
    };
    setAnalyses((prev) => [duplicate, ...prev]);
    setCurrentPage(0);
  };

  const handleExportSelected = () => {
    if (!hasSelections) {
      return;
    }
    const rows = analyses.filter((item) => selectedSet.has(item.id));
    if (rows.length === 0) {
      return;
    }
    const header = ["Adresse", "Dato analysert", "Total score", "Risiko", "FINN-kode", "Pris", "Kilde"];
    const csvRows = rows.map((row) => [
      (row.address || row.title || "").replace(/\s+/g, " "),
      row.savedAt ? new Date(row.savedAt).toISOString() : "",
      typeof row.totalScore === "number" ? String(Math.round(row.totalScore)) : "",
      row.riskLevel ?? "",
      row.finnkode ?? "",
      row.price ? String(Math.round(row.price)) : "",
      row.sourceUrl ?? "",
    ]);
    const csvContent = [header, ...csvRows]
      .map((fields) =>
        fields
          .map((field) => {
            if (field.includes(",") || field.includes("\"")) {
              return `"${field.replace(/"/g, '""')}"`;
            }
            return field;
          })
          .join(","),
      )
      .join("\n");
    const blob = new Blob([`\ufeff${csvContent}`], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `techdom-analyses-${Date.now()}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleShare = async (analysis: StoredAnalysis) => {
    const analysisLink = buildAnalysisLink(analysis);
    if (!analysisLink) {
      return;
    }
    const absoluteLink = typeof window !== "undefined" ? `${window.location.origin}${analysisLink}` : analysisLink;
    if (typeof navigator !== "undefined" && navigator.share) {
      try {
        await navigator.share({
          title: analysis.address ?? "Techdom analyse",
          url: absoluteLink,
        });
        return;
      } catch {
        /* fall back */
      }
    }
    try {
      await navigator.clipboard.writeText(absoluteLink);
      window.alert("Lenke kopiert til utklippstavlen.");
    } catch {
      window.prompt("Kopier lenken", absoluteLink);
    }
  };

  const handleOpenPreview = (analysisId: string) => {
    setPreviewId(analysisId);
  };

  const handleClosePreview = () => {
    setPreviewId(null);
  };

  const handleNavigatePage = (direction: "prev" | "next") => {
    setCurrentPage((prev) => {
      if (direction === "prev") {
        return Math.max(prev - 1, 0);
      }
      return Math.min(prev + 1, totalPages - 1);
    });
  };

  return (
    <main className="page-gradient">
      <PageContainer>
        <SiteHeader showAction actionHref="/" actionLabel="Ny analyse" />

        <section className="analyses-premium">
          <div className="analyses-premium-card">
            <header className="analyses-card-header">
              <div className="analyses-breadcrumb" aria-label="Brødsmulesti">
                <span>Oversikt</span>
                <span className="analyses-breadcrumb-separator">/</span>
                <span>Mine analyser</span>
              </div>
              <h1 className="analyses-title">Mine analyser</h1>
              <p className="analyses-intro">
                Samlede analyser med score, risiko og nøkkelinformasjon. Filtrer, forhåndsvis og eksporter for å
                finne riktige objekter raskt.
              </p>
            </header>

            <div className="analyses-toolbar" role="region" aria-label="Filter for analyser">
              <div className="analyses-toolbar-inner">
                <label className="analysis-field analysis-field--search">
                  <Search aria-hidden="true" />
                  <span className="sr-only">Søk etter adresse eller FINN-kode</span>
                  <input
                    type="search"
                    placeholder="Adresse eller FINN-kode"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </label>
                <label className="analysis-field analysis-field--select">
                  <Filter aria-hidden="true" />
                  <span className="sr-only">Filtrer på risiko</span>
                  <select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value as RiskFilterValue)}>
                    {Object.entries(RISK_FILTER_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="analysis-field analysis-field--select">
                  <ArrowUpDown aria-hidden="true" />
                  <span className="sr-only">Sorter analyser</span>
                  <select value={sortOption} onChange={(event) => setSortOption(event.target.value as SortOption)}>
                    {Object.entries(SORT_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>

            {hasSelections ? (
              <div className="analyses-bulk-bar">
                <span className="analyses-bulk-count">{selectedIds.length} valgt</span>
                <div className="analyses-bulk-actions">
                  <button
                    type="button"
                    className="analyses-bulk-button analyses-bulk-button--danger"
                    onClick={handleBulkDelete}
                    disabled={bulkDeleting}
                    aria-busy={bulkDeleting ? "true" : undefined}
                  >
                    <Trash2 size={16} aria-hidden="true" />
                    Slett valgte
                  </button>
                  <button type="button" className="analyses-bulk-button" onClick={handleExportSelected}>
                    <FileDown size={16} aria-hidden="true" />
                    Eksporter CSV
                  </button>
                </div>
              </div>
            ) : null}

            {loading ? (
              <div className="analyses-skeleton" aria-hidden="true">
                {Array.from({ length: SKELETON_ROWS }).map((_, index) => (
                  <div key={index} className="analyses-skeleton-row" />
                ))}
              </div>
            ) : error ? (
              <div className="analyses-error-state" role="alert">
                <p>{error}</p>
                <button type="button" onClick={fetchAnalyses}>
                  Prøv igjen
                </button>
              </div>
            ) : filteredAnalyses.length === 0 ? (
              <div className="analyses-empty-state">
                <EmptyIllustration />
                <h2>Ingen analyser ennå</h2>
                <p>Kjør en ny analyse eller juster filtrene for å komme i gang.</p>
                <Link href="/" className="analyses-empty-action">
                  Ny analyse
                </Link>
              </div>
            ) : (
              <>
                <div className="analysis-table-wrapper">
                  <table className="analysis-table">
                    <thead>
                      <tr>
                        <th scope="col">
                          <input
                            type="checkbox"
                            aria-label="Velg alle på denne siden"
                            checked={allSelectedOnPage}
                            onChange={handleToggleSelectAllOnPage}
                          />
                        </th>
                        <th scope="col">Eiendom</th>
                        <th scope="col">Dato analysert</th>
                        <th scope="col">Total score</th>
                        <th scope="col">Økono / TG</th>
                        <th scope="col">Risiko</th>
                        <th scope="col">Handlinger</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pagedAnalyses.map((analysis) => {
                        const formattedDate = formatDate(analysis.savedAt);
                        const formattedPrice = formatPrice(analysis.price);
                        const totalScore = normaliseScore(analysis.totalScore);
                        const scoreAccent = getScoreAccent(analysis.totalScore);
                        const { economy, condition } = extractScores(analysis.summary);
                        const riskVariant = getRiskVariant(analysis.riskLevel);
                        const targetLink = buildAnalysisLink(analysis);
                        const deleting = deletingIds.has(analysis.id);
                        return (
                          <tr key={analysis.id} onClick={() => handleOpenPreview(analysis.id)}>
                            <td onClick={(event) => event.stopPropagation()}>
                              <input
                                type="checkbox"
                                aria-label={`Velg analyse for ${analysis.address}`}
                                checked={selectedSet.has(analysis.id)}
                                onChange={() => handleToggleSelect(analysis.id)}
                              />
                            </td>
                            <td>
                              <div className="analysis-property">
                                <div className="analysis-thumb">
                                  {analysis.image ? (
                                    <Image
                                      src={analysis.image}
                                      alt={analysis.address ?? "Eiendomsbilde"}
                                      fill
                                      sizes="80px"
                                      className="analysis-thumb-img"
                                    />
                                  ) : (
                                    <span className="analysis-thumb-placeholder">Ingen bilde</span>
                                  )}
                                </div>
                                <div className="analysis-property-copy">
                                  <div className="analysis-property-title">{analysis.address ?? analysis.title}</div>
                                  <div className="analysis-property-meta">
                                    {analysis.finnkode ? `FINN-kode ${analysis.finnkode}` : "Ingen FINN-kode"}
                                  </div>
                                  {formattedPrice ? (
                                    <div className="analysis-property-price">{formattedPrice}</div>
                                  ) : null}
                                </div>
                              </div>
                            </td>
                            <td>
                              <span className="analysis-date">{formattedDate}</span>
                            </td>
                            <td>
                              <div className="analysis-score-pill" style={{ borderColor: scoreAccent }}>
                                <div className="analysis-score-value">
                                  <strong>{totalScore}</strong>
                                  <span>/100</span>
                                </div>
                                <div className="analysis-score-progress">
                                  <span style={{ width: `${totalScore}%`, background: scoreAccent }} />
                                </div>
                              </div>
                            </td>
                            <td>
                              <div className="analysis-chip-list">
                                <span
                                  className={`analysis-chip${economy === null ? " analysis-chip--muted" : ""}`}
                                  style={
                                    economy === null
                                      ? undefined
                                      : { borderColor: getScoreAccent(economy), color: getScoreAccent(economy) }
                                  }
                                >
                                  <strong>Økono</strong>
                                  <span>{economy === null ? "–" : `${economy}`}</span>
                                </span>
                                <span
                                  className={`analysis-chip${condition === null ? " analysis-chip--muted" : ""}`}
                                  style={
                                    condition === null
                                      ? undefined
                                      : { borderColor: getScoreAccent(condition), color: getScoreAccent(condition) }
                                  }
                                >
                                  <strong>TG</strong>
                                  <span>{condition === null ? "–" : `${condition}`}</span>
                                </span>
                              </div>
                            </td>
                            <td>
                              <span className={`analysis-risk analysis-risk--${riskVariant}`}>
                                {analysis.riskLevel ?? "Ukjent"}
                              </span>
                            </td>
                            <td onClick={(event) => event.stopPropagation()}>
                              <div className="analysis-actions">
                                {targetLink ? (
                                  <Link
                                    href={targetLink}
                                    className="analysis-action-button"
                                    aria-label="Åpne full analyse"
                                  >
                                    <Eye size={16} aria-hidden="true" />
                                  </Link>
                                ) : (
                                  <button className="analysis-action-button" type="button" disabled>
                                    <Eye size={16} aria-hidden="true" />
                                  </button>
                                )}
                                <button
                                  type="button"
                                  className="analysis-action-button"
                                  onClick={() => handleShare(analysis)}
                                  aria-label="Del analyse"
                                >
                                  <Share2 size={16} aria-hidden="true" />
                                </button>
                                <button
                                  type="button"
                                  className="analysis-action-button analysis-action-button--danger"
                                  onClick={() => handleSingleDelete(analysis.id)}
                                  aria-label="Slett analyse"
                                  disabled={deleting || bulkDeleting}
                                  aria-busy={deleting ? "true" : undefined}
                                >
                                  <Trash2 size={16} aria-hidden="true" />
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                <ul className="analysis-card-list">
                  {pagedAnalyses.map((analysis) => {
                    const formattedDate = formatDate(analysis.savedAt);
                    const formattedPrice = formatPrice(analysis.price);
                    const totalScore = normaliseScore(analysis.totalScore);
                    const scoreAccent = getScoreAccent(analysis.totalScore);
                    const { economy, condition } = extractScores(analysis.summary);
                    const riskVariant = getRiskVariant(analysis.riskLevel);
                    const targetLink = buildAnalysisLink(analysis);
                    const deleting = deletingIds.has(analysis.id);
                    return (
                      <li key={analysis.id}>
                        <div
                          className="analysis-card-item"
                          role="button"
                          tabIndex={0}
                          onClick={() => handleOpenPreview(analysis.id)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              handleOpenPreview(analysis.id);
                            }
                          }}
                        >
                          <div className="analysis-card-header">
                            <label className="analysis-card-checkbox" onClick={(event) => event.stopPropagation()}>
                              <input
                                type="checkbox"
                                checked={selectedSet.has(analysis.id)}
                                onChange={() => handleToggleSelect(analysis.id)}
                                aria-label={`Velg analyse for ${analysis.address}`}
                              />
                            </label>
                            <span className={`analysis-risk analysis-risk--${riskVariant}`}>
                              {analysis.riskLevel ?? "Ukjent"}
                            </span>
                          </div>
                          <div className="analysis-card-body">
                            <div className="analysis-card-thumb">
                              {analysis.image ? (
                                <Image
                                  src={analysis.image}
                                  alt={analysis.address ?? "Eiendomsbilde"}
                                  fill
                                  sizes="128px"
                                />
                              ) : (
                                <span className="analysis-thumb-placeholder">Ingen bilde</span>
                              )}
                            </div>
                            <div className="analysis-card-content">
                              <h3>{analysis.address ?? analysis.title}</h3>
                              <p>{formattedDate}</p>
                              {formattedPrice ? <p>{formattedPrice}</p> : null}
                              <div className="analysis-card-score" style={{ borderColor: scoreAccent }}>
                                <div>
                                  <strong>{totalScore}</strong>
                                  <span>/100</span>
                                </div>
                                <div className="analysis-score-progress">
                                  <span style={{ width: `${totalScore}%`, background: scoreAccent }} />
                                </div>
                              </div>
                              <div className="analysis-card-chips">
                                <span
                                  className={`analysis-chip${economy === null ? " analysis-chip--muted" : ""}`}
                                  style={
                                    economy === null
                                      ? undefined
                                      : { borderColor: getScoreAccent(economy), color: getScoreAccent(economy) }
                                  }
                                >
                                  <strong>Økono</strong>
                                  <span>{economy === null ? "–" : `${economy}`}</span>
                                </span>
                                <span
                                  className={`analysis-chip${condition === null ? " analysis-chip--muted" : ""}`}
                                  style={
                                    condition === null
                                      ? undefined
                                      : { borderColor: getScoreAccent(condition), color: getScoreAccent(condition) }
                                  }
                                >
                                  <strong>TG</strong>
                                  <span>{condition === null ? "–" : `${condition}`}</span>
                                </span>
                              </div>
                            </div>
                          </div>
                          <div className="analysis-card-actions" onClick={(event) => event.stopPropagation()}>
                            {targetLink ? (
                              <Link href={targetLink} className="analysis-action-button" aria-label="Åpne full analyse">
                                <Eye size={16} aria-hidden="true" />
                              </Link>
                            ) : (
                              <button className="analysis-action-button" type="button" disabled>
                                <Eye size={16} aria-hidden="true" />
                              </button>
                            )}
                            <button
                              type="button"
                              className="analysis-action-button"
                              onClick={() => handleShare(analysis)}
                              aria-label="Del analyse"
                            >
                              <Share2 size={16} aria-hidden="true" />
                            </button>
                            <button
                              type="button"
                              className="analysis-action-button"
                              onClick={() => handleDuplicate(analysis)}
                              aria-label="Dupliser analyse"
                            >
                              <FileDown size={16} aria-hidden="true" />
                            </button>
                            <button
                              type="button"
                              className="analysis-action-button analysis-action-button--danger"
                              onClick={() => handleSingleDelete(analysis.id)}
                              aria-label="Slett analyse"
                              disabled={deleting || bulkDeleting}
                              aria-busy={deleting ? "true" : undefined}
                            >
                              <Trash2 size={16} aria-hidden="true" />
                            </button>
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>

                <div className="analysis-pagination">
                  <button
                    type="button"
                    onClick={() => handleNavigatePage("prev")}
                    disabled={safePage === 0}
                  >
                    <ArrowLeft size={16} aria-hidden="true" /> Forrige
                  </button>
                  <span>
                    Side {safePage + 1} av {totalPages}
                  </span>
                  <button
                    type="button"
                    onClick={() => handleNavigatePage("next")}
                    disabled={safePage >= totalPages - 1}
                  >
                    Neste <ArrowRight size={16} aria-hidden="true" />
                  </button>
                </div>
              </>
            )}
          </div>
        </section>

        <SiteFooter />
      </PageContainer>

      {previewAnalysis ? (
        <div className="analysis-preview-overlay" role="dialog" aria-modal="true" aria-label="Forhåndsvisning av analyse">
          <div className="analysis-preview-backdrop" onClick={handleClosePreview} />
          <aside className="analysis-preview-panel">
            <button type="button" className="analysis-preview-close" onClick={handleClosePreview} aria-label="Lukk forhåndsvisning">
              <X size={18} aria-hidden="true" />
            </button>
            <div className="analysis-preview-header">
              <div className="analysis-preview-image">
                {previewAnalysis.image ? (
                  <Image
                    src={previewAnalysis.image}
                    alt={previewAnalysis.address ?? "Eiendomsbilde"}
                    fill
                    sizes="320px"
                  />
                ) : (
                  <span className="analysis-thumb-placeholder">Ingen bilde</span>
                )}
              </div>
              <div className="analysis-preview-meta">
                <span className={`analysis-risk analysis-risk--${getRiskVariant(previewAnalysis.riskLevel)}`}>
                  {previewAnalysis.riskLevel ?? "Ukjent risiko"}
                </span>
                <h2>{previewAnalysis.address ?? previewAnalysis.title}</h2>
                <p>{formatDate(previewAnalysis.savedAt)}</p>
                {formatPrice(previewAnalysis.price) ? <p>{formatPrice(previewAnalysis.price)}</p> : null}
              </div>
            </div>
            <div className="analysis-preview-scores">
              <div className="analysis-score-pill" style={{ borderColor: getScoreAccent(previewAnalysis.totalScore) }}>
                <div className="analysis-score-value">
                  <strong>{normaliseScore(previewAnalysis.totalScore)}</strong>
                  <span>/100</span>
                </div>
                <div className="analysis-score-progress">
                  <span
                    style={{
                      width: `${normaliseScore(previewAnalysis.totalScore)}%`,
                      background: getScoreAccent(previewAnalysis.totalScore),
                    }}
                  />
                </div>
              </div>
              <div className="analysis-chip-list">
                {(() => {
                  const { economy, condition } = extractScores(previewAnalysis.summary);
                  return (
                    <>
                      <span
                        className={`analysis-chip${economy === null ? " analysis-chip--muted" : ""}`}
                        style={
                          economy === null
                            ? undefined
                            : { borderColor: getScoreAccent(economy), color: getScoreAccent(economy) }
                        }
                      >
                        <strong>Økono</strong>
                        <span>{economy === null ? "–" : `${economy}`}</span>
                      </span>
                      <span
                        className={`analysis-chip${condition === null ? " analysis-chip--muted" : ""}`}
                        style={
                          condition === null
                            ? undefined
                            : { borderColor: getScoreAccent(condition), color: getScoreAccent(condition) }
                        }
                      >
                        <strong>TG</strong>
                        <span>{condition === null ? "–" : `${condition}`}</span>
                      </span>
                    </>
                  );
                })()}
              </div>
            </div>
            <div className="analysis-preview-body">
              <div className="analysis-preview-section">
                <h3>Nøkkeltall</h3>
                <ul>
                  <li>
                    <span>FINN-kode</span>
                    <span>{previewAnalysis.finnkode ?? "Ikke oppgitt"}</span>
                  </li>
                  <li>
                    <span>Kilde</span>
                    <span>{previewAnalysis.sourceUrl ? "Lenke tilgjengelig" : "Ingen lenke lagret"}</span>
                  </li>
                  <li>
                    <span>Lagret</span>
                    <span>{formatDate(previewAnalysis.savedAt)}</span>
                  </li>
                </ul>
              </div>
              <div className="analysis-preview-section">
                <h3>Oppsummering</h3>
                <p>{previewAnalysis.summary ?? "Ingen oppsummering tilgjengelig for denne analysen."}</p>
              </div>
            </div>
            <div className="analysis-preview-actions">
              {(() => {
                const targetLink = buildAnalysisLink(previewAnalysis);
                return targetLink ? (
                  <Link href={targetLink} className="analysis-preview-action analysis-preview-action--primary">
                    Åpne full analyse
                  </Link>
                ) : (
                  <button type="button" className="analysis-preview-action" disabled>
                    Åpne full analyse
                  </button>
                );
              })()}
              <div className="analysis-preview-action-group">
                <button type="button" onClick={() => handleDuplicate(previewAnalysis)}>
                  Dupliser
                </button>
                <button
                  type="button"
                  onClick={() => handleSingleDelete(previewAnalysis.id)}
                  disabled={deletingIds.has(previewAnalysis.id) || bulkDeleting}
                  aria-busy={deletingIds.has(previewAnalysis.id) ? "true" : undefined}
                >
                  Slett
                </button>
              </div>
            </div>
          </aside>
        </div>
      ) : null}
    </main>
  );
}
