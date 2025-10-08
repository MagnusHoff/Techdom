"use client";

/* eslint-disable @next/next/no-img-element */

import { useRouter, useSearchParams } from "next/navigation";
import {
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
} from "react";

import { PageContainer, SiteFooter, SiteHeader } from "../components/chrome";
import { getJobStatus, incrementUserAnalyses, runAnalysis, startAnalysisJob } from "@/lib/api";
import type {
  AnalysisPayload,
  AnalysisResponse,
  DecisionUi,
  JobStatus,
  KeyFactRaw,
  ListingDetailsDTO,
  ProspectusExtract,
  ProspectusLinks,
} from "@/lib/types";

const USER_UPDATED_EVENT = "techdom:user-updated";

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
  return FLOORPLAN_KEYWORDS.some((keyword) => normalised.includes(keyword));
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
  const result = new Set<string>();
  for (const value of values) {
    if (typeof value !== "string") {
      continue;
    }
    const normalised = normaliseImageUrlForContain(value);
    if (!normalised) {
      continue;
    }
    if (matchesFloorplanKeyword(normalised)) {
      result.add(normalised);
    }
  }
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

const KEY_FACT_PRIORITY_BASE = 100;

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
  const match = String(value).toUpperCase().match(/[A-G]/);
  if (!match) {
    return null;
  }
  const grade = match[0];
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

function colorClass(farge?: string): string {
  switch ((farge ?? "").toLowerCase()) {
    case "red":
      return "score-chip red";
    case "orange":
      return "score-chip orange";
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
      return "key-value orange";
    case "yellow":
      return "key-value orange";
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
    return "#f97316"; // oransje
  }
  if (percent < 66) {
    return "#a3e635"; // grønn-oransje (lime)
  }
  if (percent < 84) {
    return "#22c55e"; // grønn
  }
  return "#14532d"; // mørkegrønn
}

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
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackCategory, setFeedbackCategory] = useState<FeedbackCategory>("idea");
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [feedbackEmail, setFeedbackEmail] = useState("");
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [feedbackSuccess, setFeedbackSuccess] = useState(false);
  const [feedbackErrorMessage, setFeedbackErrorMessage] = useState<string | null>(null);
  const jobListingRef = useRef<string | null>(null);
  const jobAppliedRef = useRef<string | null>(null);
  const skipJobInitRef = useRef(process.env.NODE_ENV !== "production");

  const registerAnalysisCompletion = useCallback(() => {
    void incrementUserAnalyses()
      .then((updatedUser) => {
        if (typeof window === "undefined") {
          return;
        }
        window.dispatchEvent(
          new CustomEvent(USER_UPDATED_EVENT, { detail: updatedUser }),
        );
      })
      .catch(() => {
        /* ignore missing auth */
      });
  }, [incrementUserAnalyses]);

  const listingDetails = useMemo(() => extractListingInfo(jobStatus), [jobStatus]);
  const listingKeyFacts = useMemo(() => extractKeyFactsRaw(listingDetails), [listingDetails]);
  useEffect(() => {
    if (!listingDetails) {
      setDetailsOpen(false);
    }
  }, [listingDetails]);

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
  const tg2DisplaySource = useMemo(() => sanitizeProspectusItems(tg2Items), [tg2Items]);
  const tg3DisplayItems = useMemo(() => sanitizeProspectusItems(tg3Items), [tg3Items]);
  const watchoutDisplayItems = useMemo(() => sanitizeProspectusItems(watchoutItems), [watchoutItems]);
  const tg2DisplayItems = useMemo(() => {
    if (watchoutDisplayItems.length === 0) {
      return tg2DisplaySource;
    }
    const unique: string[] = [];
    const seen = new Set<string>();
    for (const item of [...tg2DisplaySource, ...watchoutDisplayItems]) {
      if (!item || seen.has(item)) {
        continue;
      }
      seen.add(item);
      unique.push(item);
    }
    return unique;
  }, [tg2DisplaySource, watchoutDisplayItems]);
  const tgDataAvailable = tg2Items.length > 0 || tg3Items.length > 0;
  const hasProspectusSignals = tg2DisplayItems.length > 0 || tg3DisplayItems.length > 0;
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
  const submitLabel = analyzing ? "Oppdaterer..." : jobFailed ? "Kjør manuelt" : "Oppdater";
  const resourcePdfUrl = useMemo(() => {
    const candidate = effectiveLinks?.salgsoppgave_pdf;
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
    return stringOrNull(jobStatus?.pdf_url);
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

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
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
      registerAnalysisCompletion();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Klarte ikke å hente analyse.");
    } finally {
      setAnalyzing(false);
    }
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
    setProspectus(null);
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
      registerAnalysisCompletion();
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
  }, [jobStatus, jobId, listingDetails, registerAnalysisCompletion]);

  return (
    <>
      <ListingDetailsModal
        open={detailsOpen}
        details={listingDetails}
        keyFacts={listingKeyFacts}
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
              onShowDetails={() => setDetailsOpen(true)}
            />
          </div>
          {jobFailed && !jobCompleted ? (
            <p className="analysis-manual-hint">
              Automatisk innhenting feilet. Fyll inn tallene manuelt og trykk &quot;Kjør manuelt&quot;.
            </p>
          ) : null}
          <form className="analysis-form" onSubmit={handleSubmit}>
            <div className="form-grid">
              <FormField
                label="Kjøpesum"
                value={form.price}
                placeholder="4 500 000"
                onChange={handleChange("price")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Egenkapital"
                value={form.equity}
                placeholder="675 000"
                onChange={handleChange("equity")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Rente % p.a."
                value={form.interest}
                placeholder="5.10"
                onChange={handleChange("interest")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Lånetid (år)"
                value={form.term_years}
                placeholder="30"
                onChange={handleChange("term_years")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Leie (mnd)"
                value={form.rent}
                placeholder="18 000"
                onChange={handleChange("rent")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Felleskost (mnd)"
                value={form.hoa}
                placeholder="3 000"
                onChange={handleChange("hoa")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Vedlikehold % av leie"
                value={form.maint_pct}
                placeholder="6.0"
                onChange={handleChange("maint_pct")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Andre kost (mnd)"
                value={form.other_costs}
                placeholder="800"
                onChange={handleChange("other_costs")}
                disabled={fieldsDisabled}
              />
              <FormField
                label="Ledighet %"
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
              {decisionUi?.dom_notat ? <p className="status-note">{decisionUi.dom_notat}</p> : null}
            </div>

            <div className="analysis-score-spacer" aria-hidden="true" />

            <div className="analysis-column analysis-column-economy">
              <div className="key-grid">
                {(decisionUi?.nokkel_tall ?? []).map((item, index) => {
                  const navn = typeof item.navn === "string" ? item.navn : "";
                  const verdi = typeof item.verdi === "string" ? item.verdi : String(item.verdi ?? "");
                  const farge = typeof item.farge === "string" ? item.farge : undefined;
                  return (
                    <div className="key-card" key={`${navn}-${index}`}>
                      <p className="key-name">{navn}</p>
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
                <ProspectusCard
                  title="⚠️ TG2"
                  badge={{ label: "Middels risiko", tone: "warn" }}
                  items={tg2DisplayItems}
                  empty="Ingen TG2- eller observasjonspunkter funnet ennå."
                />
                <ProspectusCard
                  title="🛑 TG3 (alvorlig)"
                  badge={{ label: "Høy risiko", tone: "danger" }}
                  items={tg3DisplayItems}
                  empty="Ingen TG3-punkter funnet ennå."
                />
                <ProspectusLinkCard linkInfo={effectiveLinks} />
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
  const label = jobStatusHeadline(status, stateKey);
  const progressValueRaw = typeof status?.progress === "number" ? Math.max(0, Math.min(100, status.progress)) : null;
  const isActive = stateKey !== "failed" && stateKey !== "done";
  const progressValue = isActive ? null : (stateKey === "done" ? 100 : progressValueRaw);
  const showIndeterminate = isActive;
  const showProgress = stateKey !== "failed";
  const failureMessage = stateKey === "failed"
    ? stringOrNull(status?.message) ?? stringOrNull(status?.error) ?? jobError
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
  const primaryPdfUrl = linkInfo?.salgsoppgave_pdf ?? pdfUrl;

  useEffect(() => {
    if (primaryPdfUrl) {
      setDiscoveredPdfUrl(null);
      setDiscoveringPdf(false);
      return;
    }
    if (!listingUrl) {
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
        setDiscoveredPdfUrl(nextUrl);
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
  }, [listingUrl, primaryPdfUrl]);

  const fallbackProspectLink = buildFinnProspectLink(listingUrl);
  const salgsoppgaveHref = primaryPdfUrl ?? discoveredPdfUrl ?? fallbackProspectLink;
  const salgsoppgaveState = primaryPdfUrl
    ? "primary"
    : discoveredPdfUrl
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
  onClose: () => void;
  title: string | null;
  address: string | null;
}

function ListingDetailsModal({ open, details, keyFacts, onClose, title, address }: ListingDetailsModalProps) {
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
    const source = keyFacts && keyFacts.length ? keyFacts : extractKeyFactsRaw(details);
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
    const decorated = facts.map((fact, index) => {
      const labelKey = normaliseFactLabel(fact.label);
      const priority = labelKey !== null && KEY_FACT_PRIORITY_MAP.has(labelKey)
        ? KEY_FACT_PRIORITY_MAP.get(labelKey) ?? index
        : KEY_FACT_PRIORITY_BASE + index;
      return { fact, index, priority };
    });
    decorated.sort((a, b) => {
      if (a.priority !== b.priority) {
        return a.priority - b.priority;
      }
      return a.index - b.index;
    });
    return decorated.map((entry) => entry.fact);
  }, [facts]);

  if (!open) {
    return null;
  }

  const heading = "Nøkkeltall";
  const subtitle = address || title;

  return (
    <div className="listing-details-overlay" role="dialog" aria-modal="true" aria-labelledby="listing-details-title">
      <div className="listing-details-modal">
        <button type="button" className="listing-details-close" aria-label="Lukk detaljer" onClick={onClose}>
          ×
        </button>
        <h2 className="listing-details-title" id="listing-details-title">
          {heading}
        </h2>
        {subtitle ? <p className="listing-details-subtitle">{subtitle}</p> : null}
        {displayFacts.length ? (
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
            <p>Vi tar kontakt dersom vi trenger mer informasjon.</p>
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
                      onChange={(event) => onMessageChange(event.target.value)}
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
  const shouldContainImage = useMemo(() => {
    if (!currentImage) {
      return false;
    }
    if (containHints?.has(currentImage)) {
      return true;
    }
    const normalised = normaliseImageUrlForContain(currentImage) ?? currentImage;
    return matchesFloorplanKeyword(normalised);
  }, [containHints, currentImage]);
  const heading = (() => {
    const trimmedAddress = listingAddress?.trim();
    if (trimmedAddress) {
      return trimmedAddress;
    }
    const trimmedTitle = listingTitle?.trim();
    if (trimmedTitle) {
      return trimmedTitle;
    }
    if (!hasListing || !listingUrl) {
      return null;
    }
    const normalizedListingUrl = listingUrl?.trim();
    if (!normalizedListingUrl) {
      return null;
    }
    try {
      const url = new URL(normalizedListingUrl);
      const pathname = decodeURIComponent(url.pathname.replace(/\/+$/, ""));
      const segments = pathname.split("/").filter(Boolean);
      if (segments.length > 0 && !segments[segments.length - 1].includes(".")) {
        return segments.join(" ");
      }
      return url.hostname;
    } catch {
      return normalizedListingUrl.replace(/^https?:\/\//i, "");
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

interface FormFieldProps {
  label: string;
  value: string;
  placeholder?: string;
  onChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  disabled?: boolean;
}

function FormField({ label, value, placeholder, onChange, disabled }: FormFieldProps) {
  const fieldClass = disabled ? "form-field form-field-disabled" : "form-field";
  return (
    <label className={fieldClass}>
      <span>{label}</span>
      <input value={value} placeholder={placeholder} onChange={onChange} disabled={disabled} />
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

interface ProspectusCardProps {
  title: string;
  items: string[];
  empty: string;
  badge?: { label: string; tone: "danger" | "warn" | "info" };
  className?: string;
}

function ProspectusCard({ title, items, empty, badge, className }: ProspectusCardProps) {
  const hasItems = items.length > 0;
  const cardClass = className ? `prospectus-card ${className}` : "prospectus-card";
  return (
    <div className={cardClass}>
      <div className="prospectus-card-header">
        <h3>{title}</h3>
        {badge ? <span className={`prospectus-badge ${badge.tone}`}>{badge.label}</span> : null}
      </div>
      {hasItems ? (
        <ul>
          {items.slice(0, 6).map((item, index) => (
            <li key={`${item}-${index}`}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="placeholder">{empty}</p>
      )}
    </div>
  );
}

interface ProspectusLinkCardProps {
  linkInfo: ProspectusLinks | null;
}

function ProspectusLinkCard({ linkInfo }: ProspectusLinkCardProps) {
  const href = typeof linkInfo?.salgsoppgave_pdf === "string" && linkInfo.salgsoppgave_pdf.trim()
    ? linkInfo.salgsoppgave_pdf.trim()
    : null;
  const confidenceText =
    typeof linkInfo?.confidence === "number" && Number.isFinite(linkInfo.confidence)
      ? `Konfidens ${Math.round(linkInfo.confidence * 100)}%`
      : null;
  const anchorTitle = linkInfo?.message ?? confidenceText ?? undefined;
  const disabledTitle = linkInfo?.message ?? "Ikke funnet";
  const fallbackMessage = !href ? linkInfo?.message ?? "Ikke funnet" : null;
  return (
    <div className="prospectus-card prospectus-salgsoppgave-card">
      <div className="prospectus-card-header">
        <h3>Salgsoppgave</h3>
      </div>
      <div className="prospectus-actions">
        {href ? (
          <a className="prospectus-action primary" href={href} target="_blank" rel="noopener noreferrer" title={anchorTitle}>
            Salgsoppgave (PDF)
          </a>
        ) : (
          <button type="button" className="prospectus-action primary" disabled title={disabledTitle}>
            Salgsoppgave (PDF)
          </button>
        )}
        {href && confidenceText ? <span className="prospectus-confidence">{confidenceText}</span> : null}
      </div>
      {fallbackMessage ? <p className="prospectus-empty-note">{fallbackMessage}</p> : null}
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

function sanitizeProspectusItems(items: readonly string[]): string[] {
  const seen = new Set<string>();
  const cleaned: string[] = [];
  for (const item of items) {
    const sanitized = sanitizeProspectusEntry(item).trim();
    if (!sanitized || !isSpecificProspectusEntry(sanitized)) {
      continue;
    }
    const key = simplifyProspectusValue(sanitized);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    cleaned.push(sanitized);
    if (cleaned.length >= 10) {
      break;
    }
  }
  return cleaned;
}

function normaliseProspectusLinks(value: unknown): ProspectusLinks | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const hrefCandidate = (() => {
    const possibleKeys = ["salgsoppgave_pdf", "salgsoppgavePdf", "pdf", "url"];
    for (const key of possibleKeys) {
      const raw = record[key];
      if (typeof raw === "string" && raw.trim()) {
        return raw.trim();
      }
    }
    return null;
  })();
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
  const upgrades = toStringArray(record.upgrades);
  const watchouts = toStringArray(record.watchouts);
  const questions = toStringArray(record.questions);
  const links =
    normaliseProspectusLinks(record.links) ??
    normaliseProspectusLinks(record.Links) ??
    null;
  const extract: ProspectusExtract = {
    summary_md: typeof record.summary_md === "string" ? record.summary_md : undefined,
    tg3,
    tg2,
    upgrades,
    watchouts,
    questions,
    links: links ?? undefined,
  };
  if (
    !extract.summary_md &&
    tg3.length === 0 &&
    tg2.length === 0 &&
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
