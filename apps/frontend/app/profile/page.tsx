"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { AUTH_MODAL_EVENT, PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import UserEmojiAvatar from "../components/user-avatar";
import {
  BillingInterval,
  changePassword,
  createSubscriptionCheckoutSession,
  createSubscriptionPortalSession,
  fetchCurrentUser,
  logoutUser,
  updateUsername,
} from "@/lib/api";
import { userDisplayName, userInitials } from "@/lib/user";
import { Check, ChevronDown, Lock, LogOut, User } from "lucide-react";
import type { AuthUser } from "@/lib/types";

const USER_UPDATED_EVENT = "techdom:user-updated";
const USERNAME_PATTERN = /^[a-zA-Z0-9._]{3,20}$/;
const PASSWORD_PATTERN =
  /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]).{8,}$/;
const PASSWORD_REQUIREMENT_MESSAGE =
  "Passordet må være minst 8 tegn og inneholde store og små bokstaver, tall og spesialtegn.";

type PlanId = "free" | "plus";

interface PlanCardConfig {
  name: string;
  subtitle: string;
  pricing: Record<BillingInterval, string>;
  bullets: string[];
  isFeatured?: boolean;
}

interface FeatureComparisonRow {
  id: string;
  label: string;
  free: string | boolean;
  plus: string | boolean;
}

const PLAN_CARDS: Record<PlanId, PlanCardConfig> = {
  free: {
    name: "Gratis",
    subtitle: "Kom i gang med kjernefunksjonene.",
    pricing: {
      monthly: "kr 0,-",
      yearly: "kr 0,-",
    },
    bullets: [
      "Stander analyser",
      "Utleiepris estimat",
      "Tilgang til AI-chat",
      "Tilgang til kundeservice",
    ],
  },
  plus: {
    name: "Pluss",
    subtitle: "Full tilgang til Techdom.ai uten begrensninger.",
    pricing: {
      monthly: "kr 199,-",
      yearly: "kr 1 990,-",
    },
    bullets: [
      "Alt i gratis",
      "Ubegrenset analyser",
      "Agent (Kommer snart)",
      "Maskinlære utleiepris estimat (kommer snart)",
      "Tidlig tilgang til nyeste funksjoner",
      "PDF - ananlyse eksport",
    ],
    isFeatured: true,
  },
};

const FEATURE_COMPARISON: FeatureComparisonRow[] = [
  { id: "analyses", label: "Analyser", free: "Standard", plus: "Ubegrenset" },
  { id: "rent", label: "Utleiepris estimat", free: true, plus: true },
  { id: "chat", label: "AI-chat", free: true, plus: true },
  { id: "support", label: "Kundeservice", free: true, plus: "Prioritert" },
  { id: "agent", label: "Agent", free: false, plus: "Kommer snart" },
  { id: "ml-rent", label: "Maskinlære utleiepris estimat", free: false, plus: "Kommer snart" },
  { id: "early", label: "Tidlig tilgang", free: false, plus: true },
  { id: "pdf", label: "PDF-eksport", free: false, plus: true },
];

const TRUST_TEXT =
  "Sikker betaling • Avbryt når som helst • Norsk MVA (25 %) inkludert ved betaling";

type SectionId = "profile" | "subscription" | "settings";

interface SectionConfig {
  id: SectionId;
  label: string;
  comingSoon: boolean;
}

const SECTIONS: SectionConfig[] = [
  { id: "profile", label: "Min profil", comingSoon: false },
  { id: "subscription", label: "Abonnement", comingSoon: false },
  { id: "settings", label: "Innstillinger", comingSoon: true },
];

const isSectionId = (value: string | null): value is SectionId =>
  typeof value === "string" && SECTIONS.some((section) => section.id === value);

const isPlusRole = (role: AuthUser["role"]): boolean => role === "plus" || role === "admin";

const formatRenewalDate = (value: string | null | undefined): string | null => {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return new Intl.DateTimeFormat("nb-NO", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(date);
};

const SECTION_META: Record<SectionId, { title: string; subtitle: string }> = {
  profile: {
    title: "Min profil",
    subtitle: "Oppdater informasjonen din og administrer tilgang til Techdom.ai.",
  },
  subscription: {
    title: "Abonnement",
    subtitle: "Sammenlign planene og oppgrader når du er klar for mer analysekapasitet.",
  },
  settings: {
    title: "Innstillinger",
    subtitle: "Tilpass opplevelsen din. Flere valg kommer snart.",
  },
};

function ProfilePageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [usernameValue, setUsernameValue] = useState("");
  const [usernameError, setUsernameError] = useState<string | null>(null);
  const [usernameMessage, setUsernameMessage] = useState<string | null>(null);
  const [usernameLoading, setUsernameLoading] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [repeatPassword, setRepeatPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordMessage, setPasswordMessage] = useState<string | null>(null);
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [logoutLoading, setLogoutLoading] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<SectionId>("profile");

  useEffect(() => {
    const sectionParam = searchParams.get("section");
    if (isSectionId(sectionParam)) {
      setActiveSection(sectionParam);
      return;
    }
    if (!sectionParam) {
      setActiveSection("profile");
    }
  }, [searchParams]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchCurrentUser()
      .then((current) => {
        if (cancelled) {
          return;
        }
        setUser(current);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        const message = err instanceof Error ? err.message : "Fant ikke bruker";
        setError(message);
        setUser(null);
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setUsernameValue(user?.username?.trim() ?? "");
  }, [user]);

  const initials = useMemo(() => userInitials(user), [user]);
  const friendlyName = useMemo(() => userDisplayName(user, "Ikke satt"), [user]);
  const activeMeta = SECTION_META[activeSection];

  const openAuthModal = useCallback((mode: "login" | "signup" | "forgot" = "signup") => {
    if (typeof window === "undefined") {
      return;
    }
    window.dispatchEvent(
      new CustomEvent<{ open?: boolean; mode?: "login" | "signup" | "forgot" }>(
        AUTH_MODAL_EVENT,
        {
          detail: { open: true, mode },
        },
      ),
    );
  }, []);

  const emitUserUpdate = (nextUser: AuthUser | null) => {
    if (typeof window === "undefined") {
      return;
    }
    window.dispatchEvent(
      new CustomEvent<AuthUser | null>(USER_UPDATED_EVENT, { detail: nextUser }),
    );
  };

  const applyUserUpdate = (nextUser: AuthUser) => {
    setUser(nextUser);
    emitUserUpdate(nextUser);
  };

  const handleUsernameSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setUsernameError(null);
    setUsernameMessage(null);

    const trimmed = usernameValue.trim();
    if (!USERNAME_PATTERN.test(trimmed)) {
      setUsernameError(
        "Brukernavn må være 3-20 tegn og kan kun inneholde bokstaver, tall, punktum og understrek.",
      );
      return;
    }

    const currentUsername = user?.username?.trim() ?? "";
    if (trimmed === currentUsername) {
      setUsernameMessage("Brukernavnet er allerede oppdatert.");
      return;
    }

    setUsernameLoading(true);
    try {
      const updated = await updateUsername({ username: trimmed });
      applyUserUpdate(updated);
      setUsernameValue(updated.username?.trim() ?? "");
      setUsernameMessage("Brukernavnet er oppdatert.");
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Kunne ikke oppdatere brukernavnet akkurat nå.";
      setUsernameError(message);
    } finally {
      setUsernameLoading(false);
    }
  };

  const handlePasswordSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPasswordError(null);
    setPasswordMessage(null);

    if (!currentPassword) {
      setPasswordError("Skriv inn nåværende passord.");
      return;
    }

    if (!PASSWORD_PATTERN.test(newPassword)) {
      setPasswordError(PASSWORD_REQUIREMENT_MESSAGE);
      return;
    }

    if (newPassword !== repeatPassword) {
      setPasswordError("Passordene må være like.");
      return;
    }

    setPasswordLoading(true);
    try {
      await changePassword({ currentPassword, newPassword });
      setPasswordMessage("Passordet er oppdatert.");
      setCurrentPassword("");
      setNewPassword("");
      setRepeatPassword("");
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Kunne ikke oppdatere passordet akkurat nå.";
      setPasswordError(message);
    } finally {
      setPasswordLoading(false);
    }
  };

  const handleLogout = async () => {
    setLogoutError(null);
    try {
      setLogoutLoading(true);
      await logoutUser();
      emitUserUpdate(null);
      setUser(null);
      router.push("/");
      router.refresh();
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Kunne ikke logge ut akkurat nå.";
      setLogoutError(message);
    } finally {
      setLogoutLoading(false);
    }
  };

  const renderSectionContent = () => {
    switch (activeSection) {
      case "profile":
        return (
          <div className="profile-card">
            <div className="profile-main">
              <UserEmojiAvatar
                user={user}
                initials={initials}
                avatarEmoji={user?.avatar_emoji ?? null}
                avatarColor={user?.avatar_color ?? null}
                className="profile-avatar"
                label="Velg emoji for avatar"
                onUserUpdate={applyUserUpdate}
              />
              <div className="profile-details">
                <div className="profile-row">
                  <span className="profile-label">Navn</span>
                  <p className="profile-value">{friendlyName}</p>
                </div>
                <div className="profile-row">
                  <span className="profile-label">E-post</span>
                  <p className="profile-value">{user?.email}</p>
                </div>
                <div className="profile-row">
                  <span className="profile-label">Rolle</span>
                  <p className="profile-value">{user?.role}</p>
                </div>
              </div>
            </div>

            <div className="profile-settings">
              <section className="profile-section">
                <div className="profile-section-header">
                  <div className="profile-section-title">
                    <User aria-hidden="true" className="profile-section-icon" size={18} />
                    <h3>Brukernavn</h3>
                  </div>
                  <p>Velg hvordan navnet ditt vises i appen.</p>
                  <p className="profile-hint">Tillatte tegn: bokstaver, tall, punktum og understrek.</p>
                </div>
                {usernameError ? (
                  <p className="profile-feedback profile-feedback--error">{usernameError}</p>
                ) : null}
                {usernameMessage ? (
                  <p className="profile-feedback profile-feedback--success">{usernameMessage}</p>
                ) : null}
                <form className="profile-form" onSubmit={handleUsernameSubmit}>
                  <label className="sr-only" htmlFor="profile-username">
                    Brukernavn
                  </label>
                  <input
                    id="profile-username"
                    type="text"
                    className="login-input profile-input"
                    placeholder="Brukernavn"
                    value={usernameValue}
                    onChange={(event) => setUsernameValue(event.target.value)}
                    autoComplete="name"
                    minLength={3}
                    maxLength={20}
                    pattern="[A-Za-z0-9._]{3,20}"
                    required
                    disabled={usernameLoading}
                  />
                  <button type="submit" className="profile-button" disabled={usernameLoading}>
                    {usernameLoading ? "Lagrer…" : "Oppdater brukernavn"}
                  </button>
                </form>
              </section>

              <section className="profile-section">
                <div className="profile-section-header">
                  <div className="profile-section-title">
                    <Lock aria-hidden="true" className="profile-section-icon" size={18} />
                    <h3>Passord</h3>
                  </div>
                  <p>
                    Bytt passordet ditt uten å gå via e-post. Passordet må inneholde store og små bokstaver,
                    tall og spesialtegn.
                  </p>
                </div>
                {passwordError ? (
                  <p className="profile-feedback profile-feedback--error">{passwordError}</p>
                ) : null}
                {passwordMessage ? (
                  <p className="profile-feedback profile-feedback--success">{passwordMessage}</p>
                ) : null}
                <form className="profile-form" onSubmit={handlePasswordSubmit}>
                  <label className="sr-only" htmlFor="profile-current-password">
                    Nåværende passord
                  </label>
                  <input
                    id="profile-current-password"
                    type="password"
                    className="login-input profile-input"
                    placeholder="Nåværende passord"
                    value={currentPassword}
                    onChange={(event) => setCurrentPassword(event.target.value)}
                    autoComplete="current-password"
                    required
                    disabled={passwordLoading}
                  />
                  <label className="sr-only" htmlFor="profile-new-password">
                    Nytt passord
                  </label>
                  <input
                    id="profile-new-password"
                    type="password"
                    className="login-input profile-input"
                    placeholder="Nytt passord"
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                    autoComplete="new-password"
                    minLength={8}
                    required
                    disabled={passwordLoading}
                  />
                  <label className="sr-only" htmlFor="profile-repeat-password">
                    Gjenta nytt passord
                  </label>
                  <input
                    id="profile-repeat-password"
                    type="password"
                    className="login-input profile-input"
                    placeholder="Gjenta nytt passord"
                    value={repeatPassword}
                    onChange={(event) => setRepeatPassword(event.target.value)}
                    autoComplete="new-password"
                    minLength={8}
                    required
                    disabled={passwordLoading}
                  />
                  <button type="submit" className="profile-button" disabled={passwordLoading}>
                    {passwordLoading ? "Lagrer…" : "Oppdater passord"}
                  </button>
                </form>
              </section>

              <section className="profile-section profile-section--logout">
                <div className="profile-section-header">
                  <div className="profile-section-title">
                    <LogOut aria-hidden="true" className="profile-section-icon" size={18} />
                    <h3>Logg ut</h3>
                  </div>
                  <p>Avslutt sesjonen på denne enheten.</p>
                </div>
                {logoutError ? (
                  <p className="profile-feedback profile-feedback--error">{logoutError}</p>
                ) : null}
                <button
                  type="button"
                  onClick={handleLogout}
                  className="profile-button profile-button--danger"
                  disabled={logoutLoading}
                >
                  {logoutLoading ? "Logger ut…" : "Logg ut"}
                </button>
              </section>
            </div>
          </div>
        );
      case "subscription":
        return (
          <SubscriptionSection
            user={user}
            onRequestSignup={() => openAuthModal("signup")}
          />
        );
      case "settings": {
        return (
          <div className="profile-card profile-card--placeholder" role="status">
            <div className="profile-placeholder-content">
              <h2>Innstillinger</h2>
              <p>Dette området blir tilgjengelig snart.</p>
            </div>
          </div>
        );
      }
      default:
        return null;
    }
  };

  return (
    <main className="page-gradient">
      <PageContainer>
        <SiteHeader showAction actionHref="/analysis" actionLabel="Ny analyse" />

        <section className="profile-page">
          <header className="profile-header">
            <div className="profile-breadcrumb" aria-label="Brødsmule">
              <span>Mine sider</span>
              <span className="profile-breadcrumb-separator">/</span>
              <span>{activeMeta?.title ?? "Min profil"}</span>
            </div>
            <div className="profile-title-group">
              <h1>{activeMeta?.title ?? "Min profil"}</h1>
              {activeMeta?.subtitle ? (
                <p className="profile-subtitle">{activeMeta.subtitle}</p>
              ) : null}
            </div>
          </header>

          {loading ? (
            <div className="profile-placeholder">Laster profil…</div>
          ) : error ? (
            <div className="profile-error">
              <h2>Ikke logget inn</h2>
              <p>{error}</p>
              <p>Bruk menyen øverst for å logge inn.</p>
            </div>
          ) : (
            <div className="profile-layout">
              <aside className="profile-sidebar" aria-label="Profilvalg">
                <div className="profile-sidebar-card">
                  {SECTIONS.map((section) => {
                    const isActive = activeSection === section.id;
                    const isDisabled = section.comingSoon;
                    const className = [
                      "profile-sidebar-button",
                      isActive ? "is-active" : "",
                      section.comingSoon ? "is-coming-soon" : "",
                    ]
                      .filter(Boolean)
                      .join(" ");

                    return (
                      <button
                        key={section.id}
                        type="button"
                        className={className}
                        onClick={() => {
                          if (!isDisabled) {
                            setActiveSection(section.id);
                            const params = new URLSearchParams(searchParams.toString());
                            if (section.id === "profile") {
                              params.delete("section");
                            } else {
                              params.set("section", section.id);
                            }
                            const query = params.toString();
                            router.replace(query ? `/profile?${query}` : "/profile", { scroll: false });
                          }
                        }}
                        disabled={isDisabled}
                        aria-current={isActive ? "page" : undefined}
                      >
                        <span>{section.label}</span>
                        {section.comingSoon ? <span className="profile-sidebar-soon">Snart</span> : null}
                      </button>
                    );
                  })}
                </div>
              </aside>

              <div className="profile-content">{renderSectionContent()}</div>
            </div>
          )}
        </section>

        <SiteFooter />
      </PageContainer>
    </main>
  );
}

interface SubscriptionSectionProps {
  user: AuthUser | null;
  onRequestSignup: () => void;
}

function SubscriptionSection({ user, onRequestSignup }: SubscriptionSectionProps) {
  const [billingInterval, setBillingInterval] = useState<BillingInterval>("monthly");
  const [openAccordion, setOpenAccordion] = useState<string | null>(
    FEATURE_COMPARISON[0]?.id ?? null,
  );
  const [subscriptionError, setSubscriptionError] = useState<string | null>(null);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [portalLoading, setPortalLoading] = useState(false);
  const [stickyVisible, setStickyVisible] = useState(false);

  const isLoggedIn = Boolean(user);
  const isPlusUser = user ? isPlusRole(user.role) : false;
  const isFreeUser = user ? !isPlusUser : false;

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (isPlusUser) {
      setStickyVisible(false);
      return;
    }

    const handleScroll = () => {
      const doc = document.documentElement;
      const scrollTop = doc.scrollTop || document.body.scrollTop;
      const scrollHeight = doc.scrollHeight || document.body.scrollHeight;
      const clientHeight = doc.clientHeight || window.innerHeight;
      const progress = scrollHeight <= clientHeight ? 1 : scrollTop / (scrollHeight - clientHeight);
      const isMobile = window.innerWidth <= 768;
      setStickyVisible(!isPlusUser && isMobile && progress >= 0.6);
    };

    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, [isPlusUser]);

  const planOrder: PlanId[] = ["free", "plus"];

  const renderFeatureValue = useCallback(
    (value: string | boolean, variant: "table" | "accordion") => {
      const baseClass =
        variant === "table" ? "subscription-table-value" : "subscription-accordion-value";
      if (typeof value === "boolean") {
        if (value) {
          return (
            <span className={`${baseClass} is-available`}>
              <Check size={16} aria-hidden="true" />
              <span className="sr-only">Inkludert</span>
            </span>
          );
        }
        return (
          <span className={`${baseClass} is-missing`}>
            <span aria-hidden="true">–</span>
            <span className="sr-only">Ikke inkludert</span>
          </span>
        );
      }
      return <span className={baseClass}>{value}</span>;
    },
    [],
  );

  const handlePlusCta = useCallback(async () => {
    if (!isLoggedIn) {
      onRequestSignup();
      return;
    }

    if (!isFreeUser) {
      return;
    }

    setSubscriptionError(null);
    setCheckoutLoading(true);

    try {
      const checkoutUrl = await createSubscriptionCheckoutSession(billingInterval);
      if (typeof window !== "undefined") {
        window.location.assign(checkoutUrl);
      }
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Kunne ikke starte Stripe Checkout akkurat nå.";
      setSubscriptionError(message);
    } finally {
      setCheckoutLoading(false);
    }
  }, [billingInterval, isFreeUser, isLoggedIn, onRequestSignup]);

  const handlePortalCta = useCallback(async () => {
    if (!isLoggedIn) {
      onRequestSignup();
      return;
    }

    if (!isPlusUser) {
      return;
    }

    setSubscriptionError(null);
    setPortalLoading(true);

    try {
      const portalUrl = await createSubscriptionPortalSession();
      if (typeof window !== "undefined") {
        window.location.assign(portalUrl);
      }
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Kunne ikke åpne Stripe-portalen. Prøv igjen senere.";
      setSubscriptionError(message);
    } finally {
      setPortalLoading(false);
    }
  }, [isLoggedIn, isPlusUser, onRequestSignup]);

  const handleFreeCta = useCallback(() => {
    if (!isLoggedIn) {
      onRequestSignup();
      return;
    }
  }, [isLoggedIn, onRequestSignup]);

  const stickyCtaLabel = isLoggedIn ? "Oppgrader til Pluss" : "Lag gratis konto";

  return (
    <div className="subscription-card">
      <section className="subscription-hero">
        <h2>Velg abonnementet som passer deg</h2>
        <p>Bygg tryggere beslutninger med riktig analysepakke.</p>
        <div className="subscription-billing-toggle" role="group" aria-label="Frekvens">
          <button
            type="button"
            className={billingInterval === "monthly" ? "is-active" : ""}
            onClick={() => setBillingInterval("monthly")}
          >
            Månedlig
          </button>
          <button
            type="button"
            className={billingInterval === "yearly" ? "is-active" : ""}
            onClick={() => setBillingInterval("yearly")}
          >
            Årlig – 2 mnd gratis
          </button>
        </div>
      </section>

      {subscriptionError ? (
        <p className="profile-feedback profile-feedback--error" role="alert">
          {subscriptionError}
        </p>
      ) : null}

      <section className="subscription-plan-grid" aria-label="Abonnementskort">
        {planOrder.map((planId) => {
          const config = PLAN_CARDS[planId];
          const isCurrentPlan =
            (planId === "plus" && isPlusUser) || (planId === "free" && isFreeUser);
          const isCurrentPlusPlan = planId === "plus" && isPlusUser;
          const nextRenewal = isCurrentPlusPlan
            ? formatRenewalDate(user?.subscription_current_period_end)
            : null;
          const cardClassName = [
            "subscription-plan",
            config.isFeatured ? "is-featured" : "",
            isCurrentPlan ? "is-current" : "",
          ]
            .filter(Boolean)
            .join(" ");

          const priceSuffix = billingInterval === "monthly" ? "per måned" : "per år";

          const planAction = (() => {
            if (planId === "free") {
              if (!isLoggedIn) {
                return (
                  <button type="button" className="subscription-plan-cta" onClick={handleFreeCta}>
                    Lag gratis konto
                  </button>
                );
              }
              if (isFreeUser) {
                return <span className="subscription-plan-current">Nåværende plan</span>;
              }
              return null;
            }

            if (planId === "plus") {
              if (isPlusUser) {
                return null;
              }
              return (
                <button
                  type="button"
                  className="subscription-plan-cta"
                  onClick={handlePlusCta}
                  disabled={checkoutLoading}
                >
                  {checkoutLoading ? "Sender til Stripe…" : "Oppgrader til Pluss"}
                </button>
              );
            }

            return null;
          })();

          const manageAction = planId === "plus" && isPlusUser ? (
            <div className="subscription-plan-manage">
              <button
                type="button"
                className="subscription-plan-cta subscription-plan-cta--secondary"
                onClick={handlePortalCta}
                disabled={portalLoading}
              >
                {portalLoading ? "Åpner…" : "Administrer i Stripe"}
              </button>
            </div>
          ) : null;

          return (
            <article key={planId} className={cardClassName} aria-label={config.name}>
              {config.isFeatured && !isCurrentPlusPlan ? (
                <div className="subscription-plan-badge">Mest populær</div>
              ) : null}
              {isCurrentPlusPlan ? (
                <div className="subscription-plan-current-badge" aria-live="polite">
                  Nåværende plan <span aria-hidden="true">💎</span>
                </div>
              ) : null}
              <header className="subscription-plan-header">
                <h3>{config.name}</h3>
                <p>{config.subtitle}</p>
                <div className="subscription-plan-price">
                  <span>{config.pricing[billingInterval]}</span>
                  <span className="subscription-plan-period">{priceSuffix}</span>
                </div>
                {nextRenewal ? (
                  <p className="subscription-plan-renewal">Neste fornyelse: {nextRenewal}</p>
                ) : null}
              </header>
              <ul className="subscription-plan-features">
                {config.bullets.map((bullet) => (
                  <li key={bullet}>
                    <Check size={16} aria-hidden="true" />
                    <span>{bullet}</span>
                  </li>
                ))}
              </ul>
              <div className="subscription-plan-action">{planAction}</div>
              {manageAction}
            </article>
          );
        })}
      </section>

      {isPlusUser ? (
        <p className="subscription-portal-hint">
          Oppsigelse håndteres i
          {' '}
          <button
            type="button"
            className="subscription-portal-link"
            onClick={handlePortalCta}
            disabled={portalLoading}
          >
            {portalLoading ? "Åpner…" : "Stripe-portalen"}
          </button>
          .
        </p>
      ) : null}

      <section className="subscription-compare" aria-label="Funksjonstabell">
        <div className="subscription-compare-header">
          <h3>Funksjonssammenligning</h3>
          <p>Se hva som følger med i hver plan.</p>
        </div>
        <div className="subscription-table-wrapper">
          <table className="subscription-table">
            <thead>
              <tr>
                <th scope="col">Funksjon</th>
                <th scope="col">Gratis</th>
                <th scope="col">Pluss</th>
              </tr>
            </thead>
            <tbody>
              {FEATURE_COMPARISON.map((row) => (
                <tr key={row.id}>
                  <th scope="row">{row.label}</th>
                  <td>{renderFeatureValue(row.free, "table")}</td>
                  <td>{renderFeatureValue(row.plus, "table")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="subscription-accordion">
          {FEATURE_COMPARISON.map((row) => {
            const isOpen = openAccordion === row.id;
            return (
              <div
                key={row.id}
                className={isOpen ? "subscription-accordion-item is-open" : "subscription-accordion-item"}
              >
                <button
                  type="button"
                  className="subscription-accordion-trigger"
                  aria-expanded={isOpen}
                  onClick={() => setOpenAccordion(isOpen ? null : row.id)}
                >
                  <span>{row.label}</span>
                  <ChevronDown size={16} aria-hidden="true" />
                </button>
                <div className="subscription-accordion-panel" role="region" aria-hidden={!isOpen}>
                  <div className="subscription-accordion-plan">
                    <span className="subscription-accordion-plan-name">Gratis</span>
                    {renderFeatureValue(row.free, "accordion")}
                  </div>
                  <div className="subscription-accordion-plan">
                    <span className="subscription-accordion-plan-name">Pluss</span>
                    {renderFeatureValue(row.plus, "accordion")}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <p className="subscription-trust">{TRUST_TEXT}</p>

      {stickyVisible && (isFreeUser || !isLoggedIn) ? (
        <div
          className="subscription-mobile-cta"
          role="region"
          aria-live="polite"
          aria-label="Oppgraderingspåminnelse"
        >
          <div className="subscription-mobile-cta-text">
            <span>Gjør analysene dine ubegrensede med Pluss.</span>
          </div>
          <button
            type="button"
            className="subscription-mobile-cta-button"
            onClick={handlePlusCta}
          >
            {stickyCtaLabel}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ProfilePageFallback() {
  return (
    <main className="page-gradient">
      <PageContainer>
        <SiteHeader showAction actionHref="/analysis" actionLabel="Ny analyse" />
        <section className="profile-page">
          <div className="profile-placeholder">Laster profil…</div>
        </section>
        <SiteFooter />
      </PageContainer>
    </main>
  );
}

export default function ProfilePage() {
  return (
    <Suspense fallback={<ProfilePageFallback />}>
      <ProfilePageContent />
    </Suspense>
  );
}
