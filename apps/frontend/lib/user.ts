import type { AuthUser } from "./types";

export function userInitials(user: AuthUser | null): string {
  const name = user?.username?.trim();
  if (name) {
    const parts = name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part.charAt(0).toUpperCase())
      .join("")
      .slice(0, 2);
    if (parts) {
      return parts;
    }
  }

  const email = user?.email?.trim();
  if (email) {
    const [localPart = ""] = email.split("@");
    const first = localPart.charAt(0).toUpperCase();
    const second = localPart.charAt(1)?.toUpperCase() ?? "";
    const initials = `${first}${second}`.trim();
    if (initials) {
      return initials;
    }
  }

  return "?";
}

export function userDisplayName(user: AuthUser | null, fallback = "Ukjent bruker"): string {
  if (!user) {
    return fallback;
  }
  const name = user.username?.trim();
  if (name) {
    return name;
  }
  const email = user.email?.trim();
  if (email) {
    const [localPart] = email.split("@");
    if (localPart) {
      return localPart;
    }
  }
  return fallback;
}

export function userRoleLabel(role: AuthUser["role"]): string {
  switch (role) {
    case "plus":
      return "Brukertype: Pluss";
    case "admin":
      return "Brukertype: Admin";
    default:
      return "Brukertype: User";
  }
}

export function userMembershipDuration(createdAt: string | null): string {
  if (!createdAt) {
    return "Medlem siden ukjent";
  }

  const timestamp = Date.parse(createdAt);
  if (Number.isNaN(timestamp)) {
    return "Medlem siden ukjent";
  }

  const joined = new Date(timestamp);
  const now = new Date();

  if (joined > now) {
    return "Medlem i dag";
  }

  let years = now.getFullYear() - joined.getFullYear();
  let months = now.getMonth() - joined.getMonth();
  let days = now.getDate() - joined.getDate();

  if (days < 0) {
    const previousMonth = new Date(now.getFullYear(), now.getMonth(), 0);
    days += previousMonth.getDate();
    months -= 1;
  }

  if (months < 0) {
    months += 12;
    years -= 1;
  }

  if (years < 0) {
    years = 0;
  }

  const parts: string[] = [];
  if (years > 0) {
    parts.push(`${years} år`);
  }
  if (months > 0) {
    parts.push(`${months} ${months === 1 ? "måned" : "måneder"}`);
  }
  if (days > 0) {
    const label = days === 1 ? "dag" : "dager";
    parts.push(`${days} ${label}`);
  }

  if (parts.length === 0) {
    return "Medlem i under en dag";
  }

  if (parts.length === 1) {
    return `Medlem i ${parts[0]}`;
  }

  if (parts.length === 2) {
    return `Medlem i ${parts[0]} og ${parts[1]}`;
  }

  const last = parts.pop();
  return `Medlem i ${parts.join(", ")} og ${last}`;
}

export function userBadgeLabel(totalAnalyses: number): string {
  if (!Number.isFinite(totalAnalyses) || totalAnalyses <= 0) {
    return "Analytiker nivå 1";
  }
  if (totalAnalyses >= 100) {
    return "Analytiker nivå 5";
  }
  if (totalAnalyses >= 50) {
    return "Analytiker nivå 4";
  }
  if (totalAnalyses >= 20) {
    return "Analytiker nivå 3";
  }
  if (totalAnalyses >= 5) {
    return "Analytiker nivå 2";
  }
  return "Analytiker nivå 1";
}
