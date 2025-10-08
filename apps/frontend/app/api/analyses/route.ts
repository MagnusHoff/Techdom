import { randomUUID } from "crypto";
import { promises as fs } from "fs";
import path from "path";
import { NextResponse } from "next/server";

import { extractFinnkode } from "@/lib/listing";

interface HistoryRecord {
  id?: string;
  ts?: string;
  finnkode?: string;
  finn_url?: string;
  title?: string;
  price?: number;
  summary?: string;
  image?: string;
  result_args?: Record<string, unknown> | null;
}

interface DecisionUiLike {
  scorelinjal?: { value?: number | null; farge?: string | null } | null;
  status?: { score?: number | null; dom?: string | null; setning?: string | null } | null;
  risiko?: Array<string | null | undefined> | null;
}

function resolveScore(resultArgs: Record<string, unknown> | null | undefined): number | null {
  if (!resultArgs || typeof resultArgs !== "object") {
    return null;
  }

  const decisionUi = resultArgs.decision_ui as DecisionUiLike | undefined;
  if (!decisionUi || typeof decisionUi !== "object") {
    return null;
  }

  const gaugeValue = decisionUi.scorelinjal?.value;
  if (typeof gaugeValue === "number" && Number.isFinite(gaugeValue)) {
    return gaugeValue;
  }

  const statusScore = decisionUi.status?.score;
  if (typeof statusScore === "number" && Number.isFinite(statusScore)) {
    return statusScore;
  }

  return null;
}

function resolveRisk(resultArgs: Record<string, unknown> | null | undefined): string | null {
  if (!resultArgs || typeof resultArgs !== "object") {
    return null;
  }
  const decisionUi = resultArgs.decision_ui as DecisionUiLike | undefined;
  if (!decisionUi) {
    return null;
  }
  const riskEntries = Array.isArray(decisionUi.risiko) ? decisionUi.risiko : [];
  const first = riskEntries.find((entry) => typeof entry === "string" && entry.trim().length > 0);
  return first ? first.trim() : null;
}

function safeExtractFinnkode(record: HistoryRecord): string | null {
  if (record.finnkode && typeof record.finnkode === "string" && record.finnkode.trim()) {
    return record.finnkode.trim();
  }
  if (record.finn_url && typeof record.finn_url === "string" && record.finn_url.trim()) {
    return extractFinnkode(record.finn_url.trim());
  }
  return null;
}

function normaliseTitle(record: HistoryRecord): string {
  const raw = typeof record.title === "string" ? record.title.trim() : "";
  if (raw) {
    return raw;
  }
  const fallbackUrl = typeof record.finn_url === "string" ? record.finn_url.trim() : "";
  if (fallbackUrl) {
    const finnkode = extractFinnkode(fallbackUrl);
    return finnkode ? `Finnkode ${finnkode}` : fallbackUrl;
  }
  return "Ukjent adresse";
}

export async function GET() {
  const projectRoot = path.resolve(process.cwd(), "..");
  const historyPath = path.join(projectRoot, "data", "cache", "analysis_history.jsonl");

  let payload: string;
  try {
    payload = await fs.readFile(historyPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return NextResponse.json({ items: [] });
    }
    console.error("Failed to read analysis history", error);
    return NextResponse.json({ error: "Kunne ikke hente analyser" }, { status: 500 });
  }

  const items = payload
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map<HistoryRecord>((line) => {
      try {
        return JSON.parse(line) as HistoryRecord;
      } catch (error) {
        console.warn("Ugyldig analyse-post i history", { error, line });
        return {} as HistoryRecord;
      }
    })
    .filter((record) => record && typeof record === "object");

  const mapped = items.map((record) => {
    const score = resolveScore(record.result_args);
    const risk = resolveRisk(record.result_args);
    const savedAt = typeof record.ts === "string" && record.ts.trim().length > 0 ? record.ts.trim() : null;
    const image = typeof record.image === "string" && record.image.trim().length > 0 ? record.image.trim() : null;

    return {
      id: record.id ?? safeExtractFinnkode(record) ?? randomUUID(),
      address: normaliseTitle(record),
      title: normaliseTitle(record),
      finnkode: safeExtractFinnkode(record),
      savedAt,
      image,
      summary: typeof record.summary === "string" ? record.summary : null,
      totalScore: score,
      riskLevel: risk,
      price: typeof record.price === "number" ? record.price : null,
      sourceUrl: typeof record.finn_url === "string" ? record.finn_url : null,
    };
  });

  mapped.sort((a, b) => {
    const aTime = a.savedAt ? Date.parse(a.savedAt) : 0;
    const bTime = b.savedAt ? Date.parse(b.savedAt) : 0;
    return bTime - aTime;
  });

  return NextResponse.json({ items: mapped });
}
