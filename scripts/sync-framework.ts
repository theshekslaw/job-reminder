#!/usr/bin/env bun
/**
 * sync-framework.ts — copy the vendored ai-job-search framework (packages/ai-job-search,
 * a pinned git submodule) into the repo root and re-apply our overlays.
 *
 * File categories:
 *   SYNC_ALWAYS — framework-owned; overwritten on every sync.
 *   INIT_ONLY   — user-owned once created (profile/skill files /setup fills in);
 *                 copied only if missing. If upstream changed one since the last
 *                 sync, a notice is printed so the user can merge manually.
 *   overlays/   — our patches; mirrors target paths and is copied LAST, always.
 *                 overlays/overlays.lock.json records the upstream sha256 each
 *                 overlay was written against; if upstream drifts, we warn
 *                 instead of silently keeping a stale patch.
 *
 * Never touched: .env, documents/ contents, state/, CLAUDE.md, README.md.
 * Idempotent: running twice changes nothing.
 */

import { createHash } from "node:crypto";
import {
  cpSync, existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync,
} from "node:fs";
import { dirname, join, relative } from "node:path";

const ROOT = join(import.meta.dir, "..");
const SRC = join(ROOT, "packages/ai-job-search");
const OVERLAYS = join(ROOT, "overlays");
const STATE_FILE = join(ROOT, ".framework-sync.json"); // committed: upstream hashes at last sync
const LOCK_FILE = join(OVERLAYS, "overlays.lock.json");

// ── manifest ────────────────────────────────────────────────────────────────

/** Framework-owned: directories/files overwritten on every sync. */
const SYNC_ALWAYS = [
  ".claude/commands",
  ".claude/settings.json",
  ".agents/skills",
  "tools",
  ".claude/skills/job-application-assistant/SKILL.md",
  ".claude/skills/job-scraper/SKILL.md",
  ".claude/skills/upskill/SKILL.md",
  "cover_letters/cover.cls",
  "cover_letters/cover_example.tex",
  "cover_letters/OpenFonts",
  "templates",
  "salary_lookup.py",
];

/** User-owned once created: copied only if the destination is missing. */
const INIT_ONLY = [
  ".claude/skills/job-application-assistant", // 01–09 reference/profile files
  ".claude/skills/job-scraper/search-queries.md",
  "cv/main_example.tex",
  "documents/README.md",
];

/** Empty working directories the pipeline expects. */
const ENSURE_DIRS = [
  "documents/cv", "documents/linkedin", "documents/diplomas", "documents/references",
  "documents/projects", "documents/applications", "documents/postings",
  "cv", "job_scraper", "company_research", "resume",
];

// ── helpers ─────────────────────────────────────────────────────────────────

const sha256 = (p: string) => createHash("sha256").update(readFileSync(p)).digest("hex");

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(p);
    else yield p;
  }
}

/** All files under a manifest entry (file or directory), as repo-relative paths. */
function filesOf(base: string, entry: string): string[] {
  const abs = join(base, entry);
  if (!existsSync(abs)) return [];
  if (statSync(abs).isFile()) return [entry];
  return [...walk(abs)].map((p) => relative(base, p));
}

function copyFile(rel: string) {
  const dest = join(ROOT, rel);
  mkdirSync(dirname(dest), { recursive: true });
  cpSync(join(SRC, rel), dest);
}

const loadJson = (p: string): Record<string, string> =>
  existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : {};

// ── main ────────────────────────────────────────────────────────────────────

if (!existsSync(join(SRC, ".claude"))) {
  console.error("sync: submodule packages/ai-job-search is missing or not initialized.");
  console.error("      run: git submodule update --init");
  process.exit(1);
}

const prevHashes = loadJson(STATE_FILE);
const newHashes: Record<string, string> = {};
let copied = 0, initialized = 0, skipped = 0;
const notices: string[] = [];

// 1. framework-owned files: overwrite
for (const entry of SYNC_ALWAYS) {
  const files = filesOf(SRC, entry);
  if (!files.length) notices.push(`upstream no longer has: ${entry}`);
  for (const rel of files) {
    copyFile(rel);
    newHashes[rel] = sha256(join(SRC, rel));
    copied++;
  }
}

// 2. init-only files: copy if missing; notice if upstream changed since last sync
for (const entry of INIT_ONLY) {
  for (const rel of filesOf(SRC, entry)) {
    if (newHashes[rel]) continue; // already handled by a SYNC_ALWAYS rule
    const upstream = sha256(join(SRC, rel));
    newHashes[rel] = upstream;
    if (!existsSync(join(ROOT, rel))) {
      copyFile(rel);
      initialized++;
    } else {
      skipped++;
      if (prevHashes[rel] && prevHashes[rel] !== upstream) {
        notices.push(`upstream updated user-owned file (merge manually if wanted): ${rel}`);
      }
    }
  }
}

// 3. working directories
for (const d of ENSURE_DIRS) mkdirSync(join(ROOT, d), { recursive: true });

// 4. overlays: ours win, applied last. Warn when upstream drifted under a patch.
let overlaid = 0;
const lock = loadJson(LOCK_FILE);
if (existsSync(OVERLAYS)) {
  for (const p of walk(OVERLAYS)) {
    const rel = relative(OVERLAYS, p);
    if (rel === "overlays.lock.json" || rel === "README.md") continue;
    const upstreamFile = join(SRC, rel);
    if (existsSync(upstreamFile)) {
      const upstream = sha256(upstreamFile);
      if (lock[rel] && lock[rel] !== upstream) {
        notices.push(
          `CONFLICT: upstream changed ${rel} since our overlay was written — ` +
          `review packages/ai-job-search/${rel}, update overlays/${rel}, then refresh overlays.lock.json`,
        );
      } else if (!lock[rel]) {
        notices.push(`overlay ${rel} has no entry in overlays.lock.json — add its upstream sha256`);
      }
    }
    const dest = join(ROOT, rel);
    mkdirSync(dirname(dest), { recursive: true });
    cpSync(p, dest);
    overlaid++;
  }
}

writeFileSync(STATE_FILE, JSON.stringify(newHashes, null, 2) + "\n");

console.log(`sync: ${copied} framework files synced, ${initialized} initialized, ` +
            `${skipped} user-owned kept, ${overlaid} overlays applied`);
for (const n of notices) console.log(`  ⚠ ${n}`);
console.log("sync: done");
