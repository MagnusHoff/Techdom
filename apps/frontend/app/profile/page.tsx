"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import { changePassword, fetchCurrentUser, logoutUser, updateUsername } from "@/lib/api";
import { Lock, LogOut, User } from "lucide-react";
import type { AuthUser } from "@/lib/types";

const USER_UPDATED_EVENT = "techdom:user-updated";
const USERNAME_PATTERN = /^[a-zA-Z0-9._]{3,20}$/;
const PASSWORD_PATTERN =
  /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]).{8,}$/;
const PASSWORD_REQUIREMENT_MESSAGE =
  "Passordet må være minst 8 tegn og inneholde store og små bokstaver, tall og spesialtegn.";

function buildInitials(user: AuthUser | null): string {
  const name = user?.username?.trim();
  if (name) {
    return name
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part.charAt(0).toUpperCase())
      .join("")
      .slice(0, 2) || name.charAt(0).toUpperCase();
  }
  const email = user?.email ?? "";
  if (email) {
    const first = email.charAt(0).toUpperCase();
    const second = email.split("@")[0].charAt(1)?.toUpperCase();
    return (first + (second ?? "")).slice(0, 2);
  }
  return "?";
}

function displayName(user: AuthUser | null): string {
  if (!user) {
    return "";
  }
  if (user.username && user.username.trim()) {
    return user.username.trim();
  }
  return "Ikke satt";
}

export default function ProfilePage() {
  const router = useRouter();
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

  const initials = useMemo(() => buildInitials(user), [user]);
  const friendlyName = useMemo(() => displayName(user), [user]);

  const emitUserUpdate = (nextUser: AuthUser | null) => {
    if (typeof window === "undefined") {
      return;
    }
    window.dispatchEvent(
      new CustomEvent<AuthUser | null>(USER_UPDATED_EVENT, { detail: nextUser }),
    );
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
      setUser(updated);
      setUsernameValue(updated.username?.trim() ?? "");
      setUsernameMessage("Brukernavnet er oppdatert.");
      emitUserUpdate(updated);
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

  return (
    <main className="page-gradient">
      <PageContainer>
        <SiteHeader showAction actionHref="/analysis" actionLabel="Ny analyse" />

        <section className="profile-page">
          <header className="profile-header">
            <div className="profile-breadcrumb" aria-label="Brødsmule">
              <span>Konto</span>
              <span className="profile-breadcrumb-separator">/</span>
              <span>Min profil</span>
            </div>
            <div className="profile-title-group">
              <h1>Min profil</h1>
              <p className="profile-subtitle">
                Oppdater informasjonen din og administrer tilgang til Techdom.ai.
              </p>
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
            <div className="profile-card">
              <div className="profile-main">
                <div className="profile-avatar" aria-hidden="true">
                  {initials}
                </div>
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
                    <span className="profile-label">E-post bekreftet</span>
                    <p
                      className={
                        user?.is_email_verified
                          ? "profile-value profile-value--success"
                          : "profile-value profile-value--warning"
                      }
                    >
                      {user?.is_email_verified ? "Ja" : "Nei"}
                    </p>
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
          )}
        </section>

        <SiteFooter />
      </PageContainer>
    </main>
  );
}
