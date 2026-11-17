# NeuroRadar

Interactive globe of neurotech startups (386 companies from the Neurofounders Start-up Map), with open roles refreshed every morning.

Live site: https://ettomagni.github.io/neuroradar/

Static site (HTML + JS + JSON, no build step): `index.html`, `app.js`, `styles.css`, `data/`.

## Run it locally

```bash
python3 -m http.server 5173
```

Then open http://localhost:5173 (it needs a server: the JSON does not load from `file://`).

## Data and scripts (`scripts/`, Python standard library only)

| Script | What it does | When |
|---|---|---|
| `build_seed.py` | creates `data/companies.json` from the company list (local file, not in the repo) — **overwrites enrichment** | once, at the start |
| `enrich_profiles.py` | from each Neurofounders profile: website, founding year, modality, form factor, invasiveness, indication, users, regulatory stage, funding | once (cached in `data/cache/`) |
| `find_careers.py` | finds the careers page and ATS from the company website (Greenhouse, Lever, Ashby, Workable, Personio, Recruitee, SmartRecruiters, BambooHR, Breezy) | Mondays, retries unresolved companies |
| `probe_ats.py` | for companies with no visible ATS: queries the Greenhouse/Ashby/Lever APIs directly; a board is accepted only if the full company name or domain appears in its postings and they are about neurotech. Namesakes rejected by hand go in `ats_rejected` | Mondays, after `find_careers.py` |
| `fetch_jobs.py` | downloads roles from each ATS, diffs them with yesterday, flags new/closed | every morning |

Output files: `data/jobs.json` (open roles), `data/job_texts.json` (full posting texts for CV matching: rebuilt on every run and **not** published in the repo), `data/jobs_closed.json` (closed roles, kept 60 days), `data/runs.json` (stats and errors for each run).

The first run of `fetch_jobs.py` is a **baseline**: the roles it finds are not flagged as new. After that, "new" means first seen in the last 72 hours.

## Daily update

`.github/workflows/daily.yml` runs every day at 05:00 UTC on GitHub Actions and commits the updated JSON. To run it by hand: *Actions* tab → *NeuroRadar daily update* → *Run workflow*.

## Claude (runs on your Claude subscription)

The Claude steps run Claude Code headless (`claude -p`, model Haiku) on your Claude subscription, so there is no pay-as-you-go API billing; they use your plan's usage limits. Each run makes at most `CLAUDE_MAX_CALLS` calls (300); anything left is done on the next run.

- On GitHub: run `claude setup-token` on your computer and save the token as the repository secret `CLAUDE_CODE_OAUTH_TOKEN` (*Settings → Secrets and variables → Actions*).
- Locally: be signed in to Claude Code (`claude`), and put `CLAUDE_CODE_OAUTH_TOKEN=...` in `.env` (gitignored).
- Fallback: if only `ANTHROPIC_API_KEY` is set, the scripts use the API instead (billed per token, Haiku + Batches API).
