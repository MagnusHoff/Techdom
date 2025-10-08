"use client";

import { useEffect, useMemo, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "@/app/components/chrome";
import {
  adminChangeUserPassword,
  changeUserRole,
  deleteUser,
  fetchCurrentUser,
  fetchUsers,
  updateUserProfile,
} from "@/lib/api";
import type { AuthUser } from "@/lib/types";

const ROLE_LABELS: Record<AuthUser["role"], string> = {
  user: "Standard",
  plus: "Pro",
  admin: "Admin",
};

const ROLE_OPTIONS: AuthUser["role"][] = ["user", "plus", "admin"];

export default function UserAdminPage() {
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [usernameDrafts, setUsernameDrafts] = useState<Record<number, string>>({});
  const [passwordDrafts, setPasswordDrafts] = useState<Record<number, string>>({});
  const [pendingAction, setPendingAction] = useState<
    { userId: number; type: "role" | "username" | "password" | "delete" } | null
  >(null);

  useEffect(() => {
    let cancelled = false;
    fetchCurrentUser()
      .then((user) => {
        if (!cancelled) {
          setCurrentUser(user);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCurrentUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAuthChecked(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const isAdmin = currentUser?.role === "admin";

  useEffect(() => {
    if (!authChecked || !isAdmin) {
      return;
    }

    let cancelled = false;
    setLoadingUsers(true);
    setFetchError(null);

    const timeout = setTimeout(() => {
      fetchUsers({ search: search.trim() || undefined })
        .then((response) => {
          if (cancelled) {
            return;
          }
          setUsers(response.items);
          setTotal(response.total);
          setUsernameDrafts(
            response.items.reduce<Record<number, string>>((acc, item) => {
              acc[item.id] = item.username?.trim() ?? "";
              return acc;
            }, {}),
          );
          setPasswordDrafts({});
        })
        .catch((error) => {
          if (!cancelled) {
            const message =
              error instanceof Error ? error.message : "Kunne ikke hente brukere";
            setFetchError(message);
          }
        })
        .finally(() => {
          if (!cancelled) {
            setLoadingUsers(false);
          }
        });
    }, 250);

    return () => {
      cancelled = true;
      clearTimeout(timeout);
    };
  }, [authChecked, isAdmin, search]);

  const handleRoleChange = async (userId: number, nextRole: AuthUser["role"]) => {
    const existing = users.find((user) => user.id === userId);
    if (!existing || existing.role === nextRole) {
      return;
    }

    if (pendingAction && pendingAction.userId === userId && pendingAction.type === "role") {
      return;
    }

    setPendingAction({ userId, type: "role" });
    setUpdateError(null);
    setFeedback(null);

    try {
      const updated = await changeUserRole(userId, { role: nextRole });
      setUsers((prev) => prev.map((user) => (user.id === updated.id ? updated : user)));
      const displayName = updated.username?.trim() || updated.email;
      setFeedback(`Oppdatert ${displayName} til ${ROLE_LABELS[updated.role]}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Kunne ikke oppdatere bruker";
      setUpdateError(message);
    } finally {
      setPendingAction(null);
    }
  };

  const handleUsernameSave = async (userId: number) => {
    const existing = users.find((user) => user.id === userId);
    if (!existing) {
      return;
    }

    const draft = (usernameDrafts[userId] ?? "").trim();
    if (draft === (existing.username ?? "")) {
      setUpdateError(null);
      setFeedback("Ingen endringer å lagre.");
      return;
    }

    if (pendingAction && pendingAction.userId === userId && pendingAction.type === "username") {
      return;
    }

    setPendingAction({ userId, type: "username" });
    setUpdateError(null);
    setFeedback(null);

    try {
      const updated = await updateUserProfile(userId, { username: draft });
      setUsers((prev) => prev.map((user) => (user.id === updated.id ? updated : user)));
      setUsernameDrafts((prev) => ({ ...prev, [userId]: updated.username?.trim() ?? "" }));
      const displayName = updated.username?.trim() || updated.email;
      setFeedback(`Brukernavn oppdatert for ${displayName}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Kunne ikke oppdatere brukernavn";
      setUpdateError(message);
    } finally {
      setPendingAction(null);
    }
  };

  const handlePasswordSave = async (userId: number) => {
    const existing = users.find((user) => user.id === userId);
    if (!existing) {
      return;
    }

    const draft = (passwordDrafts[userId] ?? "").trim();
    if (!draft) {
      setFeedback(null);
      setUpdateError("Oppgi et nytt passord.");
      return;
    }

    if (pendingAction && pendingAction.userId === userId && pendingAction.type === "password") {
      return;
    }

    setPendingAction({ userId, type: "password" });
    setUpdateError(null);
    setFeedback(null);

    try {
      await adminChangeUserPassword(userId, { newPassword: draft });
      setPasswordDrafts((prev) => ({ ...prev, [userId]: "" }));
      const displayName = existing.username?.trim() || existing.email;
      setFeedback(`Passord oppdatert for ${displayName}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Kunne ikke oppdatere passord";
      setUpdateError(message);
    } finally {
      setPendingAction(null);
    }
  };

  const handleDeleteUser = async (userId: number) => {
    const existing = users.find((user) => user.id === userId);
    if (!existing) {
      return;
    }

    if (pendingAction && pendingAction.userId === userId && pendingAction.type === "delete") {
      return;
    }

    if (typeof window !== "undefined") {
      const confirmed = window.confirm("Er du sikker på at du vil slette denne brukeren?");
      if (!confirmed) {
        return;
      }
    }

    setPendingAction({ userId, type: "delete" });
    setUpdateError(null);
    setFeedback(null);

    try {
      await deleteUser(userId);
      setUsers((prev) => prev.filter((user) => user.id !== userId));
      setTotal((prev) => Math.max(prev - 1, 0));
      setUsernameDrafts((prev) => {
        const next = { ...prev };
        delete next[userId];
        return next;
      });
      setPasswordDrafts((prev) => {
        const next = { ...prev };
        delete next[userId];
        return next;
      });
      const displayName = existing.username?.trim() || existing.email;
      setFeedback(`Slettet ${displayName}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Kunne ikke slette bruker";
      setUpdateError(message);
    } finally {
      setPendingAction(null);
    }
  };

  const filteredTotalText = useMemo(() => {
    if (search.trim()) {
      return `${users.length} av ${total} bruker${total === 1 ? "" : "e"}`;
    }
    return `${total} bruker${total === 1 ? "" : "e"}`;
  }, [search, total, users.length]);

  return (
    <main className="page-gradient">
      <PageContainer>
        <SiteHeader showAction actionHref="/" actionLabel="Til forsiden" />
        <section className="admin-users">
          <div className="admin-users-header">
            <h1>Brukeradministrasjon</h1>
            {authChecked && isAdmin ? (
              <p>Administrer brukere: rediger rolle, navn, passord og søk i listen.</p>
            ) : null}
          </div>

          {!authChecked ? (
            <p className="admin-users-status">Sjekker tilgang...</p>
          ) : null}

          {authChecked && !isAdmin ? (
            <p className="admin-users-status admin-users-status--error">
              Du må være administrator for å se denne siden.
            </p>
          ) : null}

          {authChecked && isAdmin ? (
            <div className="admin-users-controls">
              <label className="admin-search-label" htmlFor="user-search">
                Søk på e-post eller brukernavn
              </label>
              <input
                id="user-search"
                type="search"
                placeholder="Søk etter bruker"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="admin-search-input"
              />
              <div className="admin-users-meta">
                <span>{loadingUsers ? "Laster..." : filteredTotalText}</span>
                {fetchError ? (
                  <span className="admin-users-status admin-users-status--error">{fetchError}</span>
                ) : null}
                {feedback ? (
                  <span className="admin-users-status admin-users-status--success">{feedback}</span>
                ) : null}
                {updateError ? (
                  <span className="admin-users-status admin-users-status--error">{updateError}</span>
                ) : null}
              </div>
            </div>
          ) : null}

          {authChecked && isAdmin ? (
            <div className="admin-users-table-wrap">
              {users.length === 0 && !loadingUsers && !fetchError ? (
                <p className="admin-users-status">Ingen brukere funnet.</p>
              ) : null}

              {users.length > 0 ? (
                <table className="admin-users-table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Brukernavn</th>
                      <th>E-post</th>
                      <th>Rolle</th>
                      <th>Status</th>
                      <th>Passord</th>
                      <th>Handlinger</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((user) => {
                      const usernameValue = usernameDrafts[user.id] ?? "";
                      const passwordValue = passwordDrafts[user.id] ?? "";
                      const isPendingForUser = pendingAction?.userId === user.id;
                      const pendingType = pendingAction?.type;
                      const rolePending = isPendingForUser && pendingType === "role";
                      const usernamePending = isPendingForUser && pendingType === "username";
                      const passwordPending = isPendingForUser && pendingType === "password";
                      const deletePending = isPendingForUser && pendingType === "delete";

                      return (
                        <tr key={user.id}>
                          <td>{user.id}</td>
                          <td>
                            <div className="admin-users-field">
                              <input
                                type="text"
                                value={usernameValue}
                                onChange={(event) =>
                                  setUsernameDrafts((prev) => ({
                                    ...prev,
                                    [user.id]: event.target.value,
                                  }))
                                }
                                placeholder="Brukernavn"
                                disabled={deletePending}
                                className="admin-users-input"
                              />
                              <button
                                type="button"
                                onClick={() => handleUsernameSave(user.id)}
                                disabled={
                                  usernamePending ||
                                  deletePending ||
                                  usernameValue.trim().length === 0
                                }
                                className="admin-users-button"
                              >
                                Lagre
                              </button>
                            </div>
                          </td>
                          <td>{user.email}</td>
                          <td>
                            <select
                              value={user.role}
                              onChange={(event) =>
                                handleRoleChange(user.id, event.target.value as AuthUser["role"])
                              }
                              disabled={rolePending || deletePending}
                              className="admin-role-select"
                            >
                              {ROLE_OPTIONS.map((role) => (
                                <option key={role} value={role}>
                                  {ROLE_LABELS[role]}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td>{user.is_active ? "Aktiv" : "Deaktivert"}</td>
                          <td>
                            <div className="admin-users-field">
                              <input
                                type="password"
                                value={passwordValue}
                                onChange={(event) =>
                                  setPasswordDrafts((prev) => ({
                                    ...prev,
                                    [user.id]: event.target.value,
                                  }))
                                }
                                placeholder="Nytt passord"
                                disabled={deletePending}
                                className="admin-users-input"
                              />
                              <button
                                type="button"
                                onClick={() => handlePasswordSave(user.id)}
                                disabled={
                                  passwordPending ||
                                  deletePending ||
                                  passwordValue.trim().length === 0
                                }
                                className="admin-users-button"
                              >
                                Oppdater
                              </button>
                            </div>
                          </td>
                          <td>
                            <button
                              type="button"
                              onClick={() => handleDeleteUser(user.id)}
                              disabled={deletePending}
                              className="admin-users-button admin-users-button--danger"
                            >
                              Slett
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : null}
            </div>
          ) : null}
        </section>
        <SiteFooter />
      </PageContainer>
    </main>
  );
}
