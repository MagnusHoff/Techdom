"use client";

import { useEffect, useMemo, useState } from "react";

import { PageContainer, SiteFooter, SiteHeader } from "@/app/components/chrome";
import { changeUserRole, fetchCurrentUser, fetchUsers } from "@/lib/api";
import type { AuthUser } from "@/lib/types";

const ROLE_LABELS: Record<AuthUser["role"], string> = {
  user: "Standard",
  plus: "Pluss",
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
  const [pendingUserId, setPendingUserId] = useState<number | null>(null);

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
    if (pendingUserId === userId && users.find((user) => user.id === userId)?.role === nextRole) {
      return;
    }

    setPendingUserId(userId);
    setUpdateError(null);
    setFeedback(null);

    try {
      const updated = await changeUserRole(userId, { role: nextRole });
      setUsers((prev) => prev.map((user) => (user.id === updated.id ? updated : user)));
      setFeedback(`Oppdatert ${updated.email} til ${ROLE_LABELS[updated.role]}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Kunne ikke oppdatere bruker";
      setUpdateError(message);
    } finally {
      setPendingUserId(null);
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
            {authChecked && isAdmin ? <p>Administrer roller og søk blant brukere.</p> : null}
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
                Søk på e-post
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
                      <th>E-post</th>
                      <th>Rolle</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((user) => (
                      <tr key={user.id}>
                        <td>{user.id}</td>
                        <td>{user.email}</td>
                        <td>
                          <select
                            value={user.role}
                            onChange={(event) =>
                              handleRoleChange(user.id, event.target.value as AuthUser["role"])
                            }
                            disabled={pendingUserId === user.id}
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
                      </tr>
                    ))}
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
