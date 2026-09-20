#!/usr/bin/env bun
/**
 * job-hunt backend CLI. Usage: bun run src/cli.ts <command> [args]
 * (or: make run ARGS="<command> ...")
 *
 *   scrape                    zero-LLM portal sweep → normalize → dedup → DB (DISCOVERED)
 *   digest [--days N]         new matches since N days (default 1)
 *   queue                     applications awaiting your review
 *   stats                     applications by state + token usage
 *   approve <id>              publish via tools/publish_guard.py (state gate enforced there)
 *   reject <id> [reason]      reject an application
 *   override <id> <reason>    HUMAN_OVERRIDE past a blocked coverage check (recorded)
 *   apply <id|url>            run /apply headless via ClaudeRunner (stops at review gate)
 *   export-csv                dump applications as CSV to stdout (derived view)
 */

import { sql, assertDb } from "./db";
import { applicationId } from "./schema";
import { discoverPortals, searchPortal } from "./portals";
import { transition } from "./state";
import { runClaude } from "./claude-runner";

const [cmd, ...rest] = process.argv.slice(2);

const interests = (process.env.JOB_INTERESTS ?? "").replace(/^"|"$/g, "")
  .split(",").map((s) => s.trim()).filter(Boolean);
const locations = (process.env.JOB_LOCATIONS ?? "").replace(/^"|"$/g, "")
  .split(";").map((s) => s.trim()).filter(Boolean);

async function logRun(fields: {
  applicationId?: string | null; command: string; stage: string;
  tokensIn?: number; tokensOut?: number; durationMs?: number; model?: string | null;
  result: string;
}) {
  await sql`
    INSERT INTO runs (application_id, command, stage, model, tokens_in, tokens_out, duration_ms, result)
    VALUES (${fields.applicationId ?? null}, ${fields.command}, ${fields.stage},
            ${fields.model ?? null}, ${fields.tokensIn ?? 0}, ${fields.tokensOut ?? 0},
            ${fields.durationMs ?? 0}, ${fields.result})`;
}

// ── scrape ──────────────────────────────────────────────────────────────────

async function scrape() {
  if (!interests.length) {
    console.error("scrape: JOB_INTERESTS is empty — set it in .env");
    process.exit(1);
  }
  const portals = discoverPortals().filter((p) => p.enabled);
  if (!portals.length) {
    console.error("scrape: no enabled portal CLIs found under .agents/skills/ — run make sync");
    process.exit(1);
  }
  console.log(`scrape: portals [${portals.map((p) => p.name).join(", ")}] × interests [${interests.join(", ")}]`);

  const started = Date.now();
  let seen = 0, inserted = 0, newSources = 0;
  const errors: string[] = [];

  for (const portal of portals) {
    const locs = portal.supportsLocation && locations.length ? locations : [null];
    for (const interest of interests) {
      for (const loc of locs) {
        const out = await searchPortal(portal, interest, loc);
        if (out.error) {
          errors.push(`${portal.name} "${interest}"${loc ? ` @ ${loc}` : ""}: ${out.error}`);
          continue;
        }
        for (const p of out.postings) {
          seen++;
          const id = applicationId(p.company, p.title, p.location);
          const posting = await sql`
            INSERT INTO job_postings (application_id, company, title, location, description, salary, posted_at)
            VALUES (${id}, ${p.company}, ${p.title}, ${p.location}, ${p.description}, ${p.salary}, ${p.posted_at})
            ON CONFLICT (application_id) DO NOTHING RETURNING application_id`;
          if (posting.length) {
            inserted++;
            await sql`INSERT INTO applications (application_id) VALUES (${id})
                      ON CONFLICT (application_id) DO NOTHING`;
            await sql`INSERT INTO transitions (application_id, to_state, reason, actor)
                      VALUES (${id}, 'DISCOVERED', ${"scraped from " + portal.name}, 'system')`;
          }
          const src = await sql`
            INSERT INTO job_sources (application_id, source, source_id, url)
            VALUES (${id}, ${p.source}, ${p.source_id}, ${p.url})
            ON CONFLICT (source, source_id) DO NOTHING RETURNING id`;
          if (src.length) newSources++;
        }
      }
    }
  }

  const summary = `${seen} results, ${inserted} new jobs, ${newSources} new source sightings, ${errors.length} errors`;
  await logRun({ command: "scrape", stage: "scrape", durationMs: Date.now() - started, result: summary });
  console.log(`scrape: ${summary}`);
  for (const e of errors) console.log(`  ⚠ ${e}`);
}

// ── read views ──────────────────────────────────────────────────────────────

function table(rows: Record<string, unknown>[]) {
  if (!rows.length) return console.log("  (none)");
  console.table(rows);
}

async function digest() {
  const daysIdx = rest.indexOf("--days");
  const days = daysIdx >= 0 ? Number(rest[daysIdx + 1]) : 1;
  const rows = await sql`
    SELECT a.application_id AS id, p.company, p.title, p.location,
           r.rank_score, a.state, p.scraped_at::date AS scraped
    FROM applications a
    JOIN job_postings p USING (application_id)
    LEFT JOIN LATERAL (SELECT rank_score FROM rankings
                       WHERE application_id = a.application_id
                       ORDER BY at DESC LIMIT 1) r ON true
    WHERE p.scraped_at > now() - make_interval(days => ${days})
    ORDER BY r.rank_score DESC NULLS LAST, p.scraped_at DESC
    LIMIT 40`;
  console.log(`digest: jobs discovered in the last ${days} day(s) — pick one and run: make run ARGS="apply <id>"`);
  table(rows);
}

async function queue() {
  const rows = await sql`
    SELECT a.application_id AS id, p.company, p.title,
           a.keyword_coverage_score AS coverage, a.fit_rating AS fit,
           rp.pdf_path, a.updated_at::date AS waiting_since
    FROM applications a
    JOIN job_postings p USING (application_id)
    LEFT JOIN LATERAL (SELECT pdf_path FROM review_packets
                       WHERE application_id = a.application_id
                       ORDER BY created_at DESC LIMIT 1) rp ON true
    WHERE a.state = 'AWAITING_APPROVAL'
    ORDER BY a.updated_at`;
  console.log("queue: awaiting your review — approve/reject with: make run ARGS=\"approve <id>\"");
  table(rows);
}

async function stats() {
  const byState = await sql`
    SELECT state, count(*)::int AS count FROM applications GROUP BY state ORDER BY count DESC`;
  const tokens = await sql`
    SELECT count(*)::int AS runs,
           coalesce(sum(tokens_in), 0)::bigint AS tokens_in,
           coalesce(sum(tokens_out), 0)::bigint AS tokens_out,
           coalesce(sum(duration_ms), 0)::bigint AS total_ms
    FROM runs`;
  const perApp = await sql`
    SELECT coalesce(avg(t.total), 0)::int AS avg_tokens_per_application
    FROM (SELECT application_id, sum(tokens_in + tokens_out) AS total
          FROM runs WHERE application_id IS NOT NULL GROUP BY application_id) t`;
  console.log("applications by state:");
  table(byState);
  console.log("usage:");
  table([{ ...tokens[0], ...perApp[0] }]);
}

// ── actions ─────────────────────────────────────────────────────────────────

async function approve(id: string) {
  if (!id) { console.error("usage: approve <application_id>"); process.exit(1); }
  // Side effects ONLY via the guard (invariant #2). It checks state itself.
  const proc = Bun.spawn(["uv", "run", "tools/publish_guard.py", "--application-id", id],
    { stdout: "inherit", stderr: "inherit" });
  const code = await proc.exited;
  if (code !== 0) {
    console.error(code === 2
      ? "approve: publish_guard refused (see message above)"
      : "approve: tools/publish_guard.py failed or is not implemented yet");
    process.exit(code);
  }
}

async function reject(id: string) {
  if (!id) { console.error("usage: reject <application_id> [reason]"); process.exit(1); }
  const reason = rest.slice(1).join(" ") || "rejected by user";
  await transition(id, "REJECTED", { reason, actor: process.env.USER ?? "human" });
  console.log(`rejected ${id}: ${reason}`);
}

async function override(id: string) {
  const reason = rest.slice(1).join(" ");
  if (!id || !reason) {
    console.error("usage: override <application_id> <reason>  (recorded HUMAN_OVERRIDE past COVERAGE_BLOCKED)");
    process.exit(1);
  }
  await transition(id, "VALIDATED", { reason, actor: process.env.USER ?? "human" });
  console.log(`override recorded for ${id}; state → VALIDATED`);
}

async function apply(target: string) {
  if (!target) { console.error("usage: apply <application_id|url>"); process.exit(1); }
  let prompt = `/apply ${target}`;
  if (!/^https?:\/\//.test(target)) {
    const rows = await sql`
      SELECT p.title, p.company, s.url FROM job_postings p
      LEFT JOIN job_sources s USING (application_id)
      WHERE p.application_id = ${target} ORDER BY s.first_seen_at LIMIT 1`;
    if (!rows.length) { console.error(`apply: unknown application_id ${target}`); process.exit(1); }
    if (!rows[0].url) { console.error(`apply: no URL recorded for ${target} — pass the posting URL`); process.exit(1); }
    prompt = `/apply ${rows[0].url}`;
    console.log(`apply: ${rows[0].company} — ${rows[0].title}`);
  }
  console.log("apply: running Claude headless (stops at the review gate)…");
  const res = await runClaude({ prompt, timeoutMs: 20 * 60 * 1000 });
  await logRun({
    applicationId: /^https?:\/\//.test(target) ? null : target,
    command: "apply", stage: "apply-headless",
    tokensIn: res.usage.tokensIn, tokensOut: res.usage.tokensOut,
    durationMs: res.durationMs, model: res.model,
    result: res.status === "ok" ? "ok" : `error: ${res.error}`,
  });
  console.log(res.output.slice(-2000));
  if (res.status !== "ok") process.exit(1);
  console.log('\napply: check "make run ARGS=queue" — review the PDF, then approve/reject.');
}

async function exportCsv() {
  const rows = await sql`
    SELECT a.application_id, p.company, p.title, p.location, a.state,
           a.fit_rating, a.keyword_coverage_score, a.resume_path,
           p.scraped_at::date AS scraped, a.updated_at::date AS updated
    FROM applications a JOIN job_postings p USING (application_id)
    ORDER BY a.updated_at DESC`;
  const cols = ["application_id","company","title","location","state","fit_rating",
                "keyword_coverage_score","resume_path","scraped","updated"];
  console.log(cols.join(","));
  for (const r of rows) {
    console.log(cols.map((c) => JSON.stringify(r[c] ?? "")).join(","));
  }
}

// ── dispatch ────────────────────────────────────────────────────────────────

const commands: Record<string, () => Promise<void>> = {
  scrape, digest, queue, stats,
  approve: () => approve(rest[0]),
  reject: () => reject(rest[0]),
  override: () => override(rest[0]),
  apply: () => apply(rest[0]),
  "export-csv": exportCsv,
};

if (!cmd || !commands[cmd]) {
  console.log("usage: job-hunt <scrape|digest|queue|stats|approve|reject|override|apply|export-csv>");
  process.exit(cmd ? 1 : 0);
}

await assertDb();
try {
  await commands[cmd]();
} finally {
  await sql.end({ timeout: 3 });
}
