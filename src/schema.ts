/**
 * Normalized JobPosting + application identity.
 *
 * Job identity is SEPARATE from source identity (DESIGN.md):
 *   application_id = sha256(canonical company|title|location)[:16]
 * Board-specific ids live in job_sources as evidence.
 *
 * Canonicalization mirrors the framework's subfolder naming rule
 * (documents/README.md): lowercase, spaces→underscores, drop every
 * non-alphanumeric/underscore char, collapse runs, trim.
 */

import { createHash } from "node:crypto";

export interface JobPosting {
  source: string;
  source_id: string;
  url: string | null;
  company: string;
  title: string;
  location: string | null;
  remote_type: string | null;
  description: string | null;
  salary: string | null;
  posted_at: string | null; // ISO date
}

export function canonical(s: string | null | undefined): string {
  return (s ?? "")
    .toLowerCase()
    .trim()
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_]/g, "")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "");
}

export function applicationId(company: string, title: string, location: string | null): string {
  const key = `${canonical(company)}|${canonical(title)}|${canonical(location)}`;
  return createHash("sha256").update(key).digest("hex").slice(0, 16);
}

/** Portal CLIs emit {id,title,company,location,date,url} per the portal contract. */
export function normalize(source: string, raw: Record<string, unknown>): JobPosting | null {
  const company = typeof raw.company === "string" ? raw.company.trim() : "";
  const title = typeof raw.title === "string" ? raw.title.trim() : "";
  if (!company || !title) return null; // unusable card — skip, never guess
  return {
    source,
    source_id: String(raw.id ?? raw.url ?? `${company}:${title}`),
    url: typeof raw.url === "string" ? raw.url : null,
    company,
    title,
    location: typeof raw.location === "string" ? raw.location : null,
    remote_type: null,
    description: typeof raw.description === "string" ? raw.description : null,
    salary: typeof raw.salary === "string" ? raw.salary : null,
    posted_at: typeof raw.date === "string" && /^\d{4}-\d{2}-\d{2}/.test(raw.date)
      ? raw.date.slice(0, 10)
      : null,
  };
}
