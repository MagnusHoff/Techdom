"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import UserEmojiAvatar from "../components/user-avatar";
import { changePassword, fetchCurrentUser, logoutUser, updateUsername } from "@/lib/api";
import { userDisplayName, userInitials } from "@/lib/user";
import { Lock, LogOut, User } from "lucide-react";
import type { AuthUser } from "@/lib/types";

const USER_UPDATED_EVENT = "techdom:user-updated";
const USERNAME_PATTERN = /^[a-zA-Z0-9._]{3,20}$/;
const PASSWORD_PATTERN =
  /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]).{8,}$/;
const PASSWORD_REQUIREMENT_MESSAGE =
  "Passordet må være minst 8 tegn og inneholde store og små bokstaver, tall og spesialtegn.";

type SectionId = "profile" | "subscription" | "settings";

interface SectionConfig {
  id: SectionId;
  label: string;
  comingSoon: boolean;
}

const SECTIONS: SectionConfig[] = [
  { id: "profile", label: "Min profil", comingSoon: false },
  { id: "subscription", label: "Abonnement", comingSoon: true },
  { id: "settings", label: "Innstillinger", comingSoon: true },
];

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
  const [activeSection, setActiveSection] = useState<SectionId>("profile");

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
      case "settings": {
        const heading = activeSection === "subscription" ? "Abonnement" : "Innstillinger";
        return (
          <div className="profile-card profile-card--placeholder" role="status">
            <div className="profile-placeholder-content">
              <h2>{heading}</h2>
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
