"use client";

import Link from "next/link";
import type { Route } from "next";
import { FormEvent, useEffect, useMemo, useState } from "react";

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

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 16);
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

  const headerClass = scrolled ? "site-header is-scrolled" : "site-header";

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

  const modalDescription = useMemo(() => {
    if (authMode === "forgot") {
      return "Vi sender deg en lenke for å tilbakestille passordet ditt når systemet er klart.";
    }
    if (authMode === "signup") {
      return "Registrering av nye brukere blir tilgjengelig snart. Legg inn e-posten din så er du klar.";
    }
    return "Logg inn for å lagre analyser og synkronisere på tvers av enheter. Backend-koblingen kommer snart.";
  }, [authMode]);

  const handleLoginSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // TODO: Koble til faktisk autentisering når backend er klar.
  };

  const closeModal = () => {
    setLoginOpen(false);
    setAuthMode("login");
    setPassword("");
  };

  return (
    <>
      <header className={headerClass}>
        <Link href="/" className="brand-pill">
          Techdom.AI – eiendomsanalyse
        </Link>
        <div className="header-actions">
          {showAction ? (
            <Link href={actionHref} className="header-action">
              {actionLabel}
            </Link>
          ) : null}
          <button
            type="button"
            className="header-login"
            onClick={() => setLoginOpen(true)}
          >
            Logg inn
          </button>
        </div>
      </header>

      {loginOpen ? (
        <div
          className="login-modal-backdrop"
          role="dialog"
          aria-modal="true"
          onClick={closeModal}
        >
          <div className="login-modal" onClick={(event) => event.stopPropagation()}>
            <div className="login-modal-header">
              <h2>{modalHeadline}</h2>
              <button type="button" className="login-close" onClick={closeModal} aria-label="Lukk innlogging">
                ×
              </button>
            </div>
            <p className="login-modal-description">{modalDescription}</p>
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
              {authMode === "login" || authMode === "signup" ? (
                <>
                  <label className="sr-only" htmlFor="header-login-password">
                    Passord
                  </label>
                  <input
                    id="header-login-password"
                    type="password"
                    autoComplete={authMode === "signup" ? "new-password" : "current-password"}
                    placeholder="Passord"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    className="login-input"
                    required={authMode !== "forgot"}
                    disabled={authMode === "forgot"}
                  />
                </>
              ) : null}
              <button type="submit" className="login-submit">
                {authMode === "signup" ? "Registrer" : authMode === "forgot" ? "Send lenke" : "Logg inn"}
              </button>
              <button type="button" className="login-cancel" onClick={closeModal}>
                Avbryt
              </button>
            </form>
            <div className="login-secondary">
              <button
                type="button"
                className={authMode === "forgot" ? "login-secondary-active" : "login-secondary-link"}
                onClick={() => setAuthMode("forgot")}
              >
                Glemt passord?
              </button>
              <button
                type="button"
                className={authMode === "signup" ? "login-secondary-active" : "login-secondary-link"}
                onClick={() => setAuthMode("signup")}
              >
                Lag ny bruker
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
        <a href="mailto:techdom.ai@techdom.com">Mail: techdom.ai@techdom.com</a>
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
