"use client";

/* eslint-disable @next/next/no-img-element */

import { useRouter, useSearchParams } from "next/navigation";
import {
  ChangeEvent,
  DragEvent,
  FormEvent,
  MouseEvent,
  PointerEvent as ReactPointerEvent,
  Suspense,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type SyntheticEvent,
} from "react";

import { PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import { analyzeProspectusPdf, analyzeProspectusText, getJobStatus, runAnalysis, startAnalysisJob } from "@/lib/api";
import type {
  AnalysisPayload,
  AnalysisResponse,
  DecisionUi,
  JobStatus,
  KeyFactRaw,
  ListingDetailsDTO,
  ProspectusExtract,
  ProspectusLinks,
  ProspectusDetail,
} from "@/lib/types";

const DEFAULT_FORM: AnalysisPayload = {
  price: "",
  equity: "",
  interest: "5.0",
  term_years: "25",
  rent: "",
  hoa: "",
  maint_pct: "6.0",
  vacancy_pct: "0",
  other_costs: "0",
};

const FORM_FIELD_TOOLTIPS: Record<string, string> = {
  Kjøpesum: "Totalpris for eiendommen, inkludert omkostninger du betaler ved kjøp.",
  Egenkapital: "Beløpet du finansierer selv før banklånet tas opp.",
  "Rente % p.a.": "Årlig nominell rente på boliglånet, oppgitt i prosent.",
  "Lånetid (år)": "Antall år du planlegger å bruke på å nedbetale lånet.",
  "Leie (mnd)": "Forventet månedlig husleie som leietakerne betaler.",
  "Felleskost (mnd)": "Månedlige felleskostnader til borettslag eller sameie.",
  "Vedlikehold % av leie": "Andel av husleien du setter av til vedlikehold hver måned.",
  "Andre kost (mnd)": "Andre faste månedlige kostnader knyttet til eiendommen.",
  "Ledighet %": "Forventet del av året boligen står tom uten leietaker.",
};

const PROSPECTUS_CARD_TOOLTIPS: Record<string, string> = {
  "⚠️ TG2": "Tilstandsgrad 2: merkbare avvik som bør følges opp eller utbedres på sikt.",
  "🛑 TG3": "Tilstandsgrad 3: alvorlige avvik som krever rask utbedring eller nærmere undersøkelser.",
};

const JOB_POLL_INTERVAL = 2_500;
const STRONG_RED_HEX = "#c1121f";
const IMAGE_IDENTITY_SEGMENT_IGNORE = new Set([
  "original",
  "full",
  "fullsize",
  "master",
  "scaled",
  "scale",
  "web",
  "desktop",
  "mobile",
  "widescreen",
  "optimized",
  "variant",
  "quality",
]);

const FLOORPLAN_KEYWORDS = [
  "floorplan",
  "floorplans",
  "floor plan",
  "floor-plan",
  "floor_plan",
  "plantegning",
  "plantegninger",
  "planskisse",
  "planskisser",
  "planlosning",
  "planlosninger",
  "planløsning",
  "planløsninger",
  "romskisse",
  "romskisser",
  "romplan",
  "situasjonsplan",
  "situasjonskart",
  "arealplan",
  "arealplaner",
  "etasjeplan",
  "boligplan",
] as const;

const FLOORPLAN_FLAG_KEYS = [
  "is_floorplan",
  "isFloorplan",
  "floorplan",
  "floorPlan",
  "floor_plan",
  "er_plantegning",
] as const;

const FLOORPLAN_TEXT_KEYS = [
  "type",
  "category",
  "kind",
  "variant",
  "usage",
  "role",
  "label",
  "title",
  "subtitle",
  "description",
  "name",
  "media_type",
  "mediaType",
  "content_type",
  "contentType",
  "assetType",
  "asset_type",
  "group",
  "section",
  "purpose",
  "tag",
  "tags",
  "keyword",
  "keywords",
] as const;

type FeedbackCategory = "idea" | "problem" | "other";

const FEEDBACK_OPTIONS: Array<{ id: FeedbackCategory; shortcut: string; label: string; description: string }> = [
  { id: "idea", shortcut: "A", label: "Idé", description: "Del forslag eller ønsker" },
  { id: "problem", shortcut: "B", label: "Problem", description: "Rapporter feil eller hindringer" },
  { id: "other", shortcut: "C", label: "Annet", description: "Andre tanker eller spørsmål" },
];

const FLOORPLAN_DYNAMIC_REGEX =
  /\b(?:[1-9](?:\s|\.|_)?(?:etg|etasje)|hovedplan|underetasje|kjellerplan|loftsplan|mesaninplan)\b/;

function normaliseFloorplanCandidate(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/\u00f8/g, "o")
    .replace(/\u00e6/g, "ae")
    .replace(/\u00e5/g, "a")
    .replace(/[_-]/g, " ");
}

function matchesFloorplanKeyword(value: string): boolean {
  if (!value) {
    return false;
  }
  const normalised = normaliseFloorplanCandidate(value);
  if (!normalised) {
    return false;
  }
  if (FLOORPLAN_KEYWORDS.some((keyword) => normalised.includes(keyword))) {
    return true;
  }
  return FLOORPLAN_DYNAMIC_REGEX.test(normalised.replace(/[^a-z0-9\s]/g, " "));
}

type ImageDimensions = { width: number; height: number };

function isLikelyFloorplanUrl(value: string, index?: number, total?: number, dimensions?: ImageDimensions): boolean {
  const candidate = normaliseImageUrlForContain(value) ?? value;
  if (!candidate) {
    return false;
  }
  if (matchesFloorplanKeyword(candidate)) {
    return true;
  }

  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return matchesFloorplanKeyword(candidate);
  }

  const segments = url.pathname.split("/").filter(Boolean);
  if (segments.length === 0) {
    return false;
  }

  const normalizedSegments = segments.map((segment) => normaliseFloorplanCandidate(segment));
  if (normalizedSegments.some((segment) => matchesFloorplanKeyword(segment))) {
    return true;
  }
  const filenameSegment = segments[segments.length - 1];
  const filenameLower = filenameSegment.toLowerCase();
  const isJpeg = /\.(?:jpe?g)$/i.test(filenameLower);
  const hasDynamicSegment = normalizedSegments.some((segment) => FLOORPLAN_DYNAMIC_REGEX.test(segment));
  const dimensionRatio =
    dimensions && dimensions.width > 0 && dimensions.height > 0
      ? Math.max(dimensions.width, dimensions.height) / Math.min(dimensions.width, dimensions.height)
      : null;
  if (hasDynamicSegment) {
    if (!isJpeg) {
      return true;
    }
    if (dimensionRatio !== null && dimensionRatio >= 1.34) {
      return true;
    }
  }

  if (/\.(?:svg|pdf)$/i.test(filenameLower)) {
    return true;
  }
  const baseName = filenameLower.replace(/\.(?:jpe?g|png|webp|avif|gif)$/i, "");
  const baseNormalised = normaliseFloorplanCandidate(baseName);
  const baseHasDynamic = FLOORPLAN_DYNAMIC_REGEX.test(baseNormalised);
  if (matchesFloorplanKeyword(baseNormalised)) {
    return true;
  }
  if (baseHasDynamic) {
    if (!isJpeg) {
      return true;
    }
    if (dimensionRatio !== null && dimensionRatio >= 1.34) {
      return true;
    }
  }

  if (!isJpeg && dimensionRatio !== null && dimensionRatio >= 1.52) {
    return true;
  }

  const parentSegment = segments.length > 1 ? segments[segments.length - 2] : null;
  if (parentSegment) {
    const parentNormalised = normaliseFloorplanCandidate(parentSegment);
    const parentHasDynamic = FLOORPLAN_DYNAMIC_REGEX.test(parentNormalised);
    if (matchesFloorplanKeyword(parentNormalised)) {
      return true;
    }
    if (parentHasDynamic) {
      if (!isJpeg) {
        return true;
      }
      if (dimensionRatio !== null && dimensionRatio >= 1.34) {
        return true;
      }
    }
  }

  if (!isJpeg && /\.(?:png|webp)$/i.test(filenameLower)) {
    if (
      typeof index === "number" &&
      typeof total === "number" &&
      total > 0 &&
      index >= total - Math.min(3, Math.max(1, Math.round(total * 0.25)))
    ) {
      return true;
    }
  }

  return false;
}

function valueIndicatesFloorplan(value: unknown): boolean {
  if (typeof value === "string") {
    return matchesFloorplanKeyword(value);
  }
  if (typeof value === "boolean") {
    return value === true;
  }
  if (typeof value === "number") {
    return value === 1;
  }
  if (Array.isArray(value)) {
    return value.some((entry) => valueIndicatesFloorplan(entry));
  }
  return false;
}

function recordIndicatesFloorplan(record: Record<string, unknown>): boolean {
  for (const key of FLOORPLAN_FLAG_KEYS) {
    if (key in record && valueIndicatesFloorplan(record[key])) {
      return true;
    }
  }
  for (const key of FLOORPLAN_TEXT_KEYS) {
    if (key in record && valueIndicatesFloorplan(record[key])) {
      return true;
    }
  }
  return false;
}

function normaliseImageUrlForContain(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const withProtocol = trimmed.startsWith("//") ? `https:${trimmed}` : trimmed;
  try {
    const url = new URL(withProtocol);
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return null;
  }
}

function identifyFloorplanUrls(values: Iterable<string>): Set<string> {
  const allValues = Array.from(values);
  const total = allValues.length;
  const result = new Set<string>();
  allValues.forEach((value, index) => {
    if (typeof value !== "string") {
      return;
    }
    const normalised = normaliseImageUrlForContain(value);
    if (!normalised) {
      return;
    }
    if (isLikelyFloorplanUrl(normalised, index, total)) {
      result.add(normalised);
    }
  });
  return result;
}

function mergeContainHintSet(previous: Set<string>, imageOrder: string[], newHints: Iterable<string>): Set<string> {
  const merged = new Set<string>();
  const nextHints = new Set<string>();
  for (const hint of newHints) {
    if (typeof hint === "string") {
      nextHints.add(hint);
    }
  }
  for (const url of imageOrder) {
    if (nextHints.has(url) || previous.has(url)) {
      merged.add(url);
    }
  }
  return merged;
}

function extractFinnkode(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }
  if (/^\d{6,}$/.test(trimmed)) {
    return trimmed;
  }

  try {
    const url = new URL(normaliseListingUrl(trimmed));
    const param = url.searchParams.get("finnkode") ?? url.searchParams.get("finnCode");
    if (param) {
      const match = param.match(/\d{6,}/);
      if (match) {
        return match[0];
      }
    }
    const pathMatch = url.pathname.match(/(\d{6,})/);
    if (pathMatch) {
      return pathMatch[1];
    }
  } catch {
    /* ignore invalid URL */
  }

  const fallback = trimmed.match(/(\d{6,})/);
  return fallback ? fallback[1] : null;
}

function buildFinnProspectLink(listingUrl: string | null): string | null {
  if (!listingUrl) {
    return null;
  }
  const finnkode = extractFinnkode(listingUrl);
  if (!finnkode) {
    return null;
  }
  try {
    const url = new URL(normaliseListingUrl(listingUrl));
    const host = url.hostname.toLowerCase();
    if (!host.endsWith("finn.no")) {
      return null;
    }
    url.searchParams.set("finnkode", finnkode);
    url.hash = "documents";
    return url.toString();
  } catch {
    return null;
  }
}

function parseNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const normalised = value.replace(/[\s\u00a0\u202f]/g, "").replace(/,/g, ".");
    if (!normalised) {
      return null;
    }
    const parsed = Number(normalised);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatNumberWithSpaces(value: number): string {
  return new Intl.NumberFormat("nb-NO")
    .format(Math.round(value))
    .replace(/\u00a0|\u202f/g, " ");
}

function formatCurrency(value: unknown): string {
  const parsed = parseNumber(value);
  if (parsed === null) {
    return "";
  }
  if (parsed === 0) {
    return "0";
  }
  return formatNumberWithSpaces(parsed);
}

function formatFloat(value: unknown): string {
  const parsed = parseNumber(value);
  if (parsed === null) {
    return "";
  }
  const rounded = Math.round(parsed * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : (rounded.toFixed(2).replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1"));
}

function formatInteger(value: unknown): string {
  const parsed = parseNumber(value);
  if (parsed === null) {
    return "";
  }
  return String(Math.round(parsed));
}

function formatCurrencyLabel(value: unknown): string | null {
  const formatted = formatCurrency(value);
  return formatted ? `kr ${formatted}` : null;
}

function formatSquareMetres(value: unknown): string | null {
  const parsed = parseNumber(value);
  if (parsed === null) {
    return null;
  }
  return `${formatNumberWithSpaces(parsed)} m²`;
}

function formatIntegerLabel(value: unknown): string | null {
  const formatted = formatInteger(value);
  return formatted ? formatted : null;
}

function formatBooleanLabel(value: unknown): string | null {
  if (typeof value === "boolean") {
    return value ? "Ja" : "Nei";
  }
  return null;
}

function formatPlainLabel(value: unknown): string | null {
  if (value == null) {
    return null;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return formatNumberWithSpaces(value);
  }
  return formatBooleanLabel(value);
}

function getNestedValue(source: Record<string, unknown>, path: string): unknown {
  if (!path.includes(".")) {
    return source[path];
  }
  return path.split(".").reduce<unknown>((acc, segment) => {
    if (!acc || typeof acc !== "object") {
      return undefined;
    }
    return (acc as Record<string, unknown>)[segment];
  }, source);
}

function formatEnergyLabel(value: unknown): string | null {
  if (!value) {
    return null;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const label = formatPlainLabel(record.label) ?? formatPlainLabel(record.grade) ?? formatPlainLabel(record.symbol);
    const color = formatPlainLabel(record.color) ?? formatPlainLabel(record.level);
    if (label && color) {
      if (label.toLowerCase().includes(color.toLowerCase())) {
        return label;
      }
      return `${label} ${color}`.trim();
    }
    return label ?? color;
  }
  return null;
}

type ListingKeyFact = {
  key: string;
  label?: string | null;
  value: unknown;
  group?: string | null;
  order?: number | null;
};

type FinnKeyNumbersResponse = {
  finnkode?: string | null;
  url?: string | null;
  available?: boolean;
  key_numbers?: Record<string, unknown> | null;
  key_facts_raw?: KeyFactRaw[] | null;
  keyFactsRaw?: KeyFactRaw[] | null;
};

const CURRENCY_FACT_KEYS = new Set([
  "total_price",
  "asking_price",
  "costs",
  "hoa_month",
  "tax_value",
  "shared_costs_month",
]);

const AREA_FACT_KEYS = new Set([
  "internal_bra_m2",
  "bra_i_m2",
  "primary_room_m2",
  "bra_m2",
  "bra_total",
  "external_bra_m2",
  "bra_e_m2",
  "balcony_terrace_m2",
  "balcony_area",
  "balcony_area_m2",
  "plot_area_m2",
  "plot_area",
  "usable_area",
  "usable_area_internal",
]);

const INTEGER_FACT_KEYS = new Set([
  "rooms",
  "room_count",
  "bedrooms",
  "bedroom_count",
  "floor",
  "built_year",
]);

const KEY_FACT_LABEL_PRIORITY = [
  "Boligtype",
  "Eieform",
  "Soverom",
  "Internt bruksareal",
  "Bruksareal",
  "Eksternt bruksareal",
  "Balkong/Terrasse",
  "Etasje",
  "Byggeår",
  "Energimerking",
  "Rom",
  "Tomteareal",
] as const;

const KEY_FACT_PRIORITY_MAP: Map<string, number> = new Map(
  KEY_FACT_LABEL_PRIORITY.map((label, index) => [label.toLowerCase(), index]),
);

const ENERGY_LABEL_REGEX = /energimerking/i;

type EnergyLabelTone = "good" | "mid" | "warn" | "bad";

function normaliseFactLabel(label: string | null | undefined): string | null {
  if (typeof label !== "string") {
    return null;
  }
  const trimmed = label.trim().toLowerCase();
  return trimmed || null;
}

function classifyEnergyLabelTone(value: string | null | undefined): EnergyLabelTone | null {
  if (!value) {
    return null;
  }

  const rawText = String(value);
  const normalised = rawText
    .trim()
    .toLowerCase()
    .replace(/\u00f8/g, "o")
    .replace(/\u00e6/g, "ae")
    .replace(/\u00e5/g, "a");

  if (normalised.includes("gronn")) {
    return "good";
  }
  if (normalised.includes("gul")) {
    return "mid";
  }
  if (normalised.includes("oransj")) {
    return "warn";
  }
  if (normalised.includes("rod") || normalised.includes("roed") || normalised.includes("svart") || normalised.includes("sort")) {
    return "bad";
  }

  const gradeMatch = rawText.toUpperCase().match(/[A-G]/);
  if (!gradeMatch) {
    return null;
  }
  const grade = gradeMatch[0];
  switch (grade) {
    case "A":
    case "B":
      return "good";
    case "C":
    case "D":
      return "mid";
    case "E":
      return "warn";
    default:
      return "bad";
  }
}

function normaliseKeyFacts(details: ListingDetailsDTO | null): ListingKeyFact[] {
  const rawFacts = (details?.keyFacts ?? details?.key_facts) as unknown;
  if (!Array.isArray(rawFacts)) {
    return [];
  }
  const decorated: Array<{ fact: ListingKeyFact; index: number }> = [];
  rawFacts.forEach((entry, index) => {
    if (!entry || typeof entry !== "object") {
      return;
    }
    const record = entry as Record<string, unknown>;
    const key = stringOrNull(record.key) ?? stringOrNull(record.id) ?? stringOrNull(record.slug);
    if (!key) {
      return;
    }
    const label = stringOrNull(record.label) ?? stringOrNull(record.name) ?? key;
    const orderRaw = record.order;
    const order =
      typeof orderRaw === "number"
        ? orderRaw
        : typeof orderRaw === "string"
          ? parseNumber(orderRaw)
          : null;
    let value: unknown = null;
    if (record.value !== undefined) {
      value = record.value;
    } else if (record.amount !== undefined) {
      value = record.amount;
    } else if (record.displayValue !== undefined) {
      value = record.displayValue;
    }
    const group = stringOrNull(record.group);
    decorated.push({
      fact: {
        key,
        label,
        value: value === undefined ? null : value,
        group,
        order: typeof order === "number" && Number.isFinite(order) ? order : null,
      },
      index,
    });
  });
  decorated.sort((a, b) => {
    const orderA = a.fact.order ?? 1000;
    const orderB = b.fact.order ?? 1000;
    if (orderA !== orderB) {
      return orderA - orderB;
    }
    return a.index - b.index;
  });
  return decorated.map((entry) => entry.fact);
}

function parseKeyFactRawList(rawFacts: unknown[]): KeyFactRaw[] {
  const decorated: Array<{ fact: KeyFactRaw; index: number }> = [];
  rawFacts.forEach((entry, index) => {
    if (!entry || typeof entry !== "object") {
      return;
    }
    const record = entry as Record<string, unknown>;
    const label = typeof record.label === "string" ? record.label : null;
    const value = typeof record.value === "string" ? record.value : null;
    if (!label || !value) {
      return;
    }
    const rawOrder = record.order;
    let order = index;
    if (typeof rawOrder === "number" && Number.isFinite(rawOrder)) {
      order = rawOrder;
    } else if (typeof rawOrder === "string") {
      const numericOrder = Number(rawOrder);
      if (Number.isFinite(numericOrder)) {
        order = numericOrder;
      }
    }
    decorated.push({
      fact: { label, value, order },
      index,
    });
  });
  decorated.sort((a, b) => {
    if (a.fact.order !== b.fact.order) {
      return a.fact.order - b.fact.order;
    }
    return a.index - b.index;
  });
  return decorated.map((entry) => entry.fact);
}

function extractKeyFactsRaw(details: ListingDetailsDTO | null): KeyFactRaw[] {
  if (!details) {
    return [];
  }
  const camel = (details as { keyFactsRaw?: unknown }).keyFactsRaw;
  if (Array.isArray(camel)) {
    const parsed = parseKeyFactRawList(camel);
    if (parsed.length) {
      return parsed;
    }
  }
  const snake = (details as { key_facts_raw?: unknown }).key_facts_raw;
  if (Array.isArray(snake)) {
    const parsed = parseKeyFactRawList(snake);
    if (parsed.length) {
      return parsed;
    }
  }

  const legacyFacts = normaliseKeyFacts(details);
  if (!legacyFacts.length) {
    return [];
  }
  const decorated = legacyFacts.map((fact, index) => {
    const label = typeof fact.label === "string" && fact.label ? fact.label : fact.key;
    const value = formatKeyFactValue(fact.key, fact.value);
    const order =
      typeof fact.order === "number" && Number.isFinite(fact.order)
        ? fact.order
        : index;
    return {
      fact: { label, value, order },
      index,
    };
  });
  decorated.sort((a, b) => {
    if (a.fact.order !== b.fact.order) {
      return a.fact.order - b.fact.order;
    }
    return a.index - b.index;
  });
  return decorated.map((entry) => entry.fact);
}

function formatKeyFactValue(key: string, value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    if (CURRENCY_FACT_KEYS.has(key)) {
      return formatCurrencyLabel(value) ?? "—";
    }
    if (AREA_FACT_KEYS.has(key)) {
      return formatSquareMetres(value) ?? "—";
    }
    if (INTEGER_FACT_KEYS.has(key)) {
      return formatIntegerLabel(value) ?? formatNumberWithSpaces(value);
    }
    return formatNumberWithSpaces(value);
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return "—";
    }
    if (CURRENCY_FACT_KEYS.has(key)) {
      return formatCurrencyLabel(trimmed) ?? trimmed;
    }
    if (AREA_FACT_KEYS.has(key)) {
      return formatSquareMetres(trimmed) ?? trimmed;
    }
    if (INTEGER_FACT_KEYS.has(key)) {
      return formatIntegerLabel(trimmed) ?? trimmed;
    }
    return trimmed;
  }
  return formatPlainLabel(value) ?? "—";
}

const KEY_NUMBER_CURRENCY_KEYS = new Set([
  "prisantydning",
  "totalpris",
  "fellesgjeld",
  "felleskostnader",
  "omkostninger",
  "kommunale avgifter",
  "formuesverdi",
]);

const KEY_NUMBER_AREA_KEYS_NORMALISED = new Set(
  ["primærrom (m²)", "bra (m²)", "tomt (m²)"].map((entry) => entry.toLowerCase()),
);

const KEY_NUMBER_INTEGER_KEYS = new Set(["soverom", "rom", "etasje"]);

function formatKeyNumberLabel(rawKey: string): string {
  const trimmed = rawKey.trim();
  if (!trimmed) {
    return trimmed;
  }
  if (trimmed.toLowerCase() === "finn-kode") {
    return "FINN-kode";
  }
  const first = trimmed.charAt(0);
  if (!first) {
    return trimmed;
  }
  if (first === first.toUpperCase()) {
    return trimmed;
  }
  return `${first.toUpperCase()}${trimmed.slice(1)}`;
}

function formatKeyNumberValue(rawKey: string, value: unknown): string | null {
  if (value == null) {
    return null;
  }
  const key = rawKey.trim().toLowerCase();
  if (Array.isArray(value)) {
    const formatted = value.map((entry) => formatPlainLabel(entry)).filter(Boolean) as string[];
    return formatted.length ? formatted.join(", ") : null;
  }
  if (KEY_NUMBER_CURRENCY_KEYS.has(key)) {
    return formatCurrencyLabel(value) ?? formatPlainLabel(value);
  }
  if (KEY_NUMBER_AREA_KEYS_NORMALISED.has(key)) {
    return formatSquareMetres(value) ?? formatPlainLabel(value);
  }
  if (KEY_NUMBER_INTEGER_KEYS.has(key)) {
    return formatIntegerLabel(value) ?? formatPlainLabel(value);
  }
  if (key === "energimerke") {
    return formatEnergyLabel(value) ?? formatPlainLabel(value);
  }
  return formatPlainLabel(value);
}

function normaliseKeyFactsFromResponse(raw: unknown): KeyFactRaw[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const facts: KeyFactRaw[] = [];
  raw.forEach((entry, index) => {
    if (!entry || typeof entry !== "object") {
      return;
    }
    const record = entry as Record<string, unknown>;
    const label = typeof record.label === "string" ? record.label : null;
    const value = typeof record.value === "string" ? record.value : null;
    if (!label || !value) {
      return;
    }
    const rawOrder = record.order;
    let order = typeof rawOrder === "number" && Number.isFinite(rawOrder) ? rawOrder : index;
    if (typeof rawOrder === "string") {
      const numeric = Number.parseInt(rawOrder, 10);
      if (Number.isFinite(numeric)) {
        order = numeric;
      }
    }
    facts.push({ label, value, order });
  });
  facts.sort((a, b) => {
    if (a.order !== b.order) {
      return a.order - b.order;
    }
    return 0;
  });
  return facts;
}

function keyNumbersToFacts(raw: unknown): KeyFactRaw[] {
  if (!raw || typeof raw !== "object") {
    return [];
  }
  const entries = Object.entries(raw as Record<string, unknown>);
  const facts: KeyFactRaw[] = [];
  entries.forEach(([key, value], index) => {
    const label = formatKeyNumberLabel(key);
    const formattedValue = formatKeyNumberValue(key, value);
    if (!label || !formattedValue) {
      return;
    }
    facts.push({ label, value: formattedValue, order: index });
  });
  return facts;
}

function roundToStep(value: number, step: number): number {
  if (!Number.isFinite(value)) {
    return value;
  }
  const safeStep = Number.isFinite(step) && step > 0 ? step : 1;
  return Math.round(value / safeStep) * safeStep;
}

function formatApproxCurrency(
  value: number | null,
  step: number,
  suffix: string,
  options?: { includeSign?: boolean },
): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  const rounded = roundToStep(value, step);
  if (!Number.isFinite(rounded)) {
    return null;
  }
  const includeSign = options?.includeSign ?? false;
  const sign = includeSign && rounded !== 0 ? (rounded > 0 ? "+" : "−") : "";
  const absValue = Math.abs(rounded);
  const formatted = formatNumberWithSpaces(absValue);
  const suffixText = suffix ? suffix.trim() : "";
  const prefix = includeSign && rounded === 0 ? "" : sign;
  const valuePart = prefix ? `${prefix}${formatted}` : formatted;
  const components = ["~", valuePart];
  if (suffixText) {
    components.push(suffixText);
  }
  return components.join(" ").replace(/\s+/g, " ").trim();
}

function formatApproxPercent(
  value: number | null,
  decimals = 1,
  options?: { includeSign?: boolean },
): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  const factor = 10 ** Math.max(0, decimals);
  const rounded = Math.round(value * factor) / factor;
  if (!Number.isFinite(rounded)) {
    return null;
  }
  const includeSign = options?.includeSign ?? false;
  const sign = includeSign && rounded !== 0 ? (rounded > 0 ? "+" : "−") : "";
  const absValue = Math.abs(rounded);
  const formatted = absValue.toLocaleString("nb-NO", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const prefix = includeSign && rounded === 0 ? "" : sign;
  const valuePart = prefix ? `${prefix}${formatted}` : formatted;
  return `~ ${valuePart} %`.trim();
}

function stripApproxPrefix(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  return value.replace(/^~\s*/, "");
}

function buildFormFromParams(params: Record<string, unknown> | null | undefined): AnalysisPayload {
  return {
    price: params ? formatCurrency(params.price) || DEFAULT_FORM.price : DEFAULT_FORM.price,
    equity: params ? formatCurrency(params.equity) || DEFAULT_FORM.equity : DEFAULT_FORM.equity,
    interest: params ? formatFloat(params.interest) || DEFAULT_FORM.interest : DEFAULT_FORM.interest,
    term_years: params ? formatInteger(params.term_years) || DEFAULT_FORM.term_years : DEFAULT_FORM.term_years,
    rent: params ? formatCurrency(params.rent) || DEFAULT_FORM.rent : DEFAULT_FORM.rent,
    hoa: params ? formatCurrency(params.hoa) || DEFAULT_FORM.hoa : DEFAULT_FORM.hoa,
    maint_pct: params ? formatFloat(params.maint_pct) || DEFAULT_FORM.maint_pct : DEFAULT_FORM.maint_pct,
    vacancy_pct: params ? formatFloat(params.vacancy_pct) || DEFAULT_FORM.vacancy_pct : DEFAULT_FORM.vacancy_pct,
    other_costs: params ? formatCurrency(params.other_costs) || DEFAULT_FORM.other_costs : DEFAULT_FORM.other_costs,
  };
}

function stringOrNull(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  return null;
}

function extractAnalysisParams(job: JobStatus | null): Record<string, unknown> | null {
  if (!job) {
    return null;
  }
  const resultParams = job.result?.analysis?.input_params;
  if (resultParams && typeof resultParams === "object") {
    return resultParams as Record<string, unknown>;
  }
  const artifacts = job.artifacts && typeof job.artifacts === "object" ? (job.artifacts as Record<string, unknown>) : null;
  const artifactParams = artifacts?.analysis_params;
  if (artifactParams && typeof artifactParams === "object") {
    return artifactParams as Record<string, unknown>;
  }
  return null;
}

function extractListingInfo(job: JobStatus | null): ListingDetailsDTO | null {
  if (!job) {
    return null;
  }
  const resultListing = job.result?.listing;
  if (resultListing && typeof resultListing === "object") {
    return resultListing as ListingDetailsDTO;
  }
  const artifacts = job.artifacts && typeof job.artifacts === "object" ? (job.artifacts as Record<string, unknown>) : null;
  const artifactListing = artifacts?.listing;
  if (artifactListing && typeof artifactListing === "object") {
    return artifactListing as ListingDetailsDTO;
  }
  return null;
}

interface ListingImageCollection {
  urls: string[];
  containUrls: string[];
}

function collectListingImages(listing: ListingDetailsDTO): ListingImageCollection {
  const discovered: string[] = [];
  const containCandidates: string[] = [];
  const visited = new WeakSet<object>();

  const pushCandidate = (value: string | null, hint: boolean): void => {
    if (!value) {
      return;
    }
    discovered.push(value);
    if (hint) {
      containCandidates.push(value);
    }
  };

  const explore = (value: unknown, hint: boolean): void => {
    if (value == null) {
      return;
    }
    if (typeof value === "string") {
      const candidate = stringOrNull(value);
      if (!candidate) {
        return;
      }
      const inlineHint = hint || matchesFloorplanKeyword(candidate);
      pushCandidate(candidate, inlineHint);
      return;
    }
    if (Array.isArray(value)) {
      for (const entry of value) {
        explore(entry, hint);
      }
      return;
    }
    if (typeof value === "object") {
      const record = value as Record<string, unknown>;
      if (visited.has(record)) {
        return;
      }
      visited.add(record);
      const recordHint = hint || recordIndicatesFloorplan(record);
      const candidateKeys = [
        "url",
        "href",
        "src",
        "large",
        "image",
        "imageUrl",
        "image_url",
        "full",
        "fullsize",
        "original",
        "value",
        "default",
        "floorplan",
        "floorplans",
      ];
      for (const key of candidateKeys) {
        if (key in record) {
          explore(record[key], recordHint);
        }
      }
      const nestedKeys = ["sizes", "variants", "items", "images", "media", "resources", "assets", "entries", "alternatives", "floorplans", "plans"] as const;
      for (const key of nestedKeys) {
        if (key in record) {
          explore(record[key], recordHint);
        }
      }
    }
  };

  const directKeys = [
    "image",
    "image_url",
    "imageUrl",
    "cover_image",
    "coverImage",
    "main_image",
    "mainImage",
    "primary_image",
    "primaryImage",
    "hero_image",
    "heroImage",
    "thumbnail",
    "thumbnailUrl",
    "thumbnail_url",
  ] as const;

  for (const key of directKeys) {
    if (key in listing) {
      explore(listing[key], false);
    }
  }

  const collectionPaths = [
    "images",
    "gallery",
    "gallery.images",
    "gallery.items",
    "photos",
    "photos.items",
    "photos.list",
    "media",
    "media.images",
    "media.gallery",
    "media.items",
    "media.resources",
    "media.photos",
    "photo_gallery",
    "photoGallery",
    "floorplans",
    "floorPlans",
    "floor_plans",
    "plans",
    "plans.items",
    "plans.images",
    "carousel",
    "carousel.images",
    "carousel.items",
  ];

  for (const path of collectionPaths) {
    const value = getNestedValue(listing, path);
    if (value !== undefined) {
      explore(value, false);
    }
  }

  const urls = dedupeImageList(discovered);
  const containSet = identifyFloorplanUrls(urls);
  const dedupedHints = dedupeImageList(containCandidates);
  for (const hint of dedupedHints) {
    containSet.add(hint);
  }

  return {
    urls,
    containUrls: Array.from(containSet),
  };
}

function dedupeImageList(values: Iterable<string>): string[] {
  const map = new Map<string, { url: string; score: number; order: number }>();
  let order = 0;

  const toCandidate = (value: string): { key: string; url: string; score: number } | null => {
    const trimmed = value.trim();
    if (!trimmed) {
      return null;
    }
    const withProtocol = trimmed.startsWith("//") ? `https://${trimmed}` : trimmed;
    let url: URL;
    try {
      url = new URL(withProtocol);
    } catch {
      return null;
    }
    url.search = "";
    url.hash = "";

    const rawSegments = url.pathname.split("/").filter(Boolean);
    if (rawSegments.length === 0) {
      return null;
    }

    const cleanedSegments: string[] = [];
    let maxWidth = 0;

    for (let i = 0; i < rawSegments.length; i += 1) {
      const segment = decodeURIComponent(rawSegments[i]);
      const lower = segment.toLowerCase();

      if (lower === "dynamic") {
        if (i + 1 < rawSegments.length) {
          const widthCandidate = decodeURIComponent(rawSegments[i + 1]);
          const widthMatch = widthCandidate.match(/(\d{2,4})/);
          if (widthMatch) {
            const width = Number.parseInt(widthMatch[1], 10);
            if (Number.isFinite(width) && width > maxWidth) {
              maxWidth = width;
            }
          }
          if (/^\d+[xw]?$/i.test(widthCandidate) || /^w\d+$/i.test(widthCandidate)) {
            i += 1;
            continue;
          }
        }
        continue;
      }

      if (/^(?:\d{1,4}(?:x|w)?|w\d{1,4}|h\d{1,4}|c\d{1,4}|s\d{1,4}|q\d{1,4}|m\d{1,4}|xs|sm|md|lg)$/i.test(lower)) {
        continue;
      }

      if (IMAGE_IDENTITY_SEGMENT_IGNORE.has(lower)) {
        continue;
      }

      cleanedSegments.push(segment);
    }

    if (cleanedSegments.length === 0) {
      return null;
    }

    const filenameSegment = cleanedSegments[cleanedSegments.length - 1];
    const siblingSegment = cleanedSegments.length > 1 ? cleanedSegments[cleanedSegments.length - 2] : null;

    const filenameLower = filenameSegment.toLowerCase();
    const dotIndex = filenameLower.lastIndexOf(".");
    const baseName = dotIndex > 0 ? filenameLower.slice(0, dotIndex) : filenameLower;
    const trimmedBaseName = baseName.replace(/[-_](?:\d{1,2}|[a-f0-9]{1,4})$/i, "");
    const identityName = trimmedBaseName || baseName || filenameLower;

    const keySegments = [url.hostname.toLowerCase()];
    if (siblingSegment) {
      keySegments.push(siblingSegment.toLowerCase());
    }
    keySegments.push(identityName);

    const key = keySegments.join("/");
    return { key, url: url.toString(), score: maxWidth };
  };

  for (const value of values) {
    if (typeof value !== "string") {
      continue;
    }
    const candidate = toCandidate(value);
    if (!candidate) {
      continue;
    }
    const existing = map.get(candidate.key);
    if (!existing) {
      map.set(candidate.key, { url: candidate.url, score: candidate.score, order: order++ });
      continue;
    }
    if (candidate.score > existing.score) {
      existing.url = candidate.url;
      existing.score = candidate.score;
    }
  }

  return Array.from(map.values())
    .sort((a, b) => a.order - b.order)
    .map((entry) => entry.url);
}

function pickListingTitle(listing: ListingDetailsDTO): string | null {
  const keys = ["heading", "title", "name", "summary", "ad_heading"] as const;
  for (const key of keys) {
    const candidate = stringOrNull(listing[key]);
    if (candidate) {
      return candidate;
    }
  }
  return null;
}

function pickListingAddress(listing: ListingDetailsDTO): string | null {
  const directKeys = ["address", "full_address", "location", "address_text", "short_address", "display_address"] as const;
  for (const key of directKeys) {
    const candidate = stringOrNull(listing[key]);
    if (candidate) {
      return candidate;
    }
  }
  const addr = listing.address;
  if (addr && typeof addr === "object") {
    const record = addr as Record<string, unknown>;
    const parts = [
      stringOrNull(record.streetAddress ?? record.street ?? record.street_name),
      stringOrNull(record.postalCode ?? record.postal_code ?? record.zip),
      stringOrNull(record.addressLocality ?? record.city ?? record.municipality),
    ].filter(Boolean) as string[];
    if (parts.length) {
      return parts.join(", ");
    }
  }
  return null;
}

function jobStatusLabel(status: string | undefined): string {
  switch ((status ?? "").toLowerCase()) {
    case "queued":
      return "I kø";
    case "running":
      return "Henter data";
    case "done":
      return "Ferdig";
    case "failed":
      return "Feilet";
    default:
      return status ?? "Ukjent";
  }
}

function jobStatusHeadline(status: JobStatus | null, stateKey: string | undefined): string {
  const key = (stateKey ?? "").toLowerCase();
  const message = stringOrNull(status?.message);
  switch (key) {
    case "queued":
      return "Forbereder analyse";
    case "running":
      return message ?? "Automatisk innhenting pågår";
    case "done":
      return "Analyse fullført";
    case "failed":
      return "Analysen feilet";
    default:
      return jobStatusLabel(key);
  }
}

function normaliseListingUrl(value: string): string {
  if (!value) {
    return "";
  }
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

function normaliseExternalUrl(value: string | null | undefined): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  if (/^\/\//.test(trimmed)) {
    return `https:${trimmed}`;
  }
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)) {
    return trimmed;
  }
  const looksLikeDomain = /^[\w.-]+\.[a-z]{2,}/i.test(trimmed);
  if (looksLikeDomain && (!/\.pdf$/i.test(trimmed) || trimmed.includes("/"))) {
    return `https://${trimmed}`;
  }
  return trimmed;
}

function colorClass(farge?: string): string {
  switch ((farge ?? "").toLowerCase()) {
    case "red":
      return "score-chip red";
    case "orange":
      return "score-chip yellow";
    case "yellow":
      return "score-chip yellow";
    case "green":
      return "score-chip green";
    default:
      return "score-chip neutral";
  }
}

function keyColorClass(farge?: string): string {
  switch ((farge ?? "").toLowerCase()) {
    case "red":
      return "key-value red";
    case "orange":
      return "key-value yellow";
    case "yellow":
      return "key-value yellow";
    case "green":
      return "key-value green";
    default:
      return "key-value neutral";
  }
}

function scoreFillColor(percent: number | null): string {
  if (percent === null) {
    return "rgba(148, 163, 184, 0.35)";
  }
  if (percent < 32) {
    return STRONG_RED_HEX;
  }
  if (percent < 50) {
    return "#fee440"; // gul
  }
  if (percent < 66) {
    return "#a3e635"; // gulgrønn (lime)
  }
  if (percent < 84) {
    return "#22c55e"; // grønn
  }
  return "#14532d"; // mørkegrønn
}

const KEY_FIGURE_TOOLTIPS: Record<string, string> = {
  "Månedlig overskudd": "Hvor mye kontantstrøm som står igjen hver måned etter renter, avdrag og driftskostnader.",
  "Leie for å gå i null": "Minimum husleie som dekker alle kostnader (break-even-nivået).",
  "Årlig nettoinntekt": "Netto driftsinntekt (NOI) per år etter alle driftskostnader.",
  "Årlig nedbetaling på lån": "Beløpet av lånet som betales ned i løpet av ett år.",
  "Månedlig lånekostnader": "Summen du betaler på lånet hver måned, inkludert renter og avdrag.",
  "Avkastning på egenkapital": "Forventet årlig avkastning på investert egenkapital uttrykt i prosent.",
};

function AnalysisPageContent() {
  const router = useRouter();
  const params = useSearchParams();
  const listing = params.get("listing") ?? "";
  const runToken = params.get("run") ?? "";

  const listingUrl = normaliseListingUrl(listing);

  const [form, setForm] = useState<AnalysisPayload>({ ...DEFAULT_FORM });
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [prospectus, setProspectus] = useState<ProspectusExtract | null>(null);
  const [manualProspectusFile, setManualProspectusFile] = useState<File | null>(null);
  const [manualProspectusLoading, setManualProspectusLoading] = useState(false);
  const [manualProspectusError, setManualProspectusError] = useState<string | null>(null);
  const [manualProspectusSuccess, setManualProspectusSuccess] = useState(false);
  const [manualProspectusText, setManualProspectusText] = useState("");
  const [manualProspectusTextLoading, setManualProspectusTextLoading] = useState(false);
  const [manualProspectusTextError, setManualProspectusTextError] = useState<string | null>(null);
  const [manualProspectusTextSuccess, setManualProspectusTextSuccess] = useState(false);
  const [manualProspectusShouldRefresh, setManualProspectusShouldRefresh] = useState(false);
  const [previewImages, setPreviewImages] = useState<string[]>([]);
  const [previewContainHints, setPreviewContainHints] = useState<Set<string>>(() => new Set());
  const [previewImageIndex, setPreviewImageIndex] = useState(0);
  const [previewTitle, setPreviewTitle] = useState<string | null>(null);
  const [previewAddress, setPreviewAddress] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [jobStarting, setJobStarting] = useState(false);
  const [autoManualJobId, setAutoManualJobId] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackCategory, setFeedbackCategory] = useState<FeedbackCategory>("idea");
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [feedbackEmail, setFeedbackEmail] = useState("");
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [feedbackSuccess, setFeedbackSuccess] = useState(false);
  const [feedbackErrorMessage, setFeedbackErrorMessage] = useState<string | null>(null);
  const [listingKeyFacts, setListingKeyFacts] = useState<KeyFactRaw[]>([]);
  const [listingKeyFactsSource, setListingKeyFactsSource] = useState<"job" | "finn" | null>(null);
  const [listingKeyFactsLoading, setListingKeyFactsLoading] = useState(false);
  const [listingKeyFactsError, setListingKeyFactsError] = useState<string | null>(null);
  const jobListingRef = useRef<string | null>(null);
  const jobAppliedRef = useRef<string | null>(null);
  const skipJobInitRef = useRef(process.env.NODE_ENV !== "production");

  const listingDetails = useMemo(() => extractListingInfo(jobStatus), [jobStatus]);
  const derivedListingKeyFacts = useMemo(() => extractKeyFactsRaw(listingDetails), [listingDetails]);
  useEffect(() => {
    if (!listingDetails) {
      setDetailsOpen(false);
    }
  }, [listingDetails]);

  useEffect(() => {
    setListingKeyFacts([]);
    setListingKeyFactsSource(null);
    setListingKeyFactsLoading(false);
    setListingKeyFactsError(null);
  }, [listingUrl]);

  useEffect(() => {
    if (listingKeyFactsSource === "finn") {
      return;
    }
    if (derivedListingKeyFacts.length) {
      setListingKeyFacts(derivedListingKeyFacts);
      setListingKeyFactsSource("job");
      setListingKeyFactsError(null);
    } else if (listingKeyFactsSource !== null) {
      setListingKeyFacts([]);
      setListingKeyFactsSource(null);
    } else {
      setListingKeyFacts([]);
    }
  }, [derivedListingKeyFacts, listingKeyFactsSource]);

  const fetchListingKeyFacts = useCallback(async () => {
    if (!listingUrl) {
      return;
    }
    setListingKeyFactsLoading(true);
    setListingKeyFactsError(null);
    try {
      const response = await fetch("/api/finn-key-numbers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: listingUrl }),
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = (await response.json()) as FinnKeyNumbersResponse;
      const rawFacts = normaliseKeyFactsFromResponse(data.keyFactsRaw ?? data.key_facts_raw);
      const fallbackFacts = rawFacts.length ? rawFacts : keyNumbersToFacts(data.key_numbers ?? null);
      setListingKeyFacts(fallbackFacts);
      setListingKeyFactsSource("finn");
      setListingKeyFactsError(null);
    } catch (error) {
      setListingKeyFactsError("Kunne ikke hente nøkkeltall fra FINN.");
    } finally {
      setListingKeyFactsLoading(false);
    }
  }, [listingUrl]);

  useEffect(() => {
    if (runToken) {
      return;
    }
    const token = Date.now().toString(36);
    const paramsCopy = new URLSearchParams(Array.from(params.entries()));
    paramsCopy.set("run", token);
    router.replace(`?${paramsCopy.toString()}`);
  }, [runToken, params, router]);

  const resetFeedbackForm = () => {
    setFeedbackCategory("idea");
    setFeedbackMessage("");
    setFeedbackEmail("");
    setFeedbackErrorMessage(null);
    setFeedbackSuccess(false);
    setFeedbackSubmitting(false);
  };

  const handleFeedbackOpen = () => {
    resetFeedbackForm();
    setFeedbackOpen(true);
  };

  const handleFeedbackClose = () => {
    setFeedbackOpen(false);
  };

  const handleShowDetails = useCallback(() => {
    setDetailsOpen(true);
    if (!listingUrl) {
      setListingKeyFacts([]);
      setListingKeyFactsSource(null);
      setListingKeyFactsError("Legg inn FINN-annonsen for å hente nøkkeltall.");
      return;
    }
    if (listingKeyFactsLoading) {
      return;
    }
    if (listingKeyFactsSource !== "finn" || listingKeyFacts.length === 0 || listingKeyFactsError) {
      void fetchListingKeyFacts();
    }
  }, [
    listingUrl,
    listingKeyFactsLoading,
    listingKeyFactsSource,
    listingKeyFacts.length,
    listingKeyFactsError,
    fetchListingKeyFacts,
  ]);

  const decisionUi: DecisionUi | null = result?.decision_ui ?? null;

  const scoreValue = useMemo(() => {
    if (!decisionUi) return null;
    const scoreGauge = decisionUi.scorelinjal;
    const statusScore = decisionUi.status?.score;
    const gaugeValue = typeof scoreGauge?.value === "number" ? scoreGauge.value : undefined;
    return (gaugeValue ?? statusScore) ?? null;
  }, [decisionUi]);

  const scorePercent = useMemo(() => {
    const gauge = decisionUi?.scorelinjal;
    if (gauge && typeof gauge.value === "number" && Number.isFinite(gauge.value)) {
      return Math.max(0, Math.min(100, gauge.value));
    }
    if (typeof scoreValue === "number" && Number.isFinite(scoreValue)) {
      return Math.max(0, Math.min(100, scoreValue));
    }
    return null;
  }, [decisionUi, scoreValue]);

  const scoreColor = colorClass(decisionUi?.scorelinjal?.farge);
  const domLabel = decisionUi?.status?.dom ?? "";
  const statusSentence = decisionUi?.status?.setning ?? "";
  const tg2Items = useMemo(() => prospectus?.tg2 ?? [], [prospectus]);
  const tg3Items = useMemo(() => prospectus?.tg3 ?? [], [prospectus]);
  const tg2Details = useMemo(() => normaliseProspectusDetails(prospectus?.tg2_details), [prospectus]);
  const tg3Details = useMemo(() => normaliseProspectusDetails(prospectus?.tg3_details), [prospectus]);
  const watchoutItems = useMemo(() => prospectus?.watchouts ?? [], [prospectus]);
  const prospectusLinks = useMemo(() => prospectus?.links ?? null, [prospectus]);
  const jobLinkInfo = useMemo(() => {
    if (!jobStatus) {
      return null;
    }
    const direct = normaliseProspectusLinks(jobStatus.result?.links);
    if (direct) {
      return direct;
    }
    const artifacts =
      jobStatus.artifacts && typeof jobStatus.artifacts === "object"
        ? (jobStatus.artifacts as Record<string, unknown>)
        : null;
    if (artifacts && "links" in artifacts) {
      return normaliseProspectusLinks((artifacts as { links?: unknown }).links);
    }
    return null;
  }, [jobStatus]);
  const effectiveLinks = prospectusLinks ?? jobLinkInfo;
  const tg2FallbackItems = useMemo(
    () => sanitizeProspectusItems(tg2Items, { preserveLabels: true }),
    [tg2Items],
  );
  const tg3FallbackItems = useMemo(
    () => sanitizeProspectusItems(tg3Items, { preserveLabels: true }),
    [tg3Items],
  );
  const watchoutDisplayItems = useMemo(
    () => sanitizeProspectusItems(watchoutItems),
    [watchoutItems],
  );
  const tg2DisplayItems = useMemo(
    () => buildProspectusDisplayItems(tg2Details, tg2FallbackItems, watchoutDisplayItems, 5),
    [tg2Details, tg2FallbackItems, watchoutDisplayItems],
  );
  const tg3DisplayItems = useMemo(
    () => buildProspectusDisplayItems(tg3Details, tg3FallbackItems, [], 5),
    [tg3Details, tg3FallbackItems],
  );
  const tgDataAvailable =
    tg2Items.length > 0 || tg3Items.length > 0 || tg2Details.length > 0 || tg3Details.length > 0;
  const hasProspectusSignals = tg2DisplayItems.length > 0 || tg3DisplayItems.length > 0;
  const manualProspectusHasSuccess = manualProspectusSuccess || manualProspectusTextSuccess;
  const manualProspectusShowResults =
    manualProspectusHasSuccess || tg2DisplayItems.length > 0 || tg3DisplayItems.length > 0;
  const missingProspectus = useMemo(() => {
    if (effectiveLinks?.salgsoppgave_pdf) {
      return false;
    }
    const statusKey = (jobStatus?.status ?? "").trim().toLowerCase();
    const parts = [
      jobStatus?.message,
      jobStatus?.error,
      jobStatus?.result?.links?.message,
      jobError,
    ]
      .map((value) => (typeof value === "string" ? value.toLowerCase() : ""))
      .filter(Boolean);
    const combined = parts.join(" ");
    if (combined.includes("uten salgsoppgave") || combined.includes("fant ikke salgsoppgave") || combined.includes("fant ikke prospekt")) {
      return true;
    }
    if (statusKey === "failed" && (combined.includes("salgsoppgave") || combined.includes("prospekt") || combined.includes("pdf"))) {
      return true;
    }
    if (result && !prospectus && !effectiveLinks?.salgsoppgave_pdf) {
      return true;
    }
    return false;
  }, [effectiveLinks, jobStatus, jobError, prospectus, result]);
  const scoreBreakdownEntries = useMemo(() => {
    const entries = Array.isArray(decisionUi?.score_breakdown)
      ? decisionUi.score_breakdown
      : [];
    const defaults = [
      { id: "econ", label: "Økonomi" },
      { id: "tr", label: "Tilstand" },
    ];
    return defaults.map((template) => {
      const match = entries.find((entry) => entry && entry.id === template.id);
      const rawValue = typeof match?.value === "number" ? match.value : null;
      const value = rawValue === null ? null : Math.max(0, Math.min(100, Math.round(rawValue)));
      return {
        id: template.id,
        label: match?.label ?? template.label,
        value,
      };
    });
  }, [decisionUi]);
  const scoreFillStyle = useMemo(() => {
    const percentValue = scorePercent ?? 0;
    return {
      width: `${percentValue}%`,
      "--score-fill-color": scoreFillColor(scorePercent),
    } as CSSProperties;
  }, [scorePercent]);

  const calculatedMetrics = useMemo(() => {
    const source = result?.calculated_metrics;
    if (!source || typeof source !== "object") {
      return null;
    }
    const record = source as Record<string, unknown>;
    const pick = (key: string) => parseNumber(record[key]);
    const cashflow = pick("cashflow_mnd");
    const breakEven = pick("break_even_leie_mnd");
    const noiYear = pick("noi_aar");
    const roe = pick("roe_pct");
    const loanCost = pick("lanekost_mnd");
    const principalYear = pick("aarlig_nedbetaling_lan");
    if (
      cashflow === null &&
      breakEven === null &&
      noiYear === null &&
      roe === null &&
      loanCost === null &&
      principalYear === null
    ) {
      return null;
    }
    return {
      cashflow,
      breakEven,
      noiYear,
      roe,
      loanCost,
      principalYear,
    };
  }, [result]);

  const analysisMetrics = useMemo(() => {
    const source = result?.metrics;
    if (!source || typeof source !== "object") {
      return null;
    }
    const record = source as Record<string, unknown>;
    const pick = (key: string) => parseNumber(record[key]);
    const cashflow = pick("cashflow");
    if (cashflow === null) {
      return null;
    }
    return {
      cashflow,
    };
  }, [result]);

  const positiveHighlights = useMemo(() => {
    const points: string[] = [];
    const calc = calculatedMetrics;
    const cashflow =
      typeof calc?.cashflow === "number"
        ? calc.cashflow
        : typeof analysisMetrics?.cashflow === "number"
          ? analysisMetrics.cashflow
          : null;
    const roePct = typeof calc?.roe === "number" ? calc.roe : null;

    if (cashflow !== null && cashflow > 0) {
      const valueText = stripApproxPrefix(formatApproxCurrency(cashflow, 100, "kr/mnd", { includeSign: true }));
      if (valueText) {
        points.push(`Månedlig overskudd ca. ${valueText} gir løpende inntekt.`);
      }
    }

    if (roePct !== null && roePct > 0) {
      const roeText = stripApproxPrefix(formatApproxPercent(roePct, 1, { includeSign: true }));
      if (roeText) {
        points.push(`Avkastning på egenkapital ${roeText} (positiv) og indikerer lønnsom kapitalbruk.`);
      }
    }

    return points.slice(0, 2);
  }, [calculatedMetrics, analysisMetrics]);

  const negativeHighlights = useMemo(() => {
    const points: string[] = [];
    const calc = calculatedMetrics;
    const cashflow =
      typeof calc?.cashflow === "number"
        ? calc.cashflow
        : typeof analysisMetrics?.cashflow === "number"
          ? analysisMetrics.cashflow
          : null;
    const roePct = typeof calc?.roe === "number" ? calc.roe : null;

    if (cashflow !== null && cashflow < 0) {
      const valueText = stripApproxPrefix(formatApproxCurrency(cashflow, 100, "kr/mnd", { includeSign: true }));
      if (valueText) {
        points.push(`Månedlig underskudd ca. ${valueText} gir usikker inntekt og kan gi tap.`);
      }
    }

    if (roePct !== null && roePct < 0) {
      const roeText = stripApproxPrefix(formatApproxPercent(roePct, 1, { includeSign: true }));
      if (roeText) {
        points.push(`Negativ avkastning på egenkapital: ${roeText} betyr at kapitalen taper verdi.`);
      }
    }

    return points.slice(0, 2);
  }, [calculatedMetrics, analysisMetrics]);

  const statusKey = (jobStatus?.status ?? "").trim().toLowerCase();
  const jobFailed = statusKey === "failed" || Boolean(jobError);
  const jobInProgress = jobStarting || (jobStatus ? !["done", "failed"].includes(statusKey) : false);
  const submitDisabled = analyzing || (jobInProgress && !jobFailed);
  const fieldsDisabled = analyzing || (jobInProgress && !jobFailed);
  const submitLabel = analyzing ? "Oppdaterer..." : "Oppdater";
  const autoManualActive = Boolean(jobFailed && statusKey === "failed" && jobId && autoManualJobId === jobId);
  const resourcePdfUrl = useMemo(() => {
    const candidate = normaliseExternalUrl(effectiveLinks?.salgsoppgave_pdf);
    if (candidate) {
      return candidate;
    }
    const fallback = normaliseExternalUrl(jobStatus?.pdf_url);
    return fallback ?? null;
  }, [effectiveLinks, jobStatus]);
  const resourceListingUrl = useMemo(() => {
    const candidate = stringOrNull(listingUrl);
    if (!candidate) {
      return null;
    }
    try {
      return new URL(candidate).toString();
    } catch {
      return null;
    }
  }, [listingUrl]);

  const handleChange = (field: keyof AnalysisPayload) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setForm((prev) => ({ ...prev, [field]: event.target.value }));
    };

  const jobCompleted = useMemo(() => Boolean(result), [result]);

  const previewImageTotal = previewImages.length;
  const safePreviewIndex = previewImageTotal > 0 ? Math.max(0, Math.min(previewImageIndex, previewImageTotal - 1)) : 0;
  const handlePreviewPrevious = () => {
    if (previewImageTotal < 2) {
      return;
    }
    setPreviewImageIndex((prev) => {
      const next = prev - 1;
      return next < 0 ? previewImageTotal - 1 : next;
    });
  };
  const handlePreviewNext = () => {
    if (previewImageTotal < 2) {
      return;
    }
    setPreviewImageIndex((prev) => {
      const next = prev + 1;
      return next >= previewImageTotal ? 0 : next;
    });
  };

  const runManualAnalysis = useCallback(async () => {
    setAnalyzing(true);
    setError(null);

    const payload: AnalysisPayload = {
      ...form,
      tg2_items: tg2Items,
      tg3_items: tg3Items,
      tg_data_available: tgDataAvailable,
      upgrades: prospectus?.upgrades ?? [],
      warnings: watchoutItems,
    };

    try {
      const analysis = await runAnalysis(payload);
      setResult(analysis);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Klarte ikke å hente analyse.");
    } finally {
      setAnalyzing(false);
    }
  }, [form, tg2Items, tg3Items, tgDataAvailable, prospectus, watchoutItems]);

  const handleManualProspectusAnalyze = useCallback(async () => {
    if (!manualProspectusFile) {
      setManualProspectusError("Velg en PDF-fil først.");
      setManualProspectusSuccess(false);
      return;
    }
    setManualProspectusLoading(true);
    setManualProspectusError(null);
    setManualProspectusSuccess(false);
    try {
      const response = await analyzeProspectusPdf(manualProspectusFile);
      setProspectus((prev) => ({
        ...response,
        links: prev?.links,
      }));
      setManualProspectusSuccess(true);
      setManualProspectusShouldRefresh(true);
    } catch (err) {
      setManualProspectusError(
        err instanceof Error ? err.message : "Kunne ikke analysere PDF-filen.",
      );
    } finally {
      setManualProspectusLoading(false);
    }
  }, [manualProspectusFile]);

  const handleManualProspectusFileChange = useCallback(
    (file: File | null) => {
      if (file) {
        const type = (file.type || "").toLowerCase();
        const name = file.name.toLowerCase();
        if (!(type.includes("pdf") || name.endswith(".pdf"))) {
          setManualProspectusFile(null);
          setManualProspectusSuccess(false);
          setManualProspectusError("Filen må være en PDF.");
          return;
        }
      }
      setManualProspectusFile(file);
      if (manualProspectusError) {
        setManualProspectusError(null);
      }
      if (manualProspectusSuccess) {
        setManualProspectusSuccess(false);
      }
    },
    [manualProspectusError, manualProspectusSuccess],
  );

  const handleManualProspectusClear = useCallback(() => {
    setManualProspectusFile(null);
    setManualProspectusError(null);
    setManualProspectusSuccess(false);
  }, []);

  const handleManualProspectusTextChange = useCallback(
    (next: string) => {
      setManualProspectusText(next);
      if (manualProspectusTextError) {
        setManualProspectusTextError(null);
      }
      if (manualProspectusTextSuccess) {
        setManualProspectusTextSuccess(false);
      }
    },
    [manualProspectusTextError, manualProspectusTextSuccess],
  );

  const handleManualProspectusTextAnalyze = useCallback(async () => {
    const trimmed = manualProspectusText.trim();
    if (!trimmed) {
      setManualProspectusTextError("Lim inn salgsoppgaven først.");
      setManualProspectusTextSuccess(false);
      return;
    }
    setManualProspectusTextLoading(true);
    setManualProspectusTextError(null);
    setManualProspectusTextSuccess(false);
    try {
      const response = await analyzeProspectusText({ text: trimmed });
      setProspectus((prev) => ({
        ...response,
        links: prev?.links,
      }));
      setManualProspectusTextSuccess(true);
      setManualProspectusShouldRefresh(true);
    } catch (err) {
      setManualProspectusTextError(
        err instanceof Error ? err.message : "Kunne ikke analysere teksten.",
      );
    } finally {
      setManualProspectusTextLoading(false);
    }
  }, [manualProspectusText]);

  const handleManualProspectusTextClear = useCallback(() => {
    setManualProspectusText("");
    setManualProspectusTextError(null);
    setManualProspectusTextSuccess(false);
  }, []);

  useEffect(() => {
    if (!manualProspectusShouldRefresh) {
      return;
    }
    setManualProspectusShouldRefresh(false);
    void runManualAnalysis();
  }, [manualProspectusShouldRefresh, runManualAnalysis]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await runManualAnalysis();
  };

  const handleFeedbackSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = feedbackMessage.trim();
    const emailValue = feedbackEmail.trim();

    if (!message) {
      setFeedbackErrorMessage("Skriv gjerne litt før du sender.");
      return;
    }

    setFeedbackSubmitting(true);
    setFeedbackErrorMessage(null);

    setFeedbackSuccess(true);
    setFeedbackMessage("");
    setFeedbackEmail("");

    void fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: feedbackCategory,
        message,
        email: emailValue ? emailValue : undefined,
      }),
    })
      .then(async (response) => {
        if (response.ok) {
          return;
        }
        let detail: string | null = null;
        try {
          const data = await response.json();
          if (data && typeof data === "object") {
            if (typeof (data as { detail?: unknown }).detail === "string") {
              detail = (data as { detail: string }).detail;
            } else if (typeof (data as { error?: unknown }).error === "string") {
              detail = (data as { error: string }).error;
            }
          }
        } catch {
          detail = null;
        }
        throw new Error(detail ?? "Kunne ikke sende tilbakemelding.");
      })
      .catch((sendError) => {
        if (
          !emailValue &&
          sendError instanceof Error &&
          sendError.message.toLowerCase().includes("smtp_host mangler for feedback-epost")
        ) {
          // Ignore missing SMTP config when no reply email is provided to avoid confusing the user.
          return;
        }
        setFeedbackSuccess(false);
        setFeedbackErrorMessage(
          sendError instanceof Error ? sendError.message : "Kunne ikke sende tilbakemelding. Prøv igjen senere.",
        );
      })
      .finally(() => {
        setFeedbackSubmitting(false);
      });
  };

  useEffect(() => {
    if (skipJobInitRef.current) {
      skipJobInitRef.current = false;
      return;
    }
    const trimmed = listing.trim();
    if (!runToken) {
      return;
    }
    // eslint-disable-next-line no-console
    if (!trimmed) {
      jobListingRef.current = null;
      jobAppliedRef.current = null;
      setJobId(null);
      setJobStatus(null);
      setJobError(null);
      setAutoManualJobId(null);
      return;
    }

    const normalised = normaliseListingUrl(trimmed);
    const key = normalised ? `${normalised}::${runToken}` : runToken ? `::${runToken}` : normalised;
    if (jobListingRef.current === key) {
      return;
    }

    const finnkode = extractFinnkode(trimmed);
    // eslint-disable-next-line no-console
    jobListingRef.current = key;
    jobAppliedRef.current = null;

    setForm({ ...DEFAULT_FORM });
    setResult(null);
    setError(null);
    setJobStatus(null);
    setJobId(null);
    setJobError(null);
    setAutoManualJobId(null);
    setProspectus(null);
    setManualProspectusFile(null);
    setManualProspectusError(null);
    setManualProspectusSuccess(false);
    setManualProspectusText("");
    setManualProspectusTextError(null);
    setManualProspectusTextSuccess(false);
    setManualProspectusTextLoading(false);
    setManualProspectusShouldRefresh(false);
    setManualProspectusLoading(false);
    setPreviewImages([]);
    setPreviewContainHints(new Set());
    setPreviewImageIndex(0);
    setPreviewTitle(null);
    setPreviewAddress(null);
    setPreviewError(null);

    if (!finnkode) {
      setJobError("Fant ikke FINN-kode i lenken.");
      return;
    }

    let cancelled = false;
    setJobStarting(true);

    (async () => {
      // eslint-disable-next-line no-console
      try {
        const job = await startAnalysisJob(finnkode);
        if (cancelled) {
          return;
        }
        // eslint-disable-next-line no-console
        setJobId(job.job_id);
        setJobStatus({ id: job.job_id, status: job.status ?? "queued", finnkode });
      } catch (err) {
        if (!cancelled) {
          setJobError(err instanceof Error ? err.message : "Kunne ikke starte analysen.");
        }
      } finally {
        if (!cancelled) {
          setJobStarting(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (jobListingRef.current === key) {
        jobListingRef.current = null;
      }
    };
  }, [listing, runToken]);

  useEffect(() => {
    if (!listing) {
      setPreviewImages([]);
      setPreviewContainHints(new Set());
      setPreviewImageIndex(0);
      setPreviewTitle(null);
      setPreviewError(null);
      setPreviewAddress(null);
      return;
    }

    // eslint-disable-next-line no-console

    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);

    fetch(`/api/listing-preview?url=${encodeURIComponent(listing)}`)
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        return res.json() as Promise<{
          image?: string | null;
          images?: Array<string | null | undefined> | null;
          title?: string | null;
          address?: string | null;
        }>;
      })
      .then((data) => {
        if (cancelled) {
          return;
        }
        const imageValue = typeof data?.image === "string" && data.image ? data.image : null;
        const imageList = Array.isArray(data?.images)
          ? data.images
              .map((value) => (typeof value === "string" ? value.trim() : ""))
              .filter((value) => Boolean(value))
          : [];
        const uniqueImages = imageList.length > 0 ? dedupeImageList(imageList) : [];
        const titleValue = typeof data?.title === "string" && data.title ? data.title : null;
        const addressValue = typeof data?.address === "string" && data.address ? data.address : null;
        const mergedImages = uniqueImages.length > 0 ? uniqueImages : imageValue ? dedupeImageList([imageValue]) : [];
        setPreviewImages(mergedImages);
        setPreviewContainHints(identifyFloorplanUrls(mergedImages));
        setPreviewImageIndex(0);
        setPreviewTitle(titleValue);
        setPreviewAddress(addressValue);
        if (mergedImages.length === 0) {
          setPreviewError("Fant ikke bilder for denne annonsen.");
        } else {
          setPreviewError(null);
        }
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setPreviewImages([]);
        setPreviewContainHints(new Set());
        setPreviewImageIndex(0);
        setPreviewTitle(null);
        setPreviewAddress(null);
        setPreviewError("Kunne ikke hente bilde fra annonsen.");
      })
      .finally(() => {
        if (!cancelled) {
          setPreviewLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [listing]);

  useEffect(() => {
    if (!jobId) {
      return;
    }

    // eslint-disable-next-line no-console

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        // eslint-disable-next-line no-console
        const next = await getJobStatus(jobId);
        if (cancelled) {
          return;
        }
        // eslint-disable-next-line no-console
        setJobStatus(next);
        if (next.status === "failed") {
          setJobError(next.message ?? next.error ?? "Analysen feilet.");
          return;
        }
        setJobError(null);
        if (next.status === "done") {
          return;
        }
        timer = setTimeout(poll, JOB_POLL_INTERVAL);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("job status poll failed", jobId, err);
        if (cancelled) {
          return;
        }
        setJobError(err instanceof Error ? err.message : "Klarte ikke å hente jobbstatus.");
        timer = setTimeout(poll, JOB_POLL_INTERVAL * 2);
      }
    };

    poll();

    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [jobId]);

  useEffect(() => {
    if (!jobStatus || !jobId) {
      return;
    }

    const statusKey = (jobStatus.status ?? "").toLowerCase();
    const listingInfo = listingDetails;

    if (statusKey === "done" && jobAppliedRef.current !== jobId) {
      const paramsFromJob = extractAnalysisParams(jobStatus);
      if (paramsFromJob) {
        setForm(buildFormFromParams(paramsFromJob));
      }
      if (jobStatus.result?.analysis) {
        setResult(jobStatus.result.analysis);
      }
      const prospectusExtract = extractProspectusFromJob(jobStatus);
      setProspectus(prospectusExtract);
      if (listingInfo) {
        const { urls: images, containUrls } = collectListingImages(listingInfo);
        if (images.length > 0) {
          let combined: string[] = [];
          setPreviewImages((prev) => {
            const candidate = dedupeImageList([...prev, ...images]);
            const unchanged = candidate.length === prev.length && candidate.every((value, index) => value === prev[index]);
            if (unchanged) {
              combined = prev;
              return prev;
            }
            setPreviewImageIndex((currentIndex) => {
              if (candidate.length === 0) {
                return 0;
              }
              const safePrevIndex = prev.length > 0 ? Math.max(0, Math.min(currentIndex, prev.length - 1)) : 0;
              const currentImage = prev[safePrevIndex];
              if (currentImage) {
                const preservedIndex = candidate.indexOf(currentImage);
                if (preservedIndex !== -1) {
                  return preservedIndex;
                }
              }
              return Math.max(0, Math.min(currentIndex, candidate.length - 1));
            });
            combined = candidate;
            return candidate;
          });
          setPreviewContainHints((prev) => {
            if (!combined || combined.length === 0) {
              return new Set<string>();
            }
            return mergeContainHintSet(prev, combined, containUrls);
          });
          setPreviewError(null);
        }
        const addressValue = pickListingAddress(listingInfo);
        if (addressValue) {
          setPreviewAddress(addressValue);
        }
        const titleValue = pickListingTitle(listingInfo);
        if (titleValue) {
          setPreviewTitle(titleValue);
        }
      }
      setPreviewLoading(false);
      jobAppliedRef.current = jobId;
    }

    if (statusKey === "failed" && jobAppliedRef.current !== `${jobId}:failed`) {
      const paramsFromJob = extractAnalysisParams(jobStatus);
      if (paramsFromJob) {
        setForm(buildFormFromParams(paramsFromJob));
      }
      if (jobStatus.result?.analysis) {
        setResult(jobStatus.result.analysis);
      }
      const prospectusExtract = extractProspectusFromJob(jobStatus);
      setProspectus(prospectusExtract);
      if (listingInfo) {
        const addressValue = pickListingAddress(listingInfo);
        if (addressValue) {
          setPreviewAddress(addressValue);
        }
        const titleValue = pickListingTitle(listingInfo);
        if (titleValue) {
          setPreviewTitle(titleValue);
        }
        const { urls: images, containUrls } = collectListingImages(listingInfo);
        if (images.length > 0) {
          let combined: string[] = [];
          setPreviewImages((prev) => {
            const candidate = dedupeImageList([...prev, ...images]);
            const unchanged = candidate.length === prev.length && candidate.every((value, index) => value === prev[index]);
            if (unchanged) {
              combined = prev;
              return prev;
            }
            setPreviewImageIndex((currentIndex) => {
              if (candidate.length === 0) {
                return 0;
              }
              const safePrevIndex = prev.length > 0 ? Math.max(0, Math.min(currentIndex, prev.length - 1)) : 0;
              const currentImage = prev[safePrevIndex];
              if (currentImage) {
                const preservedIndex = candidate.indexOf(currentImage);
                if (preservedIndex !== -1) {
                  return preservedIndex;
                }
              }
              return Math.max(0, Math.min(currentIndex, candidate.length - 1));
            });
            combined = candidate;
            return candidate;
          });
          setPreviewContainHints((prev) => {
            if (!combined || combined.length === 0) {
              return new Set<string>();
            }
            return mergeContainHintSet(prev, combined, containUrls);
          });
          setPreviewError(null);
        }
      }
      setPreviewLoading(false);
      jobAppliedRef.current = `${jobId}:failed`;
    }
  }, [jobStatus, jobId, listingDetails]);

  useEffect(() => {
    if (!jobId || !jobStatus) {
      return;
    }
    const statusValue = (jobStatus.status ?? "").toLowerCase();
    if (statusValue !== "failed") {
      return;
    }
    if (jobStatus.result?.analysis) {
      return;
    }
    if (autoManualJobId === jobId) {
      return;
    }
    if (analyzing) {
      return;
    }
    const failureText = [jobError, jobStatus.message, jobStatus.error]
      .map((value) => (typeof value === "string" ? value.toLowerCase() : ""))
      .filter(Boolean)
      .join(" ");
    const missingProspect =
      failureText.includes("salgsoppgave") || failureText.includes("prospekt") || failureText.includes("pdf");
    if (!missingProspect) {
      return;
    }
    const hasPriceValue = typeof form.price === "string" && form.price.trim() !== "";
    if (!hasPriceValue) {
      return;
    }
    setAutoManualJobId(jobId);
    void runManualAnalysis();
  }, [jobId, jobStatus, autoManualJobId, analyzing, runManualAnalysis, jobError, form.price]);

  return (
    <>
      <ListingDetailsModal
        open={detailsOpen}
        details={listingDetails}
        keyFacts={listingKeyFacts}
        loading={listingKeyFactsLoading}
        error={listingKeyFactsError}
        onClose={() => setDetailsOpen(false)}
        title={previewTitle}
        address={previewAddress}
      />
      <FeedbackDialog
        open={feedbackOpen}
        category={feedbackCategory}
        onSelectCategory={setFeedbackCategory}
        message={feedbackMessage}
        onMessageChange={setFeedbackMessage}
        email={feedbackEmail}
        onEmailChange={setFeedbackEmail}
        submitting={feedbackSubmitting}
        success={feedbackSuccess}
        error={feedbackErrorMessage}
        onClose={handleFeedbackClose}
        onSubmit={handleFeedbackSubmit}
      />
      <section className="analysis-inputs">
        <ListingPreviewCard
          listingUrl={listingUrl}
          imageUrls={previewImages}
          containHints={previewContainHints}
          currentIndex={safePreviewIndex}
          onNavigatePrevious={handlePreviewPrevious}
          onNavigateNext={handlePreviewNext}
          listingTitle={previewTitle}
          listingAddress={previewAddress}
          loading={previewLoading}
          error={previewError}
          statusCard={
            analyzing ? (
              <AnalysisUpdateCard />
            ) : (
              <JobStatusCard
                status={jobStatus}
                jobError={jobError}
                starting={jobStarting}
                completed={jobCompleted}
              />
            )
          }
        />

        <section className="analysis-form-card">
          <div className="form-card-header">
            <ResourceLinkGroup
              pdfUrl={resourcePdfUrl}
              listingUrl={resourceListingUrl}
              linkInfo={effectiveLinks}
              onShowDetails={handleShowDetails}
            />
          </div>
          {jobFailed && !jobCompleted ? (
            <p className="analysis-manual-hint">
              {autoManualActive
                ? "Automatisk innhenting feilet. Analysen er kjørt manuelt med tallene under."
                : 'Automatisk innhenting feilet. Fyll inn tallene manuelt og trykk &quot;Oppdater&quot;.'}
            </p>
          ) : null}
          <form className="analysis-form" onSubmit={handleSubmit}>
            <div className="form-grid">
              <FormField
                label="Kjøpesum"
                tooltip={FORM_FIELD_TOOLTIPS["Kjøpesum"]}
                value={form.price}
                placeholder="4 500 000"
                onChange={handleChange("price")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Egenkapital"
                tooltip={FORM_FIELD_TOOLTIPS["Egenkapital"]}
                value={form.equity}
                placeholder="675 000"
                onChange={handleChange("equity")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Rente % p.a."
                tooltip={FORM_FIELD_TOOLTIPS["Rente % p.a."]}
                value={form.interest}
                placeholder="5.10"
                onChange={handleChange("interest")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Lånetid (år)"
                tooltip={FORM_FIELD_TOOLTIPS["Lånetid (år)"]}
                value={form.term_years}
                placeholder="30"
                onChange={handleChange("term_years")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Leie (mnd)"
                tooltip={FORM_FIELD_TOOLTIPS["Leie (mnd)"]}
                value={form.rent}
                placeholder="18 000"
                onChange={handleChange("rent")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Felleskost (mnd)"
                tooltip={FORM_FIELD_TOOLTIPS["Felleskost (mnd)"]}
                value={form.hoa}
                placeholder="3 000"
                onChange={handleChange("hoa")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Vedlikehold % av leie"
                tooltip={FORM_FIELD_TOOLTIPS["Vedlikehold % av leie"]}
                value={form.maint_pct}
                placeholder="6.0"
                onChange={handleChange("maint_pct")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Andre kost (mnd)"
                tooltip={FORM_FIELD_TOOLTIPS["Andre kost (mnd)"]}
                value={form.other_costs}
                placeholder="800"
                onChange={handleChange("other_costs")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Ledighet %"
                tooltip={FORM_FIELD_TOOLTIPS["Ledighet %"]}
                value={form.vacancy_pct}
                placeholder="0.0"
                onChange={handleChange("vacancy_pct")}
                disabled={fieldsDisabled}
              />
            </div>

            <div className="analysis-actions">
              <button type="submit" className="analysis-button" disabled={submitDisabled}>
                {submitLabel}
              </button>
              <button type="button" className="analysis-feedback-link" onClick={handleFeedbackOpen}>
                <span className="analysis-feedback-icon" aria-hidden="true">
                  <svg width="16" height="16" viewBox="0 0 20 20" fill="none" focusable="false">
                    <path
                      d="M4.5 3h11A2.5 2.5 0 0 1 18 5.5v6A2.5 2.5 0 0 1 15.5 14H9.3l-3.264 2.75A0.6 0.6 0 0 1 5 16.3V14H4.5A2.5 2.5 0 0 1 2 11.5v-6A2.5 2.5 0 0 1 4.5 3Z"
                      fill="currentColor"
                    />
                  </svg>
                </span>
                <span>Gi oss tilbakemelding</span>
              </button>
            </div>
          </form>
          {error ? <div className="error-banner">{error}</div> : null}
        </section>
      </section>

      {result ? (
        <section className="analysis-results">
          <div className="analysis-results-grid">
            <div className="analysis-score-block">
              <h2 className="analysis-column-title">Resultat – forsterket av OpenAI</h2>
              <div className="score-card">
                <div className="score-card-header">
                  <div>
                    <p className="overline">Total score</p>
                    <div className="score-value">{scoreValue ?? "-"}</div>
                  </div>
                  <span className={scoreColor}>{domLabel || "N/A"}</span>
                </div>
                <div
                  className={`score-progress${scorePercent === 100 ? " complete" : ""}`}
                  role="progressbar"
                  aria-label="Total score"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={scorePercent ?? undefined}
                >
                  <span className="score-progress-fill" style={scoreFillStyle} />
                </div>
                <div className="score-breakdown">
                  {scoreBreakdownEntries.map((entry) => {
                    const percent = entry.value ?? 0;
                    const valueText = entry.value === null ? "–" : `${percent}%`;
                    const fillStyle = {
                      width: `${percent}%`,
                      "--score-fill-color": scoreFillColor(entry.value ?? null),
                    } as CSSProperties;
                    return (
                      <div className="score-breakdown-item" key={entry.id}>
                        <div className="score-breakdown-header">
                          <span className="score-breakdown-label">{entry.label}</span>
                          <span className="score-breakdown-value">{valueText}</span>
                        </div>
                        <div
                          className="score-breakdown-bar"
                          role="progressbar"
                          aria-label={entry.label}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={entry.value ?? undefined}
                        >
                          <span className="score-breakdown-fill" style={fillStyle} />
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="score-card-footer">
                  {statusSentence ? <p className="status-sentence">{statusSentence}</p> : null}
                </div>
              </div>
            </div>

            <div className="analysis-score-spacer" aria-hidden="true" />

            <div className="analysis-column analysis-column-economy">
              <div className="key-grid">
                {(decisionUi?.nokkel_tall ?? []).map((item, index) => {
                  const navn = typeof item.navn === "string" ? item.navn : "";
                  const verdi = typeof item.verdi === "string" ? item.verdi : String(item.verdi ?? "");
                  const farge = typeof item.farge === "string" ? item.farge : undefined;
                  const description = navn ? KEY_FIGURE_TOOLTIPS[navn] : undefined;
                  const tooltipId = description ? `key-tooltip-${index}` : undefined;
                  return (
                    <div className="key-card" key={`${navn}-${index}`}>
                      <div className="key-name">
                        <span className="key-name-text">{navn}</span>
                        {description ? (
                          <div className="key-tooltip">
                            <span className="key-info" aria-hidden="true">
                              ?
                            </span>
                            <div className="key-tooltip-bubble" role="tooltip" id={tooltipId}>
                              {description}
                            </div>
                          </div>
                        ) : null}
                      </div>
                      <p className={keyColorClass(farge)}>{verdi}</p>
                    </div>
                  );
                })}
              </div>

              <div className="list-grid">
                <DecisionList
                  title="✅ Positiv"
                  items={positiveHighlights}
                  empty="Ingen positive punkter ennå."
                  showPlaceholder={false}
                />
                <DecisionList
                  title="❌ Negativ"
                  items={negativeHighlights}
                  empty="Ingen negative punkter ennå."
                  showPlaceholder={false}
                />
              </div>
            </div>

            <div className="analysis-column analysis-column-prospectus">
              <div className="prospectus-grid">
                {missingProspectus ? (
                  <>
                    <ManualProspectusCard
                      file={manualProspectusFile}
                      onFileChange={handleManualProspectusFileChange}
                      onAnalyze={handleManualProspectusAnalyze}
                      onClear={handleManualProspectusClear}
                      loading={manualProspectusLoading}
                      error={manualProspectusError}
                      success={manualProspectusSuccess}
                    />
                    <ManualProspectusTextCard
                      text={manualProspectusText}
                      onTextChange={handleManualProspectusTextChange}
                      onAnalyze={handleManualProspectusTextAnalyze}
                      onClear={handleManualProspectusTextClear}
                      loading={manualProspectusTextLoading}
                      error={manualProspectusTextError}
                      success={manualProspectusTextSuccess}
                    />
                    {manualProspectusShowResults ? (
                      <>
                        <ProspectusCard
                          title="⚠️ TG2"
                          badge={{ label: "Middels risiko", tone: "warn" }}
                          tooltip={PROSPECTUS_CARD_TOOLTIPS["⚠️ TG2"]}
                          items={tg2DisplayItems}
                          empty="Ingen TG2- eller observasjonspunkter funnet ennå."
                          className="manual-prospectus-result-card"
                        />
                        <ProspectusCard
                          title="🛑 TG3"
                          badge={{ label: "Høy risiko", tone: "danger" }}
                          tooltip={PROSPECTUS_CARD_TOOLTIPS["🛑 TG3"]}
                          items={tg3DisplayItems}
                          empty="Ingen TG3-punkter funnet ennå."
                          className="manual-prospectus-result-card"
                        />
                      </>
                    ) : null}
                  </>
                ) : (
                  <>
                    <ProspectusCard
                      title="⚠️ TG2"
                      badge={{ label: "Middels risiko", tone: "warn" }}
                      tooltip={PROSPECTUS_CARD_TOOLTIPS["⚠️ TG2"]}
                      items={tg2DisplayItems}
                      empty="Ingen TG2- eller observasjonspunkter funnet ennå."
                    />
                    <ProspectusCard
                      title="🛑 TG3"
                      badge={{ label: "Høy risiko", tone: "danger" }}
                      tooltip={PROSPECTUS_CARD_TOOLTIPS["🛑 TG3"]}
                      items={tg3DisplayItems}
                      empty="Ingen TG3-punkter funnet ennå."
                    />
                  </>
                )}
              </div>
            </div>
          </div>
        </section>
      ) : null}
    </>
  );
}

export default function AnalysisPage() {
  return (
    <main className="page-gradient">
      <PageContainer>
        <SiteHeader showAction actionHref="/" />
        <Suspense
          fallback={
            <section className="analysis-hero">
              <p className="lede">Laster analyse …</p>
            </section>
          }
        >
          <AnalysisPageContent />
        </Suspense>
        <SiteFooter />
      </PageContainer>
    </main>
  );
}

interface ListingPreviewCardProps {
  listingUrl: string | null;
  listingTitle: string | null;
  listingAddress: string | null;
  imageUrls: string[];
  containHints?: Set<string>;
  currentIndex: number;
  onNavigatePrevious: () => void;
  onNavigateNext: () => void;
  loading: boolean;
  error: string | null;
  statusCard?: ReactNode;
}

interface JobStatusCardProps {
  status: JobStatus | null;
  jobError: string | null;
  starting: boolean;
  completed: boolean;
}

function AnalysisUpdateCard() {
  return (
    <div className="job-card" role="status">
      <p className="job-label">Oppdaterer analyse</p>
      <p className="job-value">Beregner på nytt</p>
      <div className="job-progress">
        <span className="job-progress-fill indeterminate" />
      </div>
      <p className="job-message">Oppdaterer økonomitall basert på parametrene.</p>
    </div>
  );
}

function JobStatusCard({ status, jobError, starting, completed }: JobStatusCardProps) {
  if (!status && !starting && !jobError && !completed) {
    return null;
  }

  const derivedState = completed ? "done" : undefined;
  const stateKey = status?.status ?? derivedState ?? (jobError ? "failed" : starting ? "queued" : undefined);
  const failureDetails = stateKey === "failed" ? [status?.message, status?.error, jobError] : [];
  const failureSignalText = failureDetails
    .map((value) => (typeof value === "string" ? value.toLowerCase() : ""))
    .filter(Boolean)
    .join(" ");
  const missingProspect =
    stateKey === "failed" &&
    (failureSignalText.includes("salgsoppgave") ||
      failureSignalText.includes("prospekt") ||
      failureSignalText.includes("pdf"));
  const label = missingProspect ? "Analyse fullført" : jobStatusHeadline(status, stateKey);
  const progressValueRaw = typeof status?.progress === "number" ? Math.max(0, Math.min(100, status.progress)) : null;
  const isActive = stateKey !== "failed" && stateKey !== "done";
  const progressValue = isActive ? null : (stateKey === "done" ? 100 : progressValueRaw);
  const showIndeterminate = isActive;
  const showProgress = stateKey !== "failed";
  const failureMessage = stateKey === "failed"
    ? missingProspect
      ? "Fant ikke salgsoppgaven"
      : stringOrNull(status?.message) ?? stringOrNull(status?.error) ?? jobError
    : null;

  return (
    <div className="job-card">
      <p className="job-label">Automatisk innhenting</p>
      <p className="job-value">{label}</p>
      {showProgress ? (
        <div
          className={`job-progress${stateKey === "done" ? " complete" : ""}`}
          role="progressbar"
          aria-label="Fremdrift for automatisk innhenting"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={showIndeterminate ? undefined : progressValue ?? undefined}
        >
          <span
            className={`job-progress-fill${showIndeterminate ? " indeterminate" : ""}`}
            style={progressValue !== null ? { width: `${progressValue}%` } : undefined}
          />
        </div>
      ) : null}
      {failureMessage ? <p className="job-message">{failureMessage}</p> : null}
    </div>
  );
}

interface ResourceLinkGroupProps {
  pdfUrl: string | null;
  listingUrl: string | null;
  linkInfo?: ProspectusLinks | null;
  onShowDetails: () => void;
}

function ResourceLinkGroup({ pdfUrl, listingUrl, linkInfo, onShowDetails }: ResourceLinkGroupProps) {
  const [discoveredPdfUrl, setDiscoveredPdfUrl] = useState<string | null>(null);
  const [discoveringPdf, setDiscoveringPdf] = useState(false);
  const primaryPdfUrl = normaliseExternalUrl(linkInfo?.salgsoppgave_pdf) ?? normaliseExternalUrl(pdfUrl);
  const linkProtected = useMemo(() => {
    const message = linkInfo?.message?.toLowerCase() ?? "";
    if (message.includes("beskyttet")) {
      return true;
    }
    if (typeof linkInfo?.confidence === "number" && linkInfo.confidence <= 0) {
      return true;
    }
    return false;
  }, [linkInfo]);

  useEffect(() => {
    if (primaryPdfUrl) {
      setDiscoveredPdfUrl(null);
      setDiscoveringPdf(false);
      return;
    }
    if (!listingUrl || linkProtected) {
      setDiscoveredPdfUrl(null);
      setDiscoveringPdf(false);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setDiscoveringPdf(true);
    const params = new URLSearchParams({ listing: listingUrl });
    fetch(`/api/finn-prospect?${params.toString()}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          return null;
        }
        try {
          const data = (await res.json()) as { url?: string | null };
          return typeof data.url === "string" && data.url ? data.url : null;
        } catch {
          return null;
        }
      })
      .then((nextUrl) => {
        if (cancelled) {
          return;
        }
        setDiscoveredPdfUrl(normaliseExternalUrl(nextUrl));
      })
      .catch(() => {
        if (!cancelled) {
          setDiscoveredPdfUrl(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setDiscoveringPdf(false);
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [listingUrl, primaryPdfUrl, linkProtected]);

  const fallbackProspectLink = buildFinnProspectLink(listingUrl);
  const effectiveDiscoveredUrl = linkProtected ? null : discoveredPdfUrl;
  const salgsoppgaveHref = primaryPdfUrl ?? effectiveDiscoveredUrl ?? fallbackProspectLink;
  const salgsoppgaveState = primaryPdfUrl
    ? "primary"
    : effectiveDiscoveredUrl
      ? "discovered"
      : fallbackProspectLink
        ? "fallback"
        : null;
  const salgsoppgaveLabel =
    salgsoppgaveState === "discovered"
      ? "Åpne salgsoppgaven (direkte PDF fra FINN)"
      : salgsoppgaveState === "fallback"
        ? "Åpne salgsoppgaven i FINN-annonsen"
        : undefined;
  const disabledTitle = linkInfo?.message ?? "Ikke funnet";
  const anchorTitle =
    linkInfo?.message ??
    (typeof linkInfo?.confidence === "number" ? `Konfidens ${Math.round(linkInfo.confidence * 100)}%` : undefined);
  return (
    <div className="resource-links" aria-label="Ressurser">
      {salgsoppgaveHref ? (
        <a
          className={`resource-chip${salgsoppgaveState ? ` ${salgsoppgaveState}` : ""}`}
          href={salgsoppgaveHref}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={salgsoppgaveLabel}
          title={anchorTitle}
          aria-busy={salgsoppgaveState !== "primary" && discoveringPdf ? "true" : undefined}
        >
          Salgsoppgave (PDF)
        </a>
      ) : (
        <span className="resource-chip disabled" aria-disabled="true" title={disabledTitle}>
          Salgsoppgave (PDF)
        </span>
      )}
      {listingUrl ? (
        <a className="resource-chip" href={listingUrl} target="_blank" rel="noopener noreferrer">
          Annonse
        </a>
      ) : (
        <span className="resource-chip disabled" aria-disabled="true">
          Annonse
        </span>
      )}
      <button type="button" className="resource-chip" onClick={onShowDetails} aria-haspopup="dialog">
        Nøkkeltall
      </button>
    </div>
  );
}

interface ListingDetailsModalProps {
  open: boolean;
  details: ListingDetailsDTO | null;
  keyFacts?: KeyFactRaw[];
  loading?: boolean;
  error?: string | null;
  onClose: () => void;
  title: string | null;
  address: string | null;
}

function ListingDetailsModal({
  open,
  details,
  keyFacts,
  loading = false,
  error = null,
  onClose,
  title,
  address,
}: ListingDetailsModalProps) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKeydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    const previousOverflow = document.body.style.overflow;
    document.addEventListener("keydown", handleKeydown);
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeydown);
    };
  }, [open, onClose]);

  const facts = useMemo<KeyFactRaw[]>(() => {
    const source = Array.isArray(keyFacts) ? keyFacts : extractKeyFactsRaw(details);
    if (!source || !source.length) {
      return [];
    }
    const decorated = source.map((fact, index) => ({
      fact,
      index,
    }));
    decorated.sort((a, b) => {
      const orderA = Number.isFinite(a.fact.order) ? a.fact.order : a.index;
      const orderB = Number.isFinite(b.fact.order) ? b.fact.order : b.index;
      if (orderA !== orderB) {
        return orderA - orderB;
      }
      return a.index - b.index;
    });
    return decorated.map((entry) => entry.fact);
  }, [details, keyFacts]);

  const displayFacts = useMemo<KeyFactRaw[]>(() => {
    if (!facts.length) {
      return facts;
    }
    const decorated: Array<{ fact: KeyFactRaw; index: number; priority: number }> = [];
    facts.forEach((fact, index) => {
      const labelKey = normaliseFactLabel(fact.label);
      if (labelKey === null || !KEY_FACT_PRIORITY_MAP.has(labelKey)) {
        return;
      }
      const priority = KEY_FACT_PRIORITY_MAP.get(labelKey) ?? index;
      decorated.push({ fact, index, priority });
    });
    decorated.sort((a, b) => {
      if (a.priority !== b.priority) {
        return a.priority - b.priority;
      }
      return a.index - b.index;
    });
    return decorated.map((entry) => entry.fact);
  }, [facts]);

  const overlayPointerDownRef = useRef(false);

  const handleOverlayPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    overlayPointerDownRef.current = event.target === event.currentTarget;
  }, []);

  const handleOverlayClick = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (event.target === event.currentTarget && overlayPointerDownRef.current) {
        onClose();
      }
      overlayPointerDownRef.current = false;
    },
    [onClose],
  );

  if (!open) {
    return null;
  }

  const heading = "Nøkkeltall";
  const subtitle = address || title;

  return (
    <div
      className="listing-details-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="listing-details-title"
      onPointerDown={handleOverlayPointerDown}
      onClick={handleOverlayClick}
    >
      <div className="listing-details-modal" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="listing-details-close" aria-label="Lukk detaljer" onClick={onClose}>
          ×
        </button>
        <h2 className="listing-details-title" id="listing-details-title">
          {heading}
        </h2>
        {subtitle ? <p className="listing-details-subtitle">{subtitle}</p> : null}
        {displayFacts.length ? (
          <>
            <div className="listing-details-grid">
              {displayFacts.map((fact, index) => {
                const key = `${fact.order ?? index}-${index}-${fact.label}`;
                const labelText = fact.label;
                const valueText = fact.value;
                const isEnergyLabel = ENERGY_LABEL_REGEX.test(labelText);
                const energyTone = isEnergyLabel ? classifyEnergyLabelTone(valueText) : null;
                const valueClasses = ["listing-details-item-value"];
                if (isEnergyLabel) {
                  valueClasses.push("listing-details-energy");
                  if (energyTone) {
                    valueClasses.push(`listing-details-energy-${energyTone}`);
                  }
                }
                return (
                  <div key={key} className="listing-details-item">
                    <span className="listing-details-item-label">{labelText}</span>
                    <span className={valueClasses.join(" ")}>{valueText}</span>
                  </div>
                );
              })}
            </div>
            {loading ? <p className="listing-details-empty">Oppdaterer nøkkeltall ...</p> : null}
            {!loading && error ? <p className="listing-details-empty">{error}</p> : null}
          </>
        ) : loading ? (
          <p className="listing-details-empty">Laster nøkkeltall ...</p>
        ) : error ? (
          <p className="listing-details-empty">{error}</p>
        ) : (
          <p className="listing-details-empty">Nøkkeltall er ikke tilgjengelig for denne annonsen ennå.</p>
        )}
      </div>
    </div>
  );
}

interface FeedbackDialogProps {
  open: boolean;
  category: FeedbackCategory;
  onSelectCategory: (category: FeedbackCategory) => void;
  message: string;
  onMessageChange: (value: string) => void;
  email: string;
  onEmailChange: (value: string) => void;
  submitting: boolean;
  success: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

function FeedbackDialog({
  open,
  category,
  onSelectCategory,
  message,
  onMessageChange,
  email,
  onEmailChange,
  submitting,
  success,
  error,
  onClose,
  onSubmit,
}: FeedbackDialogProps) {
  const titleId = useId();
  const messageId = useId();
  const emailId = useId();
  const optionGroupId = useId();
  const overlayPointerDownRef = useRef(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKeydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    const previousOverflow = document.body.style.overflow;
    document.addEventListener("keydown", handleKeydown);
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeydown);
    };
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  const handleOverlayPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    overlayPointerDownRef.current = event.target === event.currentTarget;
  };

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget && overlayPointerDownRef.current) {
      onClose();
    }
    overlayPointerDownRef.current = false;
  };

  return (
    <div
      className="feedback-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onPointerDown={handleOverlayPointerDown}
      onClick={handleOverlayClick}
    >
      <div className="feedback-modal" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="feedback-close" aria-label="Lukk tilbakemeldingsskjema" onClick={onClose}>
          ×
        </button>
        {success ? (
          <div className="feedback-success">
            <h2 className="feedback-title" id={titleId}>
              Takk for tilbakemeldingen!
            </h2>
            <button type="button" className="analysis-button" onClick={onClose}>
              Lukk
            </button>
          </div>
        ) : (
          <>
            <div className="feedback-header">
              <span className="feedback-badge" aria-hidden="true">
                Vi lytter
              </span>
              <h2 className="feedback-title" id={titleId}>
                Gi oss tilbakemelding
              </h2>
              <p className="feedback-description">Fortell oss hva som fungerer, og hva vi kan forbedre.</p>
            </div>
            <form className="feedback-form" onSubmit={onSubmit}>
              <input type="hidden" name="category" value={category} />
              <div className="feedback-content-grid">
                <section className="feedback-options" aria-labelledby={optionGroupId}>
                  <span className="feedback-group-title" id={optionGroupId}>
                    Hva gjelder tilbakemeldingen?
                  </span>
                  <div className="feedback-option-list" role="radiogroup" aria-labelledby={optionGroupId}>
                    {FEEDBACK_OPTIONS.map((option) => {
                      const selected = option.id === category;
                      return (
                        <button
                          key={option.id}
                          type="button"
                          className={`feedback-option${selected ? " selected" : ""}`}
                          onClick={() => onSelectCategory(option.id)}
                          aria-pressed={selected}
                          disabled={submitting}
                        >
                          <span className="feedback-option-key">{option.shortcut}</span>
                          <span className="feedback-option-body">
                            <span className="feedback-option-label">{option.label}</span>
                            <span className="feedback-option-desc">{option.description}</span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </section>
                <section className="feedback-fields">
                  <div className="feedback-field feedback-field-message">
                    <label className="feedback-field-label" htmlFor={messageId}>
                      Tilbakemelding
                    </label>
                    <textarea
                      id={messageId}
                      name="message"
                      value={message}
                      onChange={(event) => {
                        event.currentTarget.setCustomValidity("");
                        onMessageChange(event.currentTarget.value);
                      }}
                      onInvalid={(event) => {
                        event.currentTarget.setCustomValidity("Vennligst fyll inn dette feltet");
                      }}
                      placeholder="Del tanker, utfordringer eller ønsker..."
                      required
                      disabled={submitting}
                    />
                  </div>
                  <div className="feedback-field feedback-field-email">
                    <label className="feedback-field-label" htmlFor={emailId}>
                      E-post (valgfritt)
                    </label>
                    <input
                      id={emailId}
                      type="email"
                      name="email"
                      value={email}
                      onChange={(event) => onEmailChange(event.target.value)}
                      placeholder="navn@virksomhet.no"
                      disabled={submitting}
                    />
                  </div>
                </section>
              </div>
              {error ? <p className="feedback-error">{error}</p> : null}
              <div className="feedback-actions">
                <button type="submit" className="analysis-button" disabled={submitting}>
                  {submitting ? "Sender..." : "Send"}
                </button>
              </div>
            </form>
          </>
        )}
      </div>
    </div>
  );
}

function ListingPreviewCard({
  listingUrl,
  listingTitle,
  listingAddress,
  imageUrls,
  containHints,
  currentIndex,
  onNavigatePrevious,
  onNavigateNext,
  loading,
  error,
  statusCard,
}: ListingPreviewCardProps) {
  const hasListing = Boolean(listingUrl);
  const imageCount = Array.isArray(imageUrls) ? imageUrls.length : 0;
  const hasImages = imageCount > 0;
  const safeIndex = hasImages ? Math.max(0, Math.min(currentIndex, imageCount - 1)) : 0;
  const currentImage = hasImages ? imageUrls[safeIndex] : null;
  const [dimensionContainHints, setDimensionContainHints] = useState<Set<string>>(() => new Set());
  const normalisedCurrentImage = useMemo(() => {
    if (!currentImage || typeof currentImage !== "string") {
      return null;
    }
    return normaliseImageUrlForContain(currentImage) ?? currentImage;
  }, [currentImage]);

  useEffect(() => {
    setDimensionContainHints(new Set());
  }, [imageUrls]);

  const handleImageLoad = useCallback(
    (event: SyntheticEvent<HTMLImageElement>) => {
      const target = event.currentTarget;
      const { naturalWidth, naturalHeight } = target;
      if (!naturalWidth || !naturalHeight) {
        return;
      }

      const rawSrc = target.currentSrc || target.src;
      const resolvedSrc = normaliseImageUrlForContain(rawSrc) ?? normalisedCurrentImage ?? rawSrc;
      if (!resolvedSrc) {
        return;
      }

      const heuristicallyFloorplan = resolvedSrc
        ? isLikelyFloorplanUrl(resolvedSrc, safeIndex, imageCount, {
            width: naturalWidth,
            height: naturalHeight,
          })
        : false;

      setDimensionContainHints((prev) => {
        const shouldContain = heuristicallyFloorplan;
        const hasEntry = prev.has(resolvedSrc);
        if (shouldContain && !hasEntry) {
          const next = new Set(prev);
          next.add(resolvedSrc);
          return next;
        }
        if (!shouldContain && hasEntry) {
          const next = new Set(prev);
          next.delete(resolvedSrc);
          return next;
        }
        return prev;
      });
    },
    [normalisedCurrentImage, imageCount, safeIndex],
  );

  const normalisedContainHints = useMemo(() => {
    if (!containHints || containHints.size === 0) {
      return new Set<string>();
    }
    const result = new Set<string>();
    containHints.forEach((value) => {
      if (typeof value !== "string") {
        return;
      }
      const normalised = normaliseImageUrlForContain(value);
      if (normalised) {
        result.add(normalised);
      } else {
        result.add(value);
      }
    });
    return result;
  }, [containHints]);

  const autoContainHints = useMemo(() => {
    if (!Array.isArray(imageUrls) || imageUrls.length === 0) {
      return new Set<string>();
    }
    const total = imageUrls.length;
    const result = new Set<string>();
    imageUrls.forEach((value, index) => {
      if (typeof value !== "string") {
        return;
      }
      const normalised = normaliseImageUrlForContain(value) ?? value;
      if (isLikelyFloorplanUrl(normalised, index, total)) {
        result.add(normalised);
      }
    });
    return result;
  }, [imageUrls]);

  const combinedContainHints = useMemo(() => {
    if (
      normalisedContainHints.size === 0 &&
      autoContainHints.size === 0 &&
      dimensionContainHints.size === 0
    ) {
      return dimensionContainHints;
    }
    const merged = new Set<string>();
    normalisedContainHints.forEach((value) => merged.add(value));
    autoContainHints.forEach((value) => merged.add(value));
    dimensionContainHints.forEach((value) => merged.add(value));
    return merged;
  }, [normalisedContainHints, autoContainHints, dimensionContainHints]);

  const shouldContainImage = useMemo(() => {
    if (!currentImage) {
      return false;
    }
    const normalised = normaliseImageUrlForContain(currentImage) ?? currentImage;
    if (combinedContainHints.has(normalised)) {
      return true;
    }
    return matchesFloorplanKeyword(normalised);
  }, [combinedContainHints, currentImage]);
  const heading = (() => {
    const trimmedAddress = listingAddress?.trim();
    if (trimmedAddress) {
      return trimmedAddress;
    }
    const trimmedTitle = listingTitle?.trim();
    if (trimmedTitle) {
      return trimmedTitle;
    }
    if (!hasListing) {
      return null;
    }
    try {
      const url = new URL(listingUrl);
      const pathname = decodeURIComponent(url.pathname.replace(/\/+$/, ""));
      const segments = pathname.split("/").filter(Boolean);
      if (segments.length > 0 && !segments[segments.length - 1].includes(".")) {
        return segments.join(" ");
      }
      return url.hostname;
    } catch {
      return listingUrl.replace(/^https?:\/\//i, "");
    }
  })();
  const srStatus = (() => {
    if (loading) {
      return "Laster forhåndsvisning fra FINN";
    }
    if (!hasListing) {
      return "Ingen FINN-lenke valgt";
    }
    if (!hasImages) {
      return error ?? "Fant ikke bilder for denne annonsen";
    }
    const baseDescription = listingAddress ?? listingTitle ?? "Bilde fra FINN-annonsen";
    return imageCount > 1
      ? `Viser bilde ${safeIndex + 1} av ${imageCount} fra FINN-annonsen`
      : baseDescription;
  })();
  const altText = (() => {
    const baseDescription = listingAddress ?? listingTitle ?? "Bilde fra FINN-annonsen";
    if (!hasImages) {
      return baseDescription;
    }
    return imageCount > 1 ? `${baseDescription} (${safeIndex + 1} av ${imageCount})` : baseDescription;
  })();
  const navigationDisabled = imageCount < 2;

  return (
    <aside className="listing-preview-card">
      {heading ? <h2 className="preview-heading">{heading}</h2> : null}
      <div className="preview-frame">
        {currentImage ? (
          <>
            <img
              src={currentImage}
              alt={altText}
              onLoad={handleImageLoad}
              className={shouldContainImage ? "listing-image listing-image-contain" : "listing-image"}
            />
            {imageCount > 1 ? (
              <>
                <button
                  type="button"
                  className="preview-nav-button preview-nav-prev"
                  onClick={onNavigatePrevious}
                  disabled={navigationDisabled}
                  aria-label="Forrige bilde"
                >
                  <span aria-hidden="true">{"<"}</span>
                </button>
                <button
                  type="button"
                  className="preview-nav-button preview-nav-next"
                  onClick={onNavigateNext}
                  disabled={navigationDisabled}
                  aria-label="Neste bilde"
                >
                  <span aria-hidden="true">{">"}</span>
                </button>
                <div className="preview-index" aria-label={`Totalt ${imageCount} bilder`}>
                  {safeIndex + 1} / {imageCount}
                </div>
              </>
            ) : null}
          </>
        ) : (
          <div className="listing-placeholder">
            <span>{srStatus}</span>
          </div>
        )}
      </div>
      {currentImage ? <span className="sr-only">{srStatus}</span> : null}
      {statusCard ? <div className="listing-status-card">{statusCard}</div> : null}
    </aside>
  );
}

interface KeyTooltipProps {
  tooltip: string;
  tooltipId: string;
  className?: string;
}

function KeyTooltip({ tooltip, tooltipId, className }: KeyTooltipProps) {
  const tooltipClassName = className ? `key-tooltip ${className}` : "key-tooltip";
  return (
    <span className={tooltipClassName}>
      <span className="key-info" aria-hidden="true">
        ?
      </span>
      <div className="key-tooltip-bubble" role="tooltip" id={tooltipId}>
        {tooltip}
      </div>
    </span>
  );
}

interface FormFieldProps {
  label: string;
  value: string;
  placeholder?: string;
  onChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  disabled?: boolean;
  tooltip?: string;
}

function FormField({ label, value, placeholder, onChange, disabled, tooltip }: FormFieldProps) {
  const reactId = useId();
  const tooltipId = tooltip ? `${reactId}-tooltip` : undefined;
  const fieldClass = disabled ? "form-field form-field-disabled" : "form-field";
  return (
    <label className={fieldClass}>
      <span className="form-field-label">
        <span className="form-field-label-text">{label}</span>
        {tooltipId && tooltip ? (
          <KeyTooltip tooltip={tooltip} tooltipId={tooltipId} className="form-field-tooltip" />
        ) : null}
      </span>
      <input
        value={value}
        placeholder={placeholder}
        onChange={onChange}
        disabled={disabled}
        aria-describedby={tooltipId}
      />
    </label>
  );
}

interface DecisionListProps {
  title: string;
  items: string[];
  empty: string;
  showPlaceholder?: boolean;
}

function DecisionList({ title, items, empty, showPlaceholder = true }: DecisionListProps) {
  const hasItems = items.length > 0;
  return (
    <div className="decision-card">
      <h3>{title}</h3>
      {hasItems ? (
        <ul>
          {items.slice(0, 6).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : showPlaceholder ? (
        <p className="placeholder">{empty}</p>
      ) : null}
    </div>
  );
}

interface ManualProspectusCardProps {
  file: File | null;
  onFileChange: (file: File | null) => void;
  onAnalyze: () => void;
  onClear: () => void;
  loading: boolean;
  error: string | null;
  success: boolean;
}

function formatFileSize(bytes: number): string {
  const kilobytes = bytes / 1024;
  if (kilobytes < 1024) {
    const rounded = kilobytes < 10 ? kilobytes.toFixed(1) : Math.round(kilobytes);
    return `${rounded} kB`;
  }
  const megabytes = kilobytes / 1024;
  const rounded = megabytes < 10 ? megabytes.toFixed(1) : Math.round(megabytes);
  return `${rounded} MB`;
}

function ManualProspectusCard({
  file,
  onFileChange,
  onAnalyze,
  onClear,
  loading,
  error,
  success,
}: ManualProspectusCardProps) {
  const inputId = useId();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const analyzeDisabled = loading || !file;
  const fileLabel = file ? `${file.name}${file.size ? ` • ${formatFileSize(file.size)}` : ""}` : null;

  useEffect(() => {
    if (!file && fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, [file]);

  const handleInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    const nextFile = event.target.files?.[0] ?? null;
    onFileChange(nextFile);
    event.target.value = "";
  };

  const handleDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragActive(false);
    if (loading) {
      return;
    }
    const nextFile = event.dataTransfer?.files?.[0] ?? null;
    if (!nextFile) {
      return;
    }
    onFileChange(nextFile);
  };

  const handleDragOver = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    if (loading) {
      return;
    }
    event.dataTransfer.dropEffect = "copy";
    if (!dragActive) {
      setDragActive(true);
    }
  };

  const handleDragLeave = () => {
    if (dragActive) {
      setDragActive(false);
    }
  };

  const uploadClassName = [
    "manual-prospectus-upload",
    dragActive ? "manual-prospectus-upload--drag" : null,
    loading ? "manual-prospectus-upload--disabled" : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="prospectus-card prospectus-card-span manual-prospectus-card">
      <div className="prospectus-card-header">
        <h3>Last opp salgsoppgaven</h3>
        <span className="prospectus-badge info">Manuell</span>
      </div>
      <p className="manual-prospectus-intro">
        Vi fant ikke salgsoppgaven automatisk. Last opp PDF-dokumentet.
      </p>
      <label className="manual-prospectus-label" htmlFor={inputId}>
        Salgsoppgave (PDF)
      </label>
      <label
        className={uploadClassName}
        htmlFor={inputId}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        data-disabled={loading ? "true" : undefined}
      >
        <input
          id={inputId}
          ref={fileInputRef}
          className="manual-prospectus-upload-input"
          type="file"
          accept="application/pdf"
          onChange={handleInputChange}
          disabled={loading}
        />
        <div className="manual-prospectus-upload-content">
          {file ? (
            <>
              <span className="manual-prospectus-upload-file">{fileLabel}</span>
            </>
          ) : (
            <span className="manual-prospectus-upload-title">Klikk eller slipp PDF her</span>
          )}
        </div>
      </label>
      {fileLabel ? (
        <p className="manual-prospectus-file-info">Valgt fil: {fileLabel}</p>
      ) : (
        <p className="manual-prospectus-file-hint">Kun PDF-filer støttes.</p>
      )}
      <div className="prospectus-actions manual-prospectus-actions">
        <button
          type="button"
          className="prospectus-action primary"
          onClick={onAnalyze}
          disabled={analyzeDisabled}
        >
          {loading ? "Analyserer…" : "Analyser PDF"}
        </button>
        <button
          type="button"
          className="prospectus-action secondary"
          onClick={onClear}
          disabled={loading || !file}
        >
          Fjern fil
        </button>
      </div>
      {error ? <p className="manual-prospectus-feedback error">{error}</p> : null}
      {!error && success ? (
        <p className="manual-prospectus-feedback success">TG-punktene er oppdatert.</p>
      ) : null}
    </div>
  );
}

interface ManualProspectusTextCardProps {
  text: string;
  onTextChange: (value: string) => void;
  onAnalyze: () => void;
  onClear: () => void;
  loading: boolean;
  error: string | null;
  success: boolean;
}

function ManualProspectusTextCard({
  text,
  onTextChange,
  onAnalyze,
  onClear,
  loading,
  error,
  success,
}: ManualProspectusTextCardProps) {
  const textareaId = useId();
  const trimmedLength = text.trim().length;
  const analyzeDisabled = loading || trimmedLength === 0;

  return (
    <div className="prospectus-card prospectus-card-span manual-prospectus-card manual-prospectus-text-card">
      <div className="prospectus-card-header">
        <h3>Lim inn salgsoppgaven</h3>
        <span className="prospectus-badge info">Manuell</span>
      </div>
      <p className="manual-prospectus-intro">
        Kopier sentrale deler fra salgsoppgaven (for eksempel TG-listen) og lim inn teksten her.
      </p>
      <label className="manual-prospectus-label" htmlFor={textareaId}>
        Tekstinnhold
      </label>
      <textarea
        id={textareaId}
        className="manual-prospectus-textarea"
        value={text}
        onChange={(event) => onTextChange(event.target.value)}
        placeholder="Lim inn utdrag fra salgsoppgaven …"
        rows={9}
        disabled={loading}
      />
      <div className="manual-prospectus-text-meta">
        <span>{trimmedLength.toLocaleString("nb-NO")} tegn</span>
      </div>
      <div className="prospectus-actions manual-prospectus-actions">
        <button
          type="button"
          className="prospectus-action primary"
          onClick={onAnalyze}
          disabled={analyzeDisabled}
        >
          {loading ? "Analyserer…" : "Analyser tekst"}
        </button>
        <button
          type="button"
          className="prospectus-action secondary"
          onClick={onClear}
          disabled={loading || text.length === 0}
        >
          Tøm feltet
        </button>
      </div>
      {error ? <p className="manual-prospectus-feedback error">{error}</p> : null}
      {!error && success ? (
        <p className="manual-prospectus-feedback success">TG-punktene er oppdatert.</p>
      ) : null}
    </div>
  );
}

type ProspectusDisplayItem = { label: string; detail: string };

interface ProspectusCardProps {
  title: string;
  items: ProspectusDisplayItem[];
  empty: string;
  badge?: { label: string; tone: "danger" | "warn" | "info" };
  className?: string;
  tooltip?: string;
}

function ProspectusCard({ title, items, empty, badge, className, tooltip }: ProspectusCardProps) {
  const hasItems = items.length > 0;
  const cardClass = className ? `prospectus-card ${className}` : "prospectus-card";
  const reactId = useId();
  const tooltipId = tooltip ? `${reactId}-tooltip` : undefined;
  return (
    <div className={cardClass}>
      <div className="prospectus-card-header">
        <h3 className="prospectus-card-title">
          <span>{title}</span>
          {tooltipId && tooltip ? (
            <KeyTooltip tooltip={tooltip} tooltipId={tooltipId} className="prospectus-card-tooltip" />
          ) : null}
        </h3>
        {badge ? <span className={`prospectus-badge ${badge.tone}`}>{badge.label}</span> : null}
      </div>
      {hasItems ? (
        <ul className="prospectus-card-list">
          {items.slice(0, 5).map((item, index) => (
            <li key={`${reactId}-${index}`}>
              <span
                className="prospectus-card-list-item"
                data-tooltip={item.detail}
                title={item.detail}
                aria-label={item.detail}
                tabIndex={0}
              >
                <span className="prospectus-card-list-label">{item.label}</span>
                <span className="prospectus-card-list-arrow" aria-hidden="true">
                  &rarr;
                </span>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="placeholder">{empty}</p>
      )}
    </div>
  );
}

function toStringArray(value: unknown, limit?: number): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const cleaned = value
    .map((item) => {
      if (typeof item === "string") {
        return item.trim();
      }
      if (item === null || item === undefined) {
        return "";
      }
      return String(item).trim();
    })
    .filter(Boolean);
  if (typeof limit === "number" && limit > 0) {
    return cleaned.slice(0, limit);
  }
  return cleaned;
}

const PROSPECTUS_LABEL_WITH_PREPOSITION = /\b(?:for|ved|til|på|av|og)\s+TG\s*[-/]*\s*(?:2|3)\b[,;:]?/gi;
const PROSPECTUS_LABEL_PATTERN = /\bTG\s*[-/]*\s*(?:2|3)\b[):\s,.;-]*/gi;
const PROSPECTUS_CJK_PATTERN = /[\u3000-\u303f\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/g;
const PROSPECTUS_COMPONENT_TERMS = [
  "bad",
  "baderom",
  "vaskerom",
  "vatrm",
  "kjokken",
  "kjokkeninnredning",
  "kjeller",
  "loft",
  "tak",
  "taket",
  "taktekking",
  "takstein",
  "takbelegg",
  "pipe",
  "piper",
  "skorstein",
  "vindu",
  "vinduer",
  "dorer",
  "ytterdor",
  "ytterdoer",
  "innervegg",
  "yttervegg",
  "vegg",
  "vegger",
  "gulv",
  "bjelkelag",
  "grunnmur",
  "fundament",
  "drener",
  "radon",
  "ventilasjon",
  "avtrekk",
  "terrasse",
  "balkong",
  "veranda",
  "rekkverk",
  "trapp",
  "fasade",
  "kledning",
  "isolasjon",
  "mur",
  "betong",
  "puss",
  "sikringsskap",
  "elanlegg",
  "elektrisk",
  "elektro",
  "varmtvannsbereder",
  "bereder",
  "ror",
  "avlop",
  "avloppsror",
  "sanitar",
  "sluk",
  "membran",
  "vatrom",
  "garasje",
  "carport",
  "bod",
  "takstol",
  "bjaelke",
  "loftsbjelke",
  "nedlop",
  "takrenne",
  "renne",
  "yttertett",
  "tegl",
  "grunn",
];
const PROSPECTUS_ISSUE_TERMS = [
  "ikke godkjent",
  "fukt",
  "fuktskade",
  "fuktmerke",
  "lekk",
  "rate",
  "raate",
  "mugg",
  "sopp",
  "skade",
  "skader",
  "sprekk",
  "sprekker",
  "defekt",
  "mangel",
  "avvik",
  "korrosjon",
  "rust",
  "utett",
  "svikt",
  "brudd",
  "fare",
  "risiko",
  "eldre",
  "gammel",
  "slitt",
  "slitasje",
  "oppgradering",
  "utbedring",
  "rehab",
  "oppussing",
  "avrenning",
  "setnings",
  "skjev",
  "ubehandlet",
  "sprukket",
  "manglende",
  "ukjent",
  "byttes",
  "bytte",
  "ma skiftes",
  "utskift",
  "brann",
  "brannfare",
  "kondens",
  "tett",
];

const PROSPECTUS_LABEL_STOPWORDS = new Set<string>([
  "og",
  "samt",
  "men",
  "eller",
  "dersom",
  "hvis",
  "når",
  "naar",
  "derfor",
  "dermed",
  "så",
  "sa",
  "der",
  "her",
  "det",
  "den",
  "dette",
  "disse",
  "som",
  "kan",
  "skal",
  "må",
  "maa",
  "bør",
  "bor",
  "er",
  "ble",
  "blir",
  "blitt",
  "har",
  "hadde",
  "får",
  "fikk",
  "være",
  "vaere",
  "vært",
  "innen",
  "etter",
  "før",
  "forst",
  "først",
  "mer",
  "mindre",
  "enn",
  "eventuelt",
  "evt",
  "inkl",
  "inkludert",
  "ca",
  "cirka",
  "omtrent",
  "pkt",
  "punkt",
  "div",
  "diverse",
  "annet",
  "mm",
  "etc",
  "osv",
  "osb",
  "ulike",
  "flere",
  "slik",
  "slike",
  "typ",
  "type",
  "vedr",
  "generelt",
  "gjelder",
  "gjeldene",
]);

const PROSPECTUS_LABEL_CONNECTORS = new Set<string>(["med", "uten", "på", "pa", "i", "ved", "mot"]);

const PROSPECTUS_LABEL_REPLACEMENTS: Record<string, string> = {
  taktekking: "tak",
  taktekkingen: "tak",
  taktekkings: "tak",
  taket: "tak",
  takkonstruksjon: "tak",
  takkonstruksjonen: "tak",
  takflate: "tak",
  takflater: "tak",
  takrenne: "takrenne",
  takrenner: "takrenner",
  taknedløp: "nedløp",
  nedlop: "nedløp",
  nedlopet: "nedløp",
  vindu: "vinduer",
  vinduer: "vinduer",
  vinduskarm: "vinduskarm",
  vinduskarmen: "vinduskarm",
  vinduskarmer: "vinduskarm",
  badet: "bad",
  bad: "bad",
  baderom: "bad",
  baderommet: "bad",
  vaskerommet: "vaskerom",
  kjokken: "kjøkken",
  kjokkenet: "kjøkken",
  kjokkeninnredning: "kjøkken",
  kjokkeninnredningen: "kjøkken",
  kjeller: "kjeller",
  kjelleren: "kjeller",
  grunnmur: "grunnmur",
  grunnmuren: "grunnmur",
  drenering: "drenering",
  drenerer: "drenering",
  drener: "drenering",
  ror: "rør",
  rorene: "rør",
  rorledning: "rør",
  rorledninger: "rør",
  avlop: "avløp",
  avlopet: "avløp",
  avloppsror: "avløp",
  avloppsrorene: "avløp",
  elanlegg: "elanlegg",
  elektrisk: "elektrisk",
  elektriske: "elektrisk",
  elektro: "elektro",
  sikringsskap: "sikringsskap",
  sikringsskapet: "sikringsskap",
  varmeanlegg: "varme",
  varmepumpe: "varmepumpe",
  varmepumper: "varmepumpe",
  varmekabler: "varmekabler",
  varmtvannsbereder: "bereder",
  varmtvannsberederen: "bereder",
  varmvannsbereder: "bereder",
  varmvannsberederen: "bereder",
  fuktighet: "fukt",
  fuktskader: "fuktskade",
  fuktmerker: "fuktmerke",
  lekkasjer: "lekkasje",
  lekk: "lekkasje",
  skade: "skader",
  skadet: "skader",
  skades: "skader",
  mangler: "mangler",
  mangel: "mangler",
  losore: "løsøre",
  losoere: "løsøre",
  inventar: "inventar",
  sluk: "sluk",
  sluket: "sluk",
  membran: "membran",
  terrasse: "terrasse",
  terrassen: "terrasse",
  balkong: "balkong",
  balkongen: "balkong",
  veranda: "veranda",
  fasaden: "fasade",
  vegg: "vegg",
  vegger: "vegger",
  yttervegger: "yttervegg",
  innervegger: "innervegg",
  kjokkenvifte: "ventilator",
  kjokkenventilator: "ventilator",
  pipe: "pipe",
  piper: "pipe",
  skorstein: "pipe",
  skorsteinen: "pipe",
  ovn: "ovn",
  panelovn: "ovn",
  panelovner: "ovn",
  kjellervegg: "kjellervegg",
  kjellervegger: "kjellervegg",
  dreneringssystem: "drenering",
  hev: "heving",
  synk: "setning",
};

function simplifyProspectusValue(value: string): string {
  return value
    .toLowerCase()
    .replace(/ø/g, "o")
    .replace(/å/g, "a")
    .replace(/æ/g, "ae")
    .replace(/é/g, "e")
    .replace(/ö/g, "o")
    .replace(/ü/g, "u");
}

function prospectusTokens(value: string): string[] {
  return value.split(/[^a-z0-9]+/).filter(Boolean);
}

function containsProspectusTerm(value: string, term: string, tokens: string[]): boolean {
  if (term.includes(" ")) {
    return value.includes(term);
  }
  return tokens.includes(term);
}

function isSpecificProspectusEntry(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }
  const simplified = simplifyProspectusValue(trimmed);
  if (simplified.length < 8) {
    return false;
  }
  const tokens = prospectusTokens(simplified);
  if (containsProspectusTerm(simplified, "ikke godkjent", tokens)) {
    return true;
  }
  const hasComponent = PROSPECTUS_COMPONENT_TERMS.some((term) =>
    containsProspectusTerm(simplified, term, tokens),
  );
  if (!hasComponent) {
    return false;
  }
  const hasIssue = PROSPECTUS_ISSUE_TERMS.some((term) => containsProspectusTerm(simplified, term, tokens));
  if (hasIssue) {
    return true;
  }
  return trimmed.split(/\s+/).length >= 3;
}

function sanitizeProspectusEntry(value: string): string {
  let result = value.replace(PROSPECTUS_LABEL_WITH_PREPOSITION, " ");
  result = result.replace(PROSPECTUS_LABEL_PATTERN, " ");
  result = result.replace(PROSPECTUS_CJK_PATTERN, "");
  result = result.replace(/\(\s*\)/g, " ");
  result = result.replace(/\s{2,}/g, " ");
  result = result.replace(/\s*,\s*/g, ", ");
  result = result.replace(/\s*;\s*/g, "; ");
  result = result.replace(/\s*:\s*/g, ": ");
  result = result.replace(/\s*\.\s*/g, ". ");
  result = result.trim();
  if (result.startsWith("(") && result.endsWith(")")) {
    result = result.slice(1, -1).trim();
  }
  result = result.replace(/^[,:;.\-]+/, "").replace(/[,:;.\-]+$/, "").trim();
  return result;
}

type ProspectusSanitizeOptions = {
  limit?: number;
  preserveLabels?: boolean;
};

function sanitizeProspectusItems(
  items: readonly string[],
  limitOrOptions?: number | ProspectusSanitizeOptions,
): string[] {
  const options: ProspectusSanitizeOptions =
    typeof limitOrOptions === "number" ? { limit: limitOrOptions } : limitOrOptions ?? {};
  const preserveLabels = options.preserveLabels ?? false;
  const effectiveLimit =
    options.limit !== undefined ? options.limit : preserveLabels ? undefined : 8;
  const seen = new Set<string>();
  const cleaned: string[] = [];
  for (const item of items) {
    if (item === null || item === undefined) {
      continue;
    }
    const raw = (typeof item === "string" ? item : String(item)).trim();
    if (!raw) {
      continue;
    }
    const sanitized = sanitizeProspectusEntry(raw).trim();
    if (!sanitized || !isSpecificProspectusEntry(sanitized)) {
      continue;
    }
    const key = simplifyProspectusValue(sanitized);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    cleaned.push(preserveLabels ? raw : sanitized);
    if (typeof effectiveLimit === "number" && effectiveLimit > 0 && cleaned.length >= effectiveLimit) {
      break;
    }
  }
  return cleaned;
}

function normaliseProspectusDetails(value: unknown): ProspectusDetail[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const details: ProspectusDetail[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    const record = entry as Record<string, unknown>;
    const label = typeof record.label === "string" ? record.label.trim() : "";
    const detail = typeof record.detail === "string" ? record.detail.trim() : "";
    if (!label || !detail) {
      continue;
    }
    const source = typeof record.source === "string" ? record.source.trim() : undefined;
    const level = typeof record.level === "number" ? record.level : undefined;
    details.push({ label, detail, source, level });
  }
  return details;
}

function buildProspectusDisplayItems(
  detailItems: readonly ProspectusDetail[],
  fallbackStrings: readonly string[],
  extraStrings: readonly string[] = [],
  limit = 5,
): ProspectusDisplayItem[] {
  const items: ProspectusDisplayItem[] = [];
  const seen = new Set<string>();

  const pushItem = (label: string, detail: string) => {
    const cleanLabel = label.trim();
    const cleanDetail = normaliseProspectusDetailText(detail);
    if (!cleanLabel || !cleanDetail) {
      return;
    }
    const key = `${cleanLabel.toLowerCase()}__${cleanDetail.toLowerCase()}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    items.push({ label: cleanLabel, detail: cleanDetail });
  };

  for (const entry of detailItems) {
    const label = deriveProspectusLabel(entry.label, entry.detail);
    pushItem(label, entry.detail);
    if (typeof limit === "number" && limit > 0 && items.length >= limit) {
      return items;
    }
  }

  for (const value of fallbackStrings) {
    const detail = normaliseProspectusDetailText(value);
    if (!detail) {
      continue;
    }
    const label = deriveProspectusLabel(value, detail);
    pushItem(label, detail);
    if (typeof limit === "number" && limit > 0 && items.length >= limit) {
      return items.slice(0, limit);
    }
  }

  for (const value of extraStrings) {
    const detail = normaliseProspectusDetailText(value);
    if (!detail) {
      continue;
    }
    const label = deriveProspectusLabel(detail);
    pushItem(label, detail);
    if (typeof limit === "number" && limit > 0 && items.length >= limit) {
      return items.slice(0, limit);
    }
  }

  return typeof limit === "number" && limit > 0 ? items.slice(0, limit) : items;
}

function deriveProspectusLabel(primary?: string, secondary?: string): string {
  const detailWords = extractProspectusLabelWords(secondary);
  if (detailWords.length >= 2) {
    return formatProspectusLabel(detailWords.slice(0, 3));
  }
  const primaryWords = extractProspectusLabelWords(primary);
  const combined = [...detailWords, ...primaryWords].filter((word, index, array) => array.indexOf(word) === index);
  if (combined.length > 0) {
    return formatProspectusLabel(combined.slice(0, 3));
  }
  const fallbackSource = [primary, secondary]
    .filter((value): value is string => Boolean(value && value.trim()))
    .join(" ");
  const fallbackWords = extractProspectusLabelWords(fallbackSource);
  if (fallbackWords.length > 0) {
    return formatProspectusLabel(fallbackWords.slice(0, 3));
  }
  return "TG";
}

function extractProspectusLabelWords(value?: string): string[] {
  if (!value) {
    return [];
  }
  const normalized = normaliseProspectusDetailText(value);
  if (!normalized) {
    return [];
  }
  const segments = normalized
    .split(/(?:\r?\n|[.;!,?]|,| - | – | — |:)/)
    .map((segment) => segment.trim())
    .filter(Boolean);
  for (const segment of segments) {
    const words = tokenizeProspectusLabelSegment(segment);
    if (words.length > 0) {
      return words;
    }
  }
  return tokenizeProspectusLabelSegment(normalized);
}

function tokenizeProspectusLabelSegment(segment: string): string[] {
  const sanitized = segment
    .replace(/[(){}\[\]]/g, " ")
    .replace(/[-/]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!sanitized) {
    return [];
  }
  const rawTokens = sanitized
    .normalize("NFKD")
    .toLowerCase()
    .split(/\s+/)
    .map((token) => token.replace(/[^0-9a-zæøåäöüéè]/g, ""))
    .filter(Boolean);
  if (rawTokens.length === 0) {
    return [];
  }
  const keywords: string[] = [];
  const connectors: string[] = [];
  for (const rawToken of rawTokens) {
    if (/^tg\d?$/.test(rawToken) || rawToken === "tilstandsgrad") {
      continue;
    }
    const replacement = PROSPECTUS_LABEL_REPLACEMENTS[rawToken] ?? rawToken;
    if (!replacement) {
      continue;
    }
    if (PROSPECTUS_LABEL_STOPWORDS.has(replacement)) {
      continue;
    }
    if (PROSPECTUS_LABEL_CONNECTORS.has(replacement)) {
      if (!connectors.includes(replacement)) {
        connectors.push(replacement);
      }
      continue;
    }
    if (!keywords.includes(replacement)) {
      keywords.push(replacement);
    }
  }
  const orderedKeywords = [
    ...keywords.filter((token) => !/^\d/.test(token)),
    ...keywords.filter((token) => /^\d/.test(token)),
  ];
  if (orderedKeywords.length < 2) {
    for (const rawToken of rawTokens) {
      const replacement = PROSPECTUS_LABEL_REPLACEMENTS[rawToken] ?? rawToken;
      if (!replacement) {
        continue;
      }
      if (
        PROSPECTUS_LABEL_STOPWORDS.has(replacement) ||
        orderedKeywords.includes(replacement) ||
        /^tg\d?$/.test(replacement)
      ) {
        continue;
      }
      if (PROSPECTUS_LABEL_CONNECTORS.has(replacement)) {
        if (!connectors.includes(replacement)) {
          connectors.push(replacement);
        }
        continue;
      }
      orderedKeywords.push(replacement);
      if (orderedKeywords.length >= 2) {
        break;
      }
    }
  }
  if (orderedKeywords.length === 0) {
    return [];
  }
  return composeProspectusLabelWords(orderedKeywords, connectors);
}

function composeProspectusLabelWords(keywords: string[], connectors: string[]): string[] {
  if (keywords.length === 0) {
    return [];
  }
  const words: string[] = [];
  const keywordQueue = [...keywords];
  const connectorQueue = [...connectors];
  words.push(keywordQueue.shift()!);
  if (keywordQueue.length > 0) {
    if (connectorQueue.length > 0) {
      words.push(connectorQueue.shift()!);
    }
    words.push(keywordQueue.shift()!);
  }
  if (words.length < 3 && keywordQueue.length > 0) {
    words.push(keywordQueue.shift()!);
  }
  return words.slice(0, 3);
}

function formatProspectusLabel(words: string[]): string {
  if (words.length === 0) {
    return "TG";
  }
  const [first, ...rest] = words;
  const formatted = [capitaliseProspectusLabelWord(first)];
  for (const word of rest) {
    formatted.push(formatProspectusLabelTailWord(word));
  }
  return formatted.join(" ");
}

function capitaliseProspectusLabelWord(word: string): string {
  if (!word) {
    return word;
  }
  if (/^\d/.test(word)) {
    return word;
  }
  return word.charAt(0).toUpperCase() + word.slice(1);
}

function formatProspectusLabelTailWord(word: string): string {
  if (!word) {
    return word;
  }
  if (/^\d/.test(word)) {
    return word;
  }
  return word.toLowerCase();
}

function normaliseProspectusDetailText(value: string | undefined): string {
  if (!value) {
    return "";
  }
  return value.replace(/\s+/g, " ").trim();
}

function normaliseProspectusLinks(value: unknown): ProspectusLinks | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const rawHref = (() => {
    const possibleKeys = ["salgsoppgave_pdf", "salgsoppgavePdf", "pdf", "url"];
    for (const key of possibleKeys) {
      const raw = record[key];
      if (typeof raw === "string" && raw.trim()) {
        return raw.trim();
      }
    }
    return null;
  })();
  const hrefCandidate = normaliseExternalUrl(rawHref);
  const confidenceCandidate = record.confidence ?? record.score ?? record.confidence_score;
  let confidence: number | null = null;
  if (typeof confidenceCandidate === "number" && Number.isFinite(confidenceCandidate)) {
    confidence = Math.min(1, Math.max(0, confidenceCandidate));
  } else if (typeof confidenceCandidate === "string") {
    const parsed = Number.parseFloat(confidenceCandidate);
    if (Number.isFinite(parsed)) {
      confidence = Math.min(1, Math.max(0, parsed));
    }
  }
  const message =
    typeof record.message === "string" && record.message.trim() ? record.message.trim() : null;

  if (!hrefCandidate && confidence === null && !message) {
    return null;
  }

  return {
    salgsoppgave_pdf: hrefCandidate,
    confidence,
    message,
  };
}

function normaliseProspectusExtract(value: unknown): ProspectusExtract | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const tg3 = toStringArray(record.tg3);
  const tg2 = toStringArray(record.tg2);
  const tg3Details = normaliseProspectusDetails(record.tg3_details ?? record.TG3_details);
  const tg2Details = normaliseProspectusDetails(record.tg2_details ?? record.TG2_details);
  const upgrades = toStringArray(record.upgrades);
  const watchouts = toStringArray(record.watchouts);
  const questions = toStringArray(record.questions);
  const links =
    normaliseProspectusLinks(record.links) ??
    normaliseProspectusLinks(record.Links) ??
    null;
  const tgMarkdown = typeof record.tg_markdown === "string" ? record.tg_markdown : undefined;
  const missingComponents = toStringArray(record.tg_missing_components ?? record.missing_components);
  const extract: ProspectusExtract = {
    summary_md: typeof record.summary_md === "string" ? record.summary_md : undefined,
    tg3,
    tg2,
    upgrades,
    watchouts,
    questions,
    links: links ?? undefined,
    tg3_details: tg3Details.length > 0 ? tg3Details : undefined,
    tg2_details: tg2Details.length > 0 ? tg2Details : undefined,
    tg_markdown: tgMarkdown,
    tg_missing_components: missingComponents.length > 0 ? missingComponents : undefined,
  };
  if (
    !extract.summary_md &&
    tg3.length === 0 &&
    tg2.length === 0 &&
    tg3Details.length === 0 &&
    tg2Details.length === 0 &&
    upgrades.length === 0 &&
    watchouts.length === 0 &&
    questions.length === 0 &&
    !links
  ) {
    return null;
  }
  return extract;
}

function extractProspectusFromJob(job: JobStatus | null): ProspectusExtract | null {
  if (!job) {
    return null;
  }
  const directLinks = normaliseProspectusLinks(job.result?.links);
  let fallbackLinks: ProspectusLinks | null = directLinks;
  const resultExtract = job.result?.ai_extract;
  if (resultExtract) {
    const normalised = normaliseProspectusExtract(resultExtract);
    if (normalised) {
      if (!normalised.links && fallbackLinks) {
        return { ...normalised, links: fallbackLinks };
      }
      return normalised;
    }
  }
  const artifacts = job.artifacts && typeof job.artifacts === "object" ? (job.artifacts as Record<string, unknown>) : null;
  if (!fallbackLinks && artifacts && "links" in artifacts) {
    fallbackLinks = normaliseProspectusLinks((artifacts as { links?: unknown }).links);
  }
  if (artifacts && "ai_extract" in artifacts) {
    const normalised = normaliseProspectusExtract((artifacts as { ai_extract?: unknown }).ai_extract);
    if (normalised) {
      if (!normalised.links && fallbackLinks) {
        return { ...normalised, links: fallbackLinks };
      }
      return normalised;
    }
  }
  if (fallbackLinks) {
    return { links: fallbackLinks };
  }
  return null;
}
