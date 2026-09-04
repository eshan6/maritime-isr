# Deploy Maritime ISR — two buttons and one upload

Free on both halves. **No credit card, no subscription.**

| | |
|---|---|
| [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/eshan6/maritime-isr) | The API and the data |
| [![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/eshan6/maritime-isr&root-directory=frontend&project-name=maritime-isr&repository-name=maritime-isr) | The map |

The buttons carry every build setting already — runtime, build command, start
command, health check, region, instance type, root directory. There is nothing
to type into a dashboard.

---

## Before you click: publish the corpus

This is the one step no button can do, because `data/` is gitignored — the
corpus lives on whichever machine generated it, and at 137 MB it is over
GitHub's 100 MB per-file limit for ordinary git.

Build it (about 55 minutes, and the order matters — `graph-populate` reads what
the earlier steps land, so it goes **last**):

```
maritime-isr scenario generate
maritime-isr build-tracks
maritime-isr radar correlate --write
maritime-isr baselines derive
maritime-isr graph-populate
```

*Success:* `maritime-isr alerts` prints at least one alert.

Pack it:

```
python deploy/pack_corpus.py
```

Then attach the file it produces to a new release at
<https://github.com/eshan6/maritime-isr/releases/new>, publish, and
**right-click the uploaded file → Copy Link Address.** The link must contain
`/download/`. A `/blob/` link is the page, not the file, and shows up later as a
puzzling 404 in the Render build log.

---

## Step 1 — Render

Click the button. Sign in with GitHub. Render reads `render.yaml` and fills in
everything.

It will ask for one value:

| Variable | Value |
|---|---|
| `MISR_CORPUS_URL` | the release link from above |

Then Apply.

*Success:* the build log prints `downloaded 137 MB, extracting` then `corpus
ready`, the service goes **Live**, and
`https://maritime-isr-api.onrender.com/api/health` returns `{"status":"ok"}`.

*Failure — `MISR_CORPUS_URL is not set`:* the variable was skipped. The build
fails on purpose rather than starting empty, because a service that comes up
green serving nothing looks like a broken product rather than a missing file.

*Failure — 404 while downloading:* the release is still a draft, or the link is
a `/blob/` one.

> **Keep the service name `maritime-isr-api`.** `frontend/vercel.json` already
> points at `maritime-isr-api.onrender.com`. If Render gives you a different
> name because that one is taken, change the `destination` in that file to match
> before deploying the frontend.

---

## Step 2 — Stop it sleeping

A free Render service spins down after **15 minutes** idle and takes about a
minute to wake. In front of an audience that minute is the whole demo.

Free fix, no card: <https://cron-job.org> → create a job hitting
`https://maritime-isr-api.onrender.com/api/health` every **10 minutes**.

The free allowance is 750 instance-hours a month and a calendar month is about
730, so staying warm around the clock costs nothing.

**Still open the link yourself ten minutes before any demo.** A pinger can fail
quietly; your own eyes cannot.

---

## Step 3 — Vercel

Click the button. Sign in with GitHub. Root directory is pre-set to `frontend`
and `vercel.json` supplies the build settings and the API proxy.

*Success:* a `.vercel.app` address showing the map with vessels on it.

*Failure — blank page:* the browser cannot reach the API. Open the Render
address directly and check it is Live.

*Failure — map but no ships:* the Render service has a different name than
`maritime-isr-api`; see the note in Step 1.

---

## What you are deploying, stated plainly

A **frozen snapshot** of synthetic data. Nothing on the internet regenerates it;
when the corpus changes you repeat the upload and redeploy.

It is **not the pipeline**. SAR processing and live AIS ingestion need far more
memory and a disk that persists, and neither runs on a free tier.

**Everything on it is invented by this repository.** No real vessel, position or
document. If anyone asks whether it has detected a real dark vessel, the answer
is no — the code exists and is measured against a synthetic suite, which is a
different claim and the difference is the point.

**Public means public.** Anyone with the link reads everything. Acceptable only
because none of the data is real. Never point this at a real feed without
building real authentication first.

**Speed.** Locally the heaviest screen answers in 0.17 s. Render's free tier
gives a tenth of a CPU, so it will be slower — measure it once it is up rather
than quoting the local number to anyone.
