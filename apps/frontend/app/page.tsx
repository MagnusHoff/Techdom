"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "./components/chrome";
import { fetchCurrentUser, fetchStats, fetchUserStatus } from "@/lib/api";
import { extractFinnkode } from "@/lib/listing";
import type { AuthUser, UserStatusResponse } from "@/lib/types";

const INITIAL_ANALYSED_COUNT = 48;
const USER_UPDATED_EVENT = "techdom:user-updated";

const NUMBER_FORMATTER = new Intl.NumberFormat("nb-NO", {
  maximumFractionDigits: 0,
});

const RELATIVE_TIME_FORMATTER = new Intl.RelativeTimeFormat("nb-NO", {
  numeric: "auto",
});

function formatNumber(value: number): string {
  try {
    return NUMBER_FORMATTER.format(value);
  } catch {
    return value.toLocaleString("nb-NO");
  }
}

function formatRelativeTime(timestamp: string | null): string {
  if (!timestamp) {
    return "Ingen kjøringer ennå";
  }
  const timeValue = Date.parse(timestamp);
  if (Number.isNaN(timeValue)) {
    return "Ukjent tidspunkt";
  }

  const now = Date.now();
  const diffMs = timeValue - now;
  const diffMinutes = Math.round(diffMs / (60 * 1000));

  if (Math.abs(diffMinutes) < 1) {
    return "Akkurat nå";
  }

  if (Math.abs(diffMinutes) < 60) {
    return RELATIVE_TIME_FORMATTER.format(diffMinutes, "minute");
  }

  const diffHours = Math.round(diffMinutes / 60);
  if (Math.abs(diffHours) < 24) {
    return RELATIVE_TIME_FORMATTER.format(diffHours, "hour");
  }

  const diffDays = Math.round(diffHours / 24);
  if (Math.abs(diffDays) < 14) {
    return RELATIVE_TIME_FORMATTER.format(diffDays, "day");
  }

  const diffWeeks = Math.round(diffDays / 7);
  if (Math.abs(diffWeeks) < 8) {
    return RELATIVE_TIME_FORMATTER.format(diffWeeks, "week");
  }

  const diffMonths = Math.round(diffDays / 30);
  if (Math.abs(diffMonths) < 18) {
    return RELATIVE_TIME_FORMATTER.format(diffMonths, "month");
  }

  const diffYears = Math.round(diffDays / 365);
  return RELATIVE_TIME_FORMATTER.format(diffYears, "year");
}

export default function LandingPage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [totalAnalyses, setTotalAnalyses] = useState<number>(INITIAL_ANALYSED_COUNT);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [userResolved, setUserResolved] = useState(false);
  const [status, setStatus] = useState<UserStatusResponse | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);

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

  useEffect(() => {
    let cancelled = false;
    fetchCurrentUser()
      .then((current) => {
        if (cancelled) {
          return;
        }
        setUser((previous) => (previous !== null ? previous : current));
      })
      .catch(() => {
        if (!cancelled) {
          setUser((previous) => (previous !== null ? previous : null));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setUserResolved(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handleUserUpdate = (event: Event) => {
      const detail = (event as CustomEvent<AuthUser | null>).detail ?? null;
      setUser(detail);
      setStatus(null);
      setStatusError(null);
      setStatusLoading(false);
    };

    window.addEventListener(USER_UPDATED_EVENT, handleUserUpdate);
    return () => {
      window.removeEventListener(USER_UPDATED_EVENT, handleUserUpdate);
    };
  }, []);

  useEffect(() => {
    if (!user) {
      setStatus(null);
      setStatusError(null);
      setStatusLoading(false);
      return;
    }

    let cancelled = false;
    setStatusLoading(true);
    fetchUserStatus()
      .then((result) => {
        if (cancelled) {
          return;
        }
        setStatus(result);
        setStatusError(null);
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setStatus(null);
        setStatusError("Kunne ikke hente status. Prøv igjen.");
      })
      .finally(() => {
        if (!cancelled) {
          setStatusLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [user]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) {
      setError("Lim inn en FINN-lenke først.");
      return;
    }

    if (!extractFinnkode(trimmed)) {
      setError("ugyldig finn lenke");
      return;
    }

    setError(null);
    const encoded = encodeURIComponent(trimmed);
    const runToken = Date.now().toString(36);
    router.push(`/analysis?listing=${encoded}&run=${runToken}`);
  };

  const formattedTotalAnalyses = useMemo(() => formatNumber(totalAnalyses), [totalAnalyses]);
  const formattedUserTotal = useMemo(() => {
    if (!status) {
      return formatNumber(0);
    }
    return formatNumber(status.total_user_analyses);
  }, [status]);

  const formattedRecentCount = useMemo(() => {
    if (!status) {
      return formatNumber(0);
    }
    return formatNumber(status.total_last_7_days);
  }, [status]);

  const lastRunRelative = useMemo(() => formatRelativeTime(status?.last_run_at ?? null), [status?.last_run_at]);

  const showSkeleton = !userResolved || (user !== null && statusLoading);
  const showMinStatusCard = Boolean(user);
  const hasAnyAnalyses = (status?.total_user_analyses ?? 0) > 0;
  const statusGridClassName = [
    "landing-status-grid",
    !showSkeleton && !showMinStatusCard ? "landing-status-grid--solo" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const lastAnalysisDisplay = hasAnyAnalyses ? lastRunRelative : "Ingen analyser ennå";

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
          <div className={statusGridClassName}>
            {showSkeleton ? (
              <>
                <StatusCardSkeleton />
                <StatusCardSkeleton />
              </>
            ) : showMinStatusCard ? (
              <>
                <MinStatusCard
                  total={formattedUserTotal}
                  recentCount={formattedRecentCount}
                  lastAnalysis={lastAnalysisDisplay}
                  statusError={statusError}
                  hasAnyAnalyses={hasAnyAnalyses}
                />
                <GlobalAnalyzedCard total={formattedTotalAnalyses} />
              </>
            ) : (
              <GlobalAnalyzedCard total={formattedTotalAnalyses} />
            )}
          </div>
        </section>

        <SiteFooter />
      </PageContainer>
    </main>
  );
}

interface GlobalAnalyzedCardProps {
  total: string;
}

function GlobalAnalyzedCard({ total }: GlobalAnalyzedCardProps) {
  return (
    <article className="status-card status-card--global">
      <header className="status-card__header">
        <h2 className="status-card__title">Eiendommer analysert</h2>
      </header>
      <p className="status-card__value" aria-live="polite">
        {total}
      </p>
    </article>
  );
}

interface MinStatusCardProps {
  total: string;
  recentCount: string;
  lastAnalysis: string;
  statusError: string | null;
  hasAnyAnalyses: boolean;
}

function MinStatusCard({ total, recentCount, lastAnalysis, statusError, hasAnyAnalyses }: MinStatusCardProps) {
  return (
    <article className="status-card status-card--personal" aria-live="polite">
      <header className="status-card__header">
        <div className="status-card__header-inner">
          <h2 className="status-card__title">Min status</h2>
          <p className="status-card__value">{total}</p>
          <span className="status-card__subtitle">Analyser totalt</span>
        </div>
      </header>

      {statusError ? (
        <p className="status-card__message status-card__message--error" role="status">
          {statusError}
        </p>
      ) : (
        <dl className="status-card__list">
          <div className="status-card__row">
            <dt className="status-card__row-label">Analyser siste 7 dager</dt>
            <dd className="status-card__row-value">{recentCount}</dd>
          </div>
          <div className="status-card__row">
            <dt className="status-card__row-label">Siste analyse</dt>
            <dd className="status-card__row-value">{hasAnyAnalyses ? lastAnalysis : "Ingen analyser ennå"}</dd>
          </div>
        </dl>
      )}

      <footer className="status-card__footer">
        <Link className="status-card__cta" href="/mine-analyser">
          Lagrede analyser
        </Link>
      </footer>
    </article>
  );
}

function StatusCardSkeleton() {
  return <div className="status-card status-card--skeleton" aria-hidden="true" />;
}
