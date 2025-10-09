"use client";

import Link from "next/link";
import type { Route } from "next";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchCurrentUser,
  loginUser,
  logoutUser,
  registerUser,
  requestPasswordReset,
  resendVerificationEmail,
} from "@/lib/api";
import type { AuthUser } from "@/lib/types";

const USERNAME_PATTERN = /^[a-zA-Z0-9._]{3,20}$/;
const PASSWORD_PATTERN =
  /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]).{8,}$/;
const PASSWORD_REQUIREMENT_MESSAGE =
  "Passordet må være minst 8 tegn og inneholde store og små bokstaver, tall og spesialtegn.";

type PasswordStrengthLevel = "weak" | "medium" | "strong";

interface PasswordStrength {
  level: PasswordStrengthLevel;
  label: "Weak" | "Medium" | "Strong";
}

function evaluatePasswordStrength(value: string): PasswordStrength {
  if (!value) {
    return { level: "weak", label: "Weak" };
  }

  const variety =
    Number(/[a-z]/.test(value)) +
    Number(/[A-Z]/.test(value)) +
    Number(/\d/.test(value)) +
    Number(/[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]/.test(value));

  const lengthScore = value.length >= 14 ? 3 : value.length >= 12 ? 2 : value.length >= 8 ? 1 : 0;

  if (PASSWORD_PATTERN.test(value) && lengthScore >= 2 && variety >= 3) {
    return { level: "strong", label: "Strong" };
  }

  if (PASSWORD_PATTERN.test(value) && (lengthScore >= 1 || variety >= 3)) {
    return { level: "medium", label: "Medium" };
  }

  return { level: variety >= 2 ? "medium" : "weak", label: variety >= 2 ? "Medium" : "Weak" };
}

const USER_UPDATED_EVENT = "techdom:user-updated";
export const AUTH_MODAL_EVENT = "techdom:auth-modal";

interface AuthModalEventDetail {
  open?: boolean;
  mode?: "login" | "forgot" | "signup";
}

interface SiteHeaderProps {
  showAction?: boolean;
  actionHref?: Route;
  actionLabel?: string;
}

export function SiteHeader({
  showAction = false,
  actionHref = "/",
  actionLabel = "Ny analyse",
}: SiteHeaderProps) {
  const [scrolled, setScrolled] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "forgot" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [username, setUsername] = useState("");
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loginNotice, setLoginNotice] = useState<string | null>(null);
  const [loginLoading, setLoginLoading] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [unverifiedEmail, setUnverifiedEmail] = useState<string | null>(null);
  const [resendCooldown, setResendCooldown] = useState(0);
  const [resendLoading, setResendLoading] = useState(false);
  const [resendFeedback, setResendFeedback] = useState<
    { type: "info" | "error"; message: string } | null
  >(null);
  const ignoreBackdropClickRef = useRef(false); // Avoid closing when dragging from inside modal
  const userMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!unverifiedEmail) {
      return;
    }
    setResendCooldown((current) => (current > 0 ? current : 30));
  }, [unverifiedEmail]);

  useEffect(() => {
    if (resendCooldown <= 0) {
      return;
    }
    const timer = window.setInterval(() => {
      setResendCooldown((value) => (value > 1 ? value - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [resendCooldown]);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled((previous) => {
        const y = window.scrollY || window.pageYOffset;
        const next = previous ? y > 6 : y > 32;
        return next === previous ? previous : next;
      });
    };
    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    if (!loginOpen) {
      return;
    }
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setLoginOpen(false);
        setAuthMode("login");
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [loginOpen]);

  useEffect(() => {
    const handleAuthModal = (event: Event) => {
      const detail = (event as CustomEvent<AuthModalEventDetail | undefined>).detail ?? {};
      const shouldOpen = detail.open ?? true;

      if (shouldOpen) {
        if (detail.mode) {
          setAuthMode(detail.mode);
        }
        setLoginOpen(true);
        setLoginError(null);
        setLoginNotice(null);
        setResendFeedback(null);
        setResendCooldown(0);
        setPasswordVisible(false);
        if (detail.mode !== "signup") {
          setUsername("");
        }
        setEmail("");
        setPassword("");
        return;
      }

      setLoginOpen(false);
      setAuthMode("login");
      setPasswordVisible(false);
      setLoginError(null);
      setLoginNotice(null);
      setResendFeedback(null);
      setResendCooldown(0);
      setEmail("");
      setPassword("");
      setUsername("");
    };

    window.addEventListener(AUTH_MODAL_EVENT, handleAuthModal as EventListener);
    return () => window.removeEventListener(AUTH_MODAL_EVENT, handleAuthModal as EventListener);
  }, []);

  const headerClass = scrolled ? "site-header is-scrolled" : "site-header";

  useEffect(() => {
    fetchCurrentUser()
      .then((user) => {
        setCurrentUser(user);
        emitUserUpdate(user);
      })
      .catch(() => {
        setCurrentUser(null);
        emitUserUpdate(null);
      });
  }, []);

  useEffect(() => {
    const listener: EventListener = (event) => {
      const detail = (event as CustomEvent<AuthUser | null>).detail ?? null;
      setCurrentUser(detail);
    };

    window.addEventListener(USER_UPDATED_EVENT, listener);
    return () => {
      window.removeEventListener(USER_UPDATED_EVENT, listener);
    };
  }, []);

  const emitUserUpdate = (user: AuthUser | null) => {
    if (typeof window === "undefined") {
      return;
    }
    window.dispatchEvent(new CustomEvent<AuthUser | null>(USER_UPDATED_EVENT, { detail: user }));
  };

  useEffect(() => {
    if (!currentUser) {
      setUserMenuOpen(false);
    }
  }, [currentUser]);

  const modalHeadline = useMemo(() => {
    switch (authMode) {
      case "forgot":
        return "Glemt passord";
      case "signup":
        return "Opprett konto";
      default:
        return "Logg inn";
    }
  }, [authMode]);

  const modalDescription = useMemo<string | null>(() => {
    if (authMode === "forgot") {
      return "Vi sender deg en lenke for å tilbakestille passordet ditt når systemet er klart.";
    }
    return null;
  }, [authMode]);

  const resendButtonLabel = useMemo(() => {
    if (resendLoading) {
      return "Sender...";
    }
    if (resendCooldown > 0) {
      return `Send verifiseringsmail på nytt (${resendCooldown}s)`;
    }
    return "Send verifiseringsmail på nytt";
  }, [resendLoading, resendCooldown]);

  const passwordStrength = useMemo(() => evaluatePasswordStrength(password), [password]);
  const passwordMeetsRequirements = useMemo(
    () => PASSWORD_PATTERN.test(password),
    [password],
  );

  const userDisplayName = useMemo(() => {
    if (!currentUser) {
      return "";
    }
    const usernameValue = currentUser.username?.trim();
    return usernameValue && usernameValue.length > 0 ? usernameValue : currentUser.email;
  }, [currentUser]);

  useEffect(() => {
    if (!userMenuOpen) {
      return;
    }
    const handleDocumentClick = (event: MouseEvent) => {
      if (!userMenuRef.current) {
        return;
      }
      if (!userMenuRef.current.contains(event.target as Node)) {
        setUserMenuOpen(false);
      }
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setUserMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleDocumentClick);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleDocumentClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [userMenuOpen]);

  const handleLoginSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoginError(null);
    setLoginNotice(null);
    setResendFeedback(null);

    if (authMode === "forgot") {
      setLoginLoading(true);
      try {
        await requestPasswordReset({ email });
        setLoginNotice(
          "Hvis vi finner e-posten din sender vi en lenke for å tilbakestille passordet."
        );
        setAuthMode("login");
        setPassword("");
        setPasswordVisible(false);
        setUnverifiedEmail(null);
        setResendCooldown(0);
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "Ukjent feil under forespørsel om tilbakestilling";
        setLoginError(message);
      } finally {
        setLoginLoading(false);
      }
      return;
    }

    if (authMode === "signup") {
      const trimmedUsername = username.trim();
      if (!USERNAME_PATTERN.test(trimmedUsername)) {
        setLoginError(
          "Brukernavn må være 3-20 tegn og kan kun inneholde bokstaver, tall, punktum og understrek.",
        );
        return;
      }

      if (!passwordMeetsRequirements) {
        setLoginError(PASSWORD_REQUIREMENT_MESSAGE);
        return;
      }
    }

    setLoginLoading(true);
    try {
      if (authMode === "signup") {
        const trimmedUsername = username.trim();
        await registerUser({ email, username: trimmedUsername, password });
        setLoginNotice(
          "Konto opprettet! Sjekk e-posten din for å bekrefte adressen før du logger inn."
        );
        setAuthMode("login");
        setPassword("");
        setUsername("");
        setPasswordVisible(false);
        setUnverifiedEmail(email.trim().toLowerCase());
        setResendCooldown(30);
        return;
      }

      const result = await loginUser({ email, password });
      setCurrentUser(result.user);
      emitUserUpdate(result.user);
      setLoginOpen(false);
      setAuthMode("login");
      setEmail("");
      setPassword("");
      setUsername("");
      setPasswordVisible(false);
      setLoginNotice(null);
      setUnverifiedEmail(null);
      setResendFeedback(null);
      setResendCooldown(0);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Ukjent feil under innlogging";
      setLoginError(message);
      const normalizedEmail = email.trim().toLowerCase();
      if (normalizedEmail && message.toLowerCase().includes("verifiser")) {
        setUnverifiedEmail(normalizedEmail);
        setResendCooldown(30);
      } else {
        setUnverifiedEmail(null);
        setResendCooldown(0);
      }
    } finally {
      setLoginLoading(false);
    }
  };

  const handleResendVerification = async () => {
    if (!unverifiedEmail || resendLoading) {
      return;
    }
    setResendLoading(true);
    setResendFeedback(null);
    try {
      await resendVerificationEmail({ email: unverifiedEmail });
      setResendFeedback({ type: "info", message: "Ny verifiseringsmail er sendt." });
      setResendCooldown(30);
    } catch (error) {
      const retryAfter =
        error && typeof (error as { retryAfter?: number }).retryAfter === "number"
          ? Math.max(0, Math.trunc((error as { retryAfter?: number }).retryAfter ?? 0))
          : 0;
      if (retryAfter > 0) {
        setResendCooldown(retryAfter);
      }
      const message =
        error instanceof Error
          ? error.message
          : "Kunne ikke sende verifiseringsmail. Prøv igjen om litt.";
      setResendFeedback({ type: "error", message });
    } finally {
      setResendLoading(false);
    }
  };

  const closeModal = () => {
    ignoreBackdropClickRef.current = false;
    setLoginOpen(false);
    setAuthMode("login");
    setPassword("");
    setUsername("");
    setPasswordVisible(false);
    setLoginError(null);
    setLoginNotice(null);
    setUnverifiedEmail(null);
    setResendFeedback(null);
    setResendCooldown(0);
  };

  const handleBackdropClick = () => {
    if (ignoreBackdropClickRef.current) {
      ignoreBackdropClickRef.current = false;
      return;
    }
    closeModal();
  };
  const handleLogout = async () => {
    try {
      await logoutUser();
    } finally {
      setUserMenuOpen(false);
      setCurrentUser(null);
      emitUserUpdate(null);
      setEmail("");
      setPassword("");
      setUsername("");
      setPasswordVisible(false);
    }
  };

  return (
    <>
      <header className={headerClass}>
        <Link href="/" className="brand-pill">
          Techdom.AI
        </Link>
        <div className="header-actions">
          {showAction ? (
            <Link href={actionHref} className="header-action">
              {actionLabel}
            </Link>
          ) : null}
          {currentUser?.role === "admin" ? (
            <Link href="/admin/users" className="header-action header-action--secondary">
              Brukere
            </Link>
          ) : null}
          {currentUser ? (
            <div className="header-user-menu" ref={userMenuRef}>
              <button
                type="button"
                className="header-user-toggle"
                aria-haspopup="true"
                aria-expanded={userMenuOpen}
                aria-controls="header-user-dropdown"
                onClick={() => setUserMenuOpen((open) => !open)}
              >
                <span className="header-user-label">{userDisplayName}</span>
                <span
                  className={userMenuOpen ? "header-user-chevron is-open" : "header-user-chevron"}
                  aria-hidden="true"
                >
                  ▾
                </span>
              </button>
              {userMenuOpen ? (
                <div className="header-user-dropdown" id="header-user-dropdown">
                  <Link href="/profile" className="header-user-item" onClick={() => setUserMenuOpen(false)}>
                    Mine sider
                  </Link>
                  <Link
                    href="/profile?section=subscription"
                    className="header-user-item"
                    onClick={() => setUserMenuOpen(false)}
                  >
                    Abonnement
                  </Link>
                  <button
                    type="button"
                    className="header-user-item header-user-logout"
                    onClick={handleLogout}
                  >
                    Logg ut
                  </button>
                </div>
              ) : null}
            </div>
          ) : (
            <button
              type="button"
              className="header-login"
              onClick={() => {
                setLoginOpen(true);
                setPasswordVisible(false);
              }}
            >
              Logg inn
            </button>
          )}
        </div>
      </header>

      {loginOpen ? (
        <div
          className="login-modal-backdrop"
          role="dialog"
          aria-modal="true"
          onClick={handleBackdropClick}
        >
          <div
            className="login-modal"
            onClick={(event) => event.stopPropagation()}
            onMouseDown={() => {
              ignoreBackdropClickRef.current = true;
            }}
            onMouseUp={() => {
              ignoreBackdropClickRef.current = false;
            }}
          >
            <div className="login-modal-header">
              <h2>{modalHeadline}</h2>
              <button type="button" className="login-close" onClick={closeModal} aria-label="Lukk innlogging">
                ×
              </button>
            </div>
            {modalDescription ? (
              <p className="login-modal-description">{modalDescription}</p>
            ) : null}
            {loginError ? <div className="error-banner">{loginError}</div> : null}
            {loginNotice ? <p className="login-notice">{loginNotice}</p> : null}
            {authMode === "login" && unverifiedEmail ? (
              <div className="login-resend">
                <p className="login-resend-hint">
                  Fikk du ikke e-posten? Send verifiseringsmail på nytt.
                </p>
                {resendFeedback ? (
                  <div
                    className={
                      resendFeedback.type === "error" ? "error-banner" : "login-notice"
                    }
                  >
                    {resendFeedback.message}
                  </div>
                ) : null}
                <button
                  type="button"
                  className="login-resend-button"
                  onClick={handleResendVerification}
                  disabled={resendLoading || resendCooldown > 0}
                >
                  {resendButtonLabel}
                </button>
              </div>
            ) : null}
            <form className="login-form" onSubmit={handleLoginSubmit}>
              <label className="sr-only" htmlFor="header-login-email">
                E-post
              </label>
              <input
                id="header-login-email"
                type="email"
                autoComplete="email"
                placeholder="din@epost.no"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="login-input"
                required
              />
              {authMode === "signup" ? (
                <>
                  <label className="sr-only" htmlFor="header-login-username">
                    Brukernavn
                  </label>
                  <input
                    id="header-login-username"
                    type="text"
                    autoComplete="username"
                    placeholder="Brukernavn"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    className="login-input"
                    required
                    minLength={3}
                    maxLength={20}
                    pattern="[A-Za-z0-9._]{3,20}"
                  />
                </>
              ) : null}
              {authMode === "login" || authMode === "signup" ? (
                <>
                  <label className="sr-only" htmlFor="header-login-password">
                    Passord
                  </label>
                  <div className="password-field">
                    <input
                      id="header-login-password"
                      type={passwordVisible ? "text" : "password"}
                      autoComplete={authMode === "signup" ? "new-password" : "current-password"}
                      placeholder="Passord"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      className="login-input password-input"
                      required
                    />
                    <button
                      type="button"
                      className="password-toggle"
                      onClick={() => setPasswordVisible((visible) => !visible)}
                      aria-label={passwordVisible ? "Skjul passord" : "Vis passord"}
                    >
                      {passwordVisible ? "Skjul" : "Vis"}
                    </button>
                  </div>
                  {authMode === "signup" && password ? (
                    <div className={`password-strength password-strength--${passwordStrength.level}`}>
                      Passordstyrke: {passwordStrength.label}
                    </div>
                  ) : null}
                  {authMode === "signup" && password && !passwordMeetsRequirements ? (
                    <p className="password-requirements">{PASSWORD_REQUIREMENT_MESSAGE}</p>
                  ) : null}
                </>
              ) : null}
              <button type="submit" className="login-submit" disabled={loginLoading}>
                {loginLoading
                  ? "Jobber..."
                  : authMode === "signup"
                  ? "Registrer"
                  : authMode === "forgot"
                  ? "Send lenke"
                  : "Logg inn"}
              </button>
              <button type="button" className="login-cancel" onClick={closeModal}>
                Avbryt
              </button>
            </form>
            <div className="login-secondary">
              <button
                type="button"
                className={authMode === "forgot" ? "login-secondary-active" : "login-secondary-link"}
                onClick={() => {
                  setAuthMode("forgot");
                  setPasswordVisible(false);
                  setUnverifiedEmail(null);
                  setResendFeedback(null);
                  setResendCooldown(0);
                }}
              >
                Glemt passord?
              </button>
              <button
                type="button"
                className={authMode === "signup" ? "login-secondary-active" : "login-secondary-link"}
                onClick={() => {
                  const nextMode = authMode === "signup" ? "login" : "signup";
                  setAuthMode(nextMode);
                  setPasswordVisible(false);
                  if (nextMode !== "signup") {
                    setUsername("");
                  }
                  if (nextMode !== "login") {
                    setUnverifiedEmail(null);
                    setResendFeedback(null);
                    setResendCooldown(0);
                  }
                }}
              >
                {authMode === "signup" ? "Logg inn" : "Lag ny bruker"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

type PageContainerVariant = "default" | "narrow";

interface PageContainerProps {
  children: React.ReactNode;
  variant?: PageContainerVariant;
}

export function PageContainer({ children, variant = "default" }: PageContainerProps) {
  const classes = ["page-shell"];
  if (variant === "narrow") {
    classes.push("page-shell--narrow");
  }
  return <div className={classes.join(" ")}>{children}</div>;
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-links">
        <a href="https://instagram.com/techdom.ai" target="_blank" rel="noreferrer">
          Instagram: techdom.ai
        </a>
        <span className="footer-separator">·</span>
        <a href="https://www.tiktok.com/@techdom.ai" target="_blank" rel="noreferrer">
          TikTok: techdom.ai
        </a>
        <span className="footer-separator">·</span>
        <a href="mailto:support@techdom.ai">Mail: support@techdom.ai</a>
      </div>
      <p>
        Techdom.ai tilbyr kun generell og veiledende informasjon. Vi garanterer ikke at analysene er
        fullstendige, korrekte eller oppdaterte, og vi fraskriver oss ethvert ansvar for tap eller beslutninger
        basert på informasjon fra plattformen. All bruk skjer på eget ansvar, og vi anbefaler å søke profesjonell
        rådgivning før du tar investeringsbeslutninger.
      </p>
    </footer>
  );
}
