/**
 * Portal adapters: discover installed portal CLIs (the framework's Agent Skills
 * under .agents/skills/) and run their `search` command per the portal contract
 * (search/detail, --format json, results: [{id,title,company,location,date,url}]).
 * Zero-LLM — these are plain bun CLIs.
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { normalize, type JobPosting } from "./schema";

export interface Portal {
  name: string;
  cliPath: string;
  enabled: boolean;
  supportsLocation: boolean;
}

const SKILLS_DIR = ".agents/skills";

export function discoverPortals(root = "."): Portal[] {
  const dir = join(root, SKILLS_DIR);
  if (!existsSync(dir)) return [];
  const portals: Portal[] = [];
  for (const name of readdirSync(dir)) {
    const skillMd = join(dir, name, "SKILL.md");
    const cliPath = join(dir, name, "cli/src/cli.ts");
    if (!existsSync(skillMd) || !existsSync(cliPath)) continue;
    const md = readFileSync(skillMd, "utf8");
    const enabled = !/^enabled:\s*false/m.test(md);
    const supportsLocation = /(^|\s)(-l|--location)\b/.test(md);
    portals.push({ name, cliPath, enabled, supportsLocation });
  }
  return portals;
}

export interface SearchOutcome {
  portal: string;
  query: string;
  location: string | null;
  postings: JobPosting[];
  error: string | null;
}

export async function searchPortal(
  portal: Portal,
  query: string,
  location: string | null,
  limit = 20,
): Promise<SearchOutcome> {
  const args = ["run", portal.cliPath, "search", "-q", query, "--format", "json",
                "--limit", String(limit)];
  if (location && portal.supportsLocation) args.push("-l", location);

  const proc = Bun.spawn(["bun", ...args], { stdout: "pipe", stderr: "pipe" });
  const killer = setTimeout(() => proc.kill(), 60_000);
  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  clearTimeout(killer);

  const base = { portal: portal.name, query, location, postings: [] as JobPosting[] };
  if (exitCode !== 0) {
    return { ...base, error: stderr.trim().slice(0, 300) || `exit ${exitCode}` };
  }
  try {
    const parsed = JSON.parse(stdout);
    const results: unknown[] = Array.isArray(parsed?.results) ? parsed.results : [];
    const postings = results
      .map((r) => normalize(portal.name, r as Record<string, unknown>))
      .filter((p): p is JobPosting => p !== null);
    return { ...base, postings, error: null };
  } catch {
    return { ...base, error: "unparseable CLI output" };
  }
}
