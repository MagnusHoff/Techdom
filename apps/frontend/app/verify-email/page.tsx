"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import { verifyEmail } from "@/lib/api";

export default function VerifyEmailPage(): JSX.Element {
  return (
    <main className="page-gradient">
      <PageContainer variant="narrow">
        <SiteHeader showAction actionHref="/" actionLabel="Hjem" />
        <section className="reset-section">
          <div className="login-modal">
            <div className="login-modal-header">
              <h2>Bekreft e-post</h2>
            </div>
            <p className="login-modal-description">
              Vi bekrefter e-postadressen din slik at du kan bruke Techdom.ai. Det tar kun et øyeblikk.
            </p>
            <Suspense fallback={<VerifyEmailFallback />}>
              <VerifyEmailContent />
            </Suspense>
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

function VerifyEmailContent(): JSX.Element {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!token.trim()) {
      setStatus("error");
      setMessage("Lenken mangler token. Be om en ny verifiseringslenke.");
      return;
    }

    let cancelled = false;
    const run = async () => {
      setStatus("loading");
      try {
        await verifyEmail({ token });
        if (cancelled) {
          return;
        }
        setStatus("success");
        setMessage("E-postadressen er bekreftet. Du kan nå logge inn.");
      } catch (error) {
        if (cancelled) {
          return;
        }
        const detail =
          error instanceof Error
            ? error.message
            : "Kunne ikke verifisere e-postadressen akkurat nå.";
        setStatus("error");
        setMessage(detail);
      }
    };

    run();
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (status === "loading") {
    return (
      <div className="login-form" aria-busy="true">
        <p className="login-notice">Verifiserer e-post…</p>
      </div>
    );
  }

  if (status === "success") {
    return (
      <div className="login-form">
        <p className="login-notice">{message}</p>
        <Link href="/" className="login-submit" style={{ textAlign: "center" }}>
          Gå til innlogging
        </Link>
      </div>
    );
  }

  return (
    <div className="login-form">
      <div className="error-banner">{message ?? "Kunne ikke verifisere e-postadressen."}</div>
      <Link href="/" className="login-cancel">
        Til forsiden
      </Link>
    </div>
  );
}

function VerifyEmailFallback(): JSX.Element {
  return (
    <div className="login-form" aria-busy="true">
      <p className="login-notice">Laster verifisering…</p>
    </div>
  );
}
