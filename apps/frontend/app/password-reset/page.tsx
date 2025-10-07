"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import { confirmPasswordReset } from "@/lib/api";

export default function PasswordResetPage(): JSX.Element {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [repeatPassword, setRepeatPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const tokenMissing = token.trim().length === 0;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (tokenMissing) {
      setError("Lenken mangler token. Be om en ny tilbakestillingslenke.");
      return;
    }

    if (!password || password.length < 8) {
      setError("Passordet må være minst 8 tegn.");
      return;
    }

    if (password !== repeatPassword) {
      setError("Passordene må være like.");
      return;
    }

    setLoading(true);
    try {
      await confirmPasswordReset({ token, password });
      setNotice("Passordet er oppdatert. Du kan nå logge inn med det nye passordet.");
      setPassword("");
      setRepeatPassword("");
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Kunne ikke tilbakestille passordet. Prøv igjen.";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="page-gradient">
      <PageContainer variant="narrow">
        <SiteHeader showAction actionHref="/" />
        <section className="reset-section">
          <div className="login-modal">
            <div className="login-modal-header">
              <h2>Tilbakestill passord</h2>
            </div>
            <p className="login-modal-description">
              Skriv inn et nytt passord for kontoen din. Lenken er gyldig i én time etter at du mottok den.
            </p>
            {error ? <div className="error-banner">{error}</div> : null}
            {notice ? <p className="login-notice">{notice}</p> : null}
            {notice ? null : (
              <form className="login-form" onSubmit={handleSubmit}>
                <label className="sr-only" htmlFor="reset-password">
                  Nytt passord
                </label>
                <input
                  id="reset-password"
                  type="password"
                  autoComplete="new-password"
                  placeholder="Nytt passord"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="login-input"
                  required
                  minLength={8}
                />
                <label className="sr-only" htmlFor="reset-password-repeat">
                  Gjenta passord
                </label>
                <input
                  id="reset-password-repeat"
                  type="password"
                  autoComplete="new-password"
                  placeholder="Gjenta passord"
                  value={repeatPassword}
                  onChange={(event) => setRepeatPassword(event.target.value)}
                  className="login-input"
                  required
                  minLength={8}
                />
                <button type="submit" className="login-submit" disabled={loading || tokenMissing}>
                  {loading ? "Lagrer..." : "Oppdater passord"}
                </button>
                <Link href="/" className="login-cancel">
                  Avbryt
                </Link>
              </form>
            )}
            <div className="login-secondary" style={{ justifyContent: "flex-end" }}>
              <Link href="/" className="login-secondary-link">
                Til forsiden
              </Link>
            </div>
          </div>
        </section>
        <SiteFooter />
      </PageContainer>
    </main>
  );
}
