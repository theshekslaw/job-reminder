# Template: abhishek-default

> **Note:** `template.tex` here is LOCAL-ONLY (gitignored — it is the user's real resume
> and carries PII). On a fresh clone, seed it from your own resume:
> `cp <your-resume>.tex templates/cv/abhishek-default/template.tex` (keeping the macro
> structure below), or register your own template via `/add-template`.

- **Type:** CV
- **Source extension:** `.tex`
- **Compile command:** `pdflatex -interaction=nonstopmode <file>`
- **Fonts:** LaTeX defaults (Computer Modern) — no fontspec, no bundled fonts, no font path needed
- **Page limit:** exactly 1 page
- **Output file:** `cv/main_<company>_<role>.tex`

## Machine-readable constraints (enforced by tools/template_guard.py)

```yaml
page_limit: 1
compile_command: pdflatex
editable_regions:
  - summary            # Professional Summary paragraph text
  - skills_rows        # right-hand cells of the Technical Skills tabular
  - experience_bullets # \resumeItem{...} contents
  - project_selection  # which \resumeExperienceProject / \resumeProjectHeading blocks appear
  - achievements       # Achievements & Certifications \resumeItem contents
forbidden_regions:
  - preamble           # everything before \begin{document} is byte-identical to this template
  - macro_definitions  # \newcommand blocks
  - geometry           # margins, page size
  - packages           # \usepackage lines
```

Run after every draft/revision:
```bash
uv run tools/template_guard.py --draft cv/main_<company>_<role>.tex
```

## Structure (single column, top to bottom)

Header (name + contact line) → Professional Summary → Technical Skills (two-column
`tabular*`) → Experience (`\resumeSubheading` per company, overall `\resumeItem`
bullets, then `\resumeExperienceProject` blocks with `\resumeProjectItemListStart`
bullets) → Projects (`\resumeProjectHeading`) → Education → Achievements & Certifications.

## Tailoring rules

- Tailor by **editing content inside the macros**: reword the summary around the
  posting's keywords, swap/reorder skills-row terms, re-emphasize bullets, and
  select which project blocks appear. Never regenerate the document from scratch.
- The 1-page budget is enforced by cutting via relevance-weighted scoring
  (lowest relevance-to-THIS-posting bullet goes first), never by shrinking
  geometry, font sizes, or list spacing.
- Contact header stays verbatim — name, phone, email, LinkedIn/GitHub/LeetCode links.

## Known pitfalls

- Escape `%` in metrics (`89\%`) — an unescaped `%` silently truncates the rest of the line.
- Use `---` for em-dashes inside bullets; plain `--` in a date range renders as en-dash (correct there).
- Company headings are NOT list items — `\resumeSubheading` is a full-width table row;
  never wrap it in an itemize.
- `\resumeItem` bullets already wrap in `\small` — no font commands inside bullets.
- Keep `hyperref` links as-is; the email/phone must remain literal text (ATS parses
  the text layer, not link targets).
- pdflatex is sufficient (no fontawesome5/fontspec); do NOT switch to lualatex/xelatex.
