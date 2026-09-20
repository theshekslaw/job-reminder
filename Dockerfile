# job-hunt backend image: bun (TS CLI) + uv (Python tools).
# LaTeX is deliberately NOT baked in (multi-GB) — compilation uses local
# pdflatex or the texlive/texlive docker image (see Makefile compile-example).

FROM oven/bun:1 AS base

# uv — fast Python package manager (installs its own Python if needed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency layers first for caching
COPY package.json bun.lock* ./
RUN bun install --frozen-lockfile || bun install

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen || uv sync

# Application code (PII directories are gitignored and never in build context —
# see .dockerignore)
COPY src/ src/
COPY tools/ tools/
COPY db/ db/
COPY scripts/ scripts/

ENTRYPOINT ["bun", "run", "src/cli.ts"]
CMD ["stats"]
