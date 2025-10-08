"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "./components/chrome";
import UserEmojiAvatar from "./components/user-avatar";
import { fetchCurrentUser, fetchStats, fetchUserStatus } from "@/lib/api";
import { extractFinnkode } from "@/lib/listing";
import { resolveUserLevel, userDisplayName, userInitials } from "@/lib/user";
import type { AuthUser, UserStatusResponse } from "@/lib/types";

const INITIAL_ANALYSED_COUNT = 48;
const USER_UPDATED_EVENT = "techdom:user-updated";

const NUMBER_FORMATTER = new Intl.NumberFormat("nb-NO", {
  maximumFractionDigits: 0,
});

function formatNumber(value: number): string {
  try {
    return NUMBER_FORMATTER.format(value);
  } catch {
    return value.toLocaleString("nb-NO");
  }
}

function hexWithAlpha(hexColor: string, alpha: number): string {
  const clampedAlpha = Number.isFinite(alpha) ? Math.min(Math.max(alpha, 0), 1) : 0;
  const normalised = hexColor.trim().replace(/^#/, "");
  const expanded = normalised.length === 3
    ? normalised.split("").map((char) => `${char}${char}`).join("")
    : normalised;
  if (expanded.length !== 6) {
    return hexColor;
  }
  const r = Number.parseInt(expanded.slice(0, 2), 16);
  const g = Number.parseInt(expanded.slice(2, 4), 16);
  const b = Number.parseInt(expanded.slice(4, 6), 16);
  if ([r, g, b].some((component) => Number.isNaN(component))) {
    return hexColor;
  }
  return `rgba(${r}, ${g}, ${b}, ${clampedAlpha})`;
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
  const userId = user?.id ?? null;
  const userAnalyses = user?.total_analyses ?? null;

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

  const emitUserUpdate = (nextUser: AuthUser | null) => {
    if (typeof window === "undefined") {
      return;
    }
    window.dispatchEvent(new CustomEvent<AuthUser | null>(USER_UPDATED_EVENT, { detail: nextUser }));
  };

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
      setStatus((previous) => {
        if (!detail) {
          return null;
        }
        if (!previous) {
          return {
            total_user_analyses: detail.total_analyses,
            total_last_7_days: 0,
            last_run_at: null,
          };
        }
        if (previous.total_user_analyses === detail.total_analyses) {
          return previous;
        }
        return { ...previous, total_user_analyses: detail.total_analyses };
      });
      setStatusError(null);
      setStatusLoading(false);
    };

    window.addEventListener(USER_UPDATED_EVENT, handleUserUpdate);
    return () => {
      window.removeEventListener(USER_UPDATED_EVENT, handleUserUpdate);
    };
  }, []);

  useEffect(() => {
    if (userId === null) {
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
        setUser((previous) => {
          if (!previous) {
            return previous;
          }
          if (previous.total_analyses === result.total_user_analyses) {
            return previous;
          }
          return { ...previous, total_analyses: result.total_user_analyses };
        });
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
  }, [userId, userAnalyses]);

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

  const analysesForBadge = user?.total_analyses ?? status?.total_user_analyses ?? 0;

  const showSkeleton = !userResolved || (user !== null && statusLoading);
  const showMinStatusCard = Boolean(user);
  const statusGridClassName = [
    "landing-status-grid",
    !showMinStatusCard ? "landing-status-grid--solo" : "",
  ]
    .filter(Boolean)
    .join(" ");

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
              showMinStatusCard ? (
                <>
                  <StatusCardSkeleton />
                  <div className="status-card-stack">
                    <StatusCardSkeleton className="status-card--square" />
                    <StatusCardSkeleton className="status-card--square" />
                    <StatusCardSkeleton className="status-card--square" />
                  </div>
                </>
              ) : (
                <StatusCardSkeleton />
              )
            ) : showMinStatusCard && user ? (
              <>
                <MinStatusCard
                  user={user}
                  totalAnalyses={analysesForBadge}
                  statusError={statusError}
                  onUserUpdate={(updated) => {
                    setUser(updated);
                    emitUserUpdate(updated);
                  }}
                />
                <div className="status-card-stack">
                  <MetricCard label="Totalt på plattformen" value={formattedTotalAnalyses} variant="square" />
                  <MetricCard label="Dine analyser totalt" value={formattedUserTotal} variant="square" />
                  <MetricCard label="Analyser siste 7 dager" value={formattedRecentCount} variant="square" />
                </div>
              </>
            ) : (
              <MetricCard label="Eiendommer analysert" value={formattedTotalAnalyses} />
            )}
          </div>
        </section>

        <SiteFooter />
      </PageContainer>
    </main>
  );
}

interface MetricCardProps {
  label: string;
  value: string;
  variant?: "highlight" | "square";
}

function MetricCard({ label, value, variant = "highlight" }: MetricCardProps) {
  const className = [
    "status-card",
    "status-card--highlight",
    variant === "square" ? "status-card--square" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article className={className} aria-live="polite">
      <span className="status-card__label">{label}</span>
      <strong className="status-card__value">{value}</strong>
    </article>
  );
}

interface MinStatusCardProps {
  user: AuthUser;
  totalAnalyses: number;
  statusError: string | null;
  onUserUpdate: (user: AuthUser) => void;
}

function MinStatusCard({ user, totalAnalyses, statusError, onUserUpdate }: MinStatusCardProps) {
  const initials = userInitials(user);
  const name = userDisplayName(user);
  const level = resolveUserLevel(totalAnalyses);
  const badgeStyle = useMemo(() => {
    const baseColor = level.color || "#9CA3AF";
    return {
      color: baseColor,
      backgroundColor: hexWithAlpha(baseColor, 0.18),
      borderColor: hexWithAlpha(baseColor, 0.35),
      boxShadow: `0 0 0 1px ${hexWithAlpha(baseColor, 0.22)} inset`,
    } as const;
  }, [level.color]);
  const analysisLabel = level.analyses === 1 ? "analyse" : "analyser";
  const badgeAriaLabel = `${level.label} – ${level.analyses} ${analysisLabel}`;

  return (
    <article className="status-card status-card--personal" aria-live="polite">
      <h2 className="status-card__title sr-only">Min status</h2>

      <div className="status-card__personal-content">
        <div className="status-card__avatar-group">
          <UserEmojiAvatar
            user={user}
            initials={initials}
            avatarEmoji={user.avatar_emoji ?? null}
            avatarColor={user.avatar_color ?? null}
            className="status-card__avatar"
            label="Velg emoji for avatar"
            onUserUpdate={onUserUpdate}
          />
        </div>

        <div className="status-card__personal-details">
          <p className="status-card__name">{name}</p>
          <span
            className="status-card__badge"
            aria-label={badgeAriaLabel}
            title={`${level.analyses} ${analysisLabel}`}
            style={badgeStyle}
            data-user-level={level.level}
          >
            {level.label}
          </span>
        </div>
      </div>

      {statusError ? (
        <p className="status-card__message status-card__message--error" role="status">
          {statusError}
        </p>
      ) : null}

      <div className="status-card__actions">
        <Link className="status-card__action" href="/mine-analyser">
          Mine analyser
        </Link>
        <button className="status-card__action status-card__action--disabled" type="button" disabled>
          Mine venner
        </button>
      </div>
    </article>
  );
}

interface StatusCardSkeletonProps {
  className?: string;
}

function StatusCardSkeleton({ className = "" }: StatusCardSkeletonProps) {
  const skeletonClassName = ["status-card", "status-card--skeleton", className]
    .filter(Boolean)
    .join(" ");
  return <div className={skeletonClassName} aria-hidden="true" />;
}
