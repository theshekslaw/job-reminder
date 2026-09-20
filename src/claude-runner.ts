/**
 * ClaudeRunner — the ONLY code that touches the raw `claude -p` CLI envelope
 * (DESIGN.md: Adapter pattern). Everything else consumes ClaudeRunResult.
 *
 * Also enforces the env-isolation invariant: headless Claude gets a WHITELIST,
 * never process.env wholesale. Secrets (DATABASE_URL, MinIO, tokens) stay out.
 */

export interface ClaudeRunResult {
  status: "ok" | "error";
  output: string;
  usage: { tokensIn: number; tokensOut: number };
  durationMs: number;
  model: string | null;
  error: string | null;
}

const ENV_WHITELIST = [
  "JOB_INTERESTS", "JOB_LOCATIONS", "RESUME_USERNAME", "KEYWORD_COVERAGE_THRESHOLD",
  // process basics the CLI itself needs:
  "HOME", "PATH", "SHELL", "TERM", "LANG", "USER", "TMPDIR",
];

export async function runClaude(opts: {
  prompt: string;
  model?: string;
  timeoutMs?: number;
  cwd?: string;
}): Promise<ClaudeRunResult> {
  const env: Record<string, string> = {};
  for (const k of ENV_WHITELIST) if (process.env[k]) env[k] = process.env[k]!;

  const args = ["-p", opts.prompt, "--output-format", "json"];
  if (opts.model) args.push("--model", opts.model);

  const started = Date.now();
  const proc = Bun.spawn(["claude", ...args], {
    env,
    cwd: opts.cwd ?? process.cwd(),
    stdout: "pipe",
    stderr: "pipe",
  });

  const timeout = opts.timeoutMs ?? 15 * 60 * 1000;
  const killer = setTimeout(() => proc.kill(), timeout);
  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  clearTimeout(killer);
  const durationMs = Date.now() - started;

  // Parse the envelope defensively — format details live ONLY here.
  try {
    const envelope = JSON.parse(stdout);
    return {
      status: exitCode === 0 && envelope.subtype !== "error" ? "ok" : "error",
      output: typeof envelope.result === "string" ? envelope.result : stdout,
      usage: {
        tokensIn: Number(envelope?.usage?.input_tokens ?? 0),
        tokensOut: Number(envelope?.usage?.output_tokens ?? 0),
      },
      durationMs: Number(envelope?.duration_ms ?? durationMs),
      model: typeof envelope?.model === "string" ? envelope.model : null,
      error: exitCode === 0 ? null : (envelope?.error ?? (stderr.slice(0, 500) || `exit ${exitCode}`)),
    };
  } catch {
    return {
      status: exitCode === 0 ? "ok" : "error",
      output: stdout,
      usage: { tokensIn: 0, tokensOut: 0 },
      durationMs,
      model: null,
      error: exitCode === 0 ? null : stderr.slice(0, 500) || `exit ${exitCode}`,
    };
  }
}
