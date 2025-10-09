import type { AuthUser } from "./types";

export interface UserLevelDefinition {
  level: number;
  title: string;
  label: string;
  minAnalyses: number;
  maxAnalyses: number | null;
  color: string;
  emoji?: string;
  glow: string;
}

export interface UserLevel extends UserLevelDefinition {
  analyses: number;
}

const USER_LEVELS: UserLevelDefinition[] = [
  {
    level: 5,
    title: "Dominus",
    label: "Dominus nivå 5 💎",
    minAnalyses: 1000,
    maxAnalyses: null,
    color: "#FFD700",
    emoji: "💎",
    glow: "0 0 18px rgba(255, 215, 0, 0.6)",
  },
  {
    level: 4,
    title: "Eiendomsstrateg",
    label: "Eiendomsstrateg nivå 4",
    minAnalyses: 200,
    maxAnalyses: 999,
    color: "#8B5CF6",
    glow: "0 0 14px rgba(139, 92, 246, 0.4)",
  },
  {
    level: 3,
    title: "Porteføljebygger",
    label: "Porteføljebygger nivå 3",
    minAnalyses: 50,
    maxAnalyses: 199,
    color: "#F59E0B",
    glow: "0 0 12px rgba(245, 158, 11, 0.35)",
  },
  {
    level: 2,
    title: "Markedstolker",
    label: "Markedstolker nivå 2",
    minAnalyses: 10,
    maxAnalyses: 49,
    color: "#22C55E",
    glow: "0 0 10px rgba(34, 197, 94, 0.3)",
  },
  {
    level: 1,
    title: "Observatør",
    label: "Observatør nivå 1",
    minAnalyses: 0,
    maxAnalyses: 9,
    color: "#A78BFA",
    glow: "0 0 10px rgba(167, 139, 250, 0.3)",
  },
];

export function listUserLevels(): UserLevelDefinition[] {
  return [...USER_LEVELS].reverse();
}

function normaliseAnalysesCount(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.floor(value));
}

export function resolveUserLevel(totalAnalyses: number): UserLevel {
  const analyses = normaliseAnalysesCount(totalAnalyses);
  const fallback = USER_LEVELS[USER_LEVELS.length - 1];
  const match = USER_LEVELS.find((candidate) => analyses >= candidate.minAnalyses) ?? fallback;
  return { ...match, analyses };
}

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
  return resolveUserLevel(totalAnalyses).label;
}
