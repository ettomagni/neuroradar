# NeuroRadar

Globo interattivo delle startup neurotech (386 aziende dalla Start-up Map di Neurofounders), con posizioni aperte aggiornate ogni mattina.

Sito statico (HTML + JS + JSON, nessuna build): `index.html`, `app.js`, `styles.css`, `data/`.

## Avviarlo in locale

```bash
python3 -m http.server 5173
```

Poi apri http://localhost:5173 (serve un server: da `file://` il JSON non si carica).

## Dati e script (`scripts/`, solo libreria standard di Python)

| Script | Cosa fa | Quando |
|---|---|---|
| `build_seed.py` | crea `data/companies.json` dalla lista aziende (file locale, non nel repo) (**sovrascrive l'arricchimento**) | solo all'inizio |
| `enrich_profiles.py` | dal profilo Neurofounders: sito, anno, modalità, form factor, invasività, indicazione, utenti, regolatorio, funding, analisi | una volta (cache in `data/cache/`) |
| `find_careers.py` | dal sito trova pagina carriere e ATS (Greenhouse, Lever, Ashby, Workable, Personio, Recruitee, SmartRecruiters, BambooHR, Breezy) | il lunedì, riprova i casi non risolti |
| `probe_ats.py` | per chi non ha un ATS visibile: prova direttamente le API di Greenhouse/Ashby/Lever; accetta solo se nome completo o dominio compaiono negli annunci e si parla di neurotech. Gli omonimi scartati a mano vanno in `ats_rejected` | il lunedì, dopo `find_careers.py` |
| `fetch_jobs.py` | scarica le posizioni dagli ATS, confronta con ieri, segna nuove/chiuse | ogni mattina |

File prodotti: `data/jobs.json` (posizioni aperte), `data/job_texts.json` (testi completi degli annunci, per il match col CV: rigenerato a ogni run e **non** pubblicato nel repo), `data/jobs_closed.json` (chiuse, 60 giorni), `data/runs.json` (statistiche ed errori di ogni run).

La prima esecuzione di `fetch_jobs.py` è una **baseline**: le posizioni trovate non vengono segnate come "nuove". Da quella dopo, "nuova" = vista per la prima volta nelle ultime 72 ore.

## Aggiornamento automatico

`.github/workflows/daily.yml` gira ogni giorno alle 05:00 UTC su GitHub Actions e fa il commit dei JSON aggiornati. Avvio manuale: tab *Actions* → *NeuroRadar daily update* → *Run workflow*.

## Chiave API di Claude (dalla fase 3)

- In locale: copia `.env.example` in `.env` e incolla la chiave (`.env` è in `.gitignore`).
- Su GitHub: *Settings → Secrets and variables → Actions → New repository secret*, nome `ANTHROPIC_API_KEY`.
