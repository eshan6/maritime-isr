# Putting Maritime ISR on the internet, for free

Two free services, one job each. **No credit card, no subscription, on either.**

| Piece | Where | Why there |
|---|---|---|
| The screen (React map) | **Vercel** | Free, fast worldwide, no server to run |
| The brain (FastAPI + data) | **Render** | Free tier is 512 MB with no card; the API peaks at 228 MB |

Vercel quietly forwards anything starting with `/api` to Render, so the browser
only ever talks to one address.

---

## Why not the obvious places

**Hugging Face Spaces** moved the Docker SDK behind a paid plan. Only Static
Spaces remain free, and a static host cannot run FastAPI. The Dockerfile in
`deploy/huggingface/` still works and is kept for anyone who has PRO, but it is
no longer the free path.

**Vercel's own Python functions** cap a deployment at 250 MB unzipped. This API
pulls pyarrow (156 MB), pandas (76 MB) and numpy (45 MB) at import — 294 MB of
dependencies before a single byte of corpus. It does not fit.

**Render's free tier** is 512 MB of RAM, no card, 750 instance-hours a month.
Measured peak here after serving the heaviest endpoints: **228 MB**. It fits.

---

## What this deploys, and what it does not

A **frozen photograph** of the data from your laptop. Nothing on the internet
regenerates it. It does **not** deploy the pipeline: SAR processing and live AIS
need far more memory and a disk that persists.

---

## Step 1 — build and publish the corpus

`data/` is gitignored, so the corpus is not in the repo and Render cannot get it
from GitHub. Publish it as a Release asset instead — free, and no git-lfs.

Generate the corpus first, **in this order**. The order matters and is not
obvious: `graph-populate` reads what the earlier steps landed, so it goes last.
Run it early and everything still exits 0, but the graph never sees the vessel
encounters and the Watch screen comes out empty with nothing explaining why.

```
cd maritime-isr-live
maritime-isr scenario generate          # the world
maritime-isr build-tracks               # positions -> tracks and encounters
maritime-isr radar correlate --write    # slow, ~35 min
maritime-isr baselines derive
maritime-isr graph-populate             # LAST
```

*Success:* `maritime-isr alerts` prints at least one alert.

Then pack it:

```
bash deploy/pack_corpus.sh
```

It prints a file and the exact steps to attach it to a GitHub Release. Follow
them and copy the **asset** link (right-click the uploaded file), not the
release page link.

---

## Step 2 — deploy the API to Render

1. Sign in at <https://render.com> with GitHub. No card is requested.
2. **New → Web Service**, pick the `maritime-isr` repo.
3. Render reads `deploy/render/render.yaml`. If it asks manually:
   - Runtime **Python 3**
   - Build: `pip install -e ".[api]" && python deploy/render/fetch_corpus.py`
   - Start: `uvicorn maritime_isr.api.app:app --host 0.0.0.0 --port $PORT`
4. Add the environment variable **`MISR_CORPUS_URL`** = the Release asset link
   from step 1. **The build fails on purpose without it** — a service that came
   up green with no corpus would look like a broken product rather than a
   missing file.
5. Deploy.

*Success:* the build log prints `downloaded NNN MB, extracting` then `corpus
ready`, the service goes **Live**, and `https://your-service.onrender.com/api/health`
returns `{"status":"ok"}`.

*Failure:* `MISR_CORPUS_URL is not set` — step 4 was skipped. A 404 while
downloading — the release is still a draft, or you copied the page link instead
of the asset link.

---

## Step 3 — keep it awake (do not skip this)

**A free Render service spins down after 15 minutes of no traffic, and takes
about a minute to wake.** For a demo opened in front of people that minute is
the whole ballgame.

The fix is free. Sign up at <https://cron-job.org> (no card) and create a job:

- URL: `https://your-service.onrender.com/api/health`
- Every **10 minutes**

The free allowance is 750 instance-hours a month, and a calendar month is about
730 hours, so keeping one service warm around the clock costs nothing.

**Even so, open the link yourself ten minutes before any demo.** A pinger can
fail quietly; your own eyes cannot.

---

## Step 4 — point the frontend at it

Open `frontend/vercel.json`, replace `REPLACE-ME` with your Render address:

```
"destination": "https://your-service.onrender.com/api/:path*"
```

Commit and push.

---

## Step 5 — deploy the screen to Vercel

<https://vercel.com/new>, sign in with GitHub, import `maritime-isr`.

- **Root Directory:** click Edit, set to `frontend`
- Leave the rest; `vercel.json` sets the build

*Success:* a `.vercel.app` address showing the map with vessels on it.

*Failure — blank page:* the browser cannot reach the API. Open the Render
address directly and check it is Live.

*Failure — map but no ships:* `vercel.json` still says `REPLACE-ME`, or the
address has a typo.

---

## Honest limits of the free tier

**0.1 CPU.** Locally the heaviest screen answers in 0.17 s. On a tenth of a core
it will be slower — measure it once it is up rather than quoting the local
number to anyone.

**Public means public.** Anyone with the link reads everything. Acceptable only
because every vessel, position and document is synthetic. Never point this at a
real feed without building real authentication first.

---

## When your data changes

Rerun the pipeline (step 1 order), then:

```
bash deploy/pack_corpus.sh
```

Upload as a **new** Release, update `MISR_CORPUS_URL` on Render, redeploy.
Vercel redeploys itself on every push; Render only changes when you tell it to.
