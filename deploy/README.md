# Putting Maritime ISR on the internet

Two free services, one job each:

| Piece | Where | Why there |
|---|---|---|
| The screen (React map) | **Vercel** | Free, fast worldwide, no server to run |
| The brain (FastAPI + data) | **Hugging Face Spaces** | Free container with 16 GB RAM and a real filesystem, which DuckDB needs |

Vercel quietly forwards anything starting with `/api` to Hugging Face, so the
browser only ever talks to one address. You do not have to understand that part
for it to work, but it is why there is no separate "backend URL" to configure in
the app.

---

## What this deploys, and what it does not

It deploys a **frozen photograph** of the data on your laptop. Nothing on the
internet regenerates it. When you rerun the pipeline locally and want the
public demo to match, you push again — step 2 below, which is one command.

It does **not** deploy the pipeline. SAR image processing and live AIS capture
need far more memory and a disk that persists, and neither runs on a free tier.
Those still need the Oracle VM. This gets the *demo surface* online; it does not
make the system live.

---

## Before you start, once

You need three things installed. Run each line; if it prints a version you
already have it.

```
git --version
git lfs version
node --version
```

If `git lfs` says "not found", install it — the data files are too big for
plain git:

```
sudo apt install git-lfs      # on Linux
brew install git-lfs          # on a Mac
git lfs install
```

Then log in to Hugging Face:

```
pip install huggingface_hub
huggingface-cli login
```

It asks for a token. Get one at <https://huggingface.co/settings/tokens> —
create it with **Write** permission, not Read.

---

## Step 1 — make the Space

Go to <https://huggingface.co/new-space>.

- **Space name:** `maritime-isr-api`
- **License:** MIT
- **Select the SDK:** **Docker** → *Blank*. This matters. If you pick Gradio or
  Streamlit it will not work.
- **Hardware:** CPU basic (free)
- **Visibility:** Public

Do not add any files. Just create it and leave the page.

> **Public means public.** Anyone with the link can read everything it serves.
> That is acceptable here *only* because every vessel, position and document in
> the corpus is synthetic — invented by this repository. Never point this setup
> at a real feed without building real authentication first.

---

## Step 2 — send the code and data to it

From the project folder:

```
cd maritime-isr-live
bash deploy/push_to_space.sh YOUR-USERNAME/maritime-isr-api
```

Replace `YOUR-USERNAME` with your Hugging Face username.

The script checks everything is present before it uploads anything, so if it
stops immediately, read what it says — it will tell you exactly which command
to run first.

**Success:** it ends with a green block of text giving you a `.hf.space` web
address.
**Failure:** it stops with a line starting `ERROR:` naming the missing piece.
Nothing is uploaded when that happens.

The upload is slow — about 145 MB — so expect a few minutes.

---

## Step 3 — wait for it to build

Open your Space page and click the **Logs** tab. Hugging Face is building the
container.

**Success:** the top of the page says **Running**, and opening
`https://your-username-maritime-isr-api.hf.space/api/health` in a browser shows
`{"status":"ok"}`.

**Failure:** it says **Build failed**. Copy the last twenty lines of the Logs
tab and send them to Claude. The usual cause is one Python package that has no
ready-made build for this machine, and the log names it.

First build takes several minutes. Later ones are faster.

---

## Step 4 — point the frontend at it

Open `frontend/vercel.json`. Find `REPLACE-ME` and replace that whole web
address with your real Space address, keeping the rest of the line intact:

```
"destination": "https://your-username-maritime-isr-api.hf.space/api/:path*"
```

Save it, then commit:

```
git add frontend/vercel.json
git commit -m "Point the frontend at the deployed API"
git push
```

---

## Step 5 — deploy the screen to Vercel

Go to <https://vercel.com/new> and sign in with GitHub.

- Import the `maritime-isr` repository
- **Root Directory:** click Edit and set it to `frontend`
- Leave everything else alone — `vercel.json` already sets the build settings
- Click **Deploy**

**Success:** after a minute or two you get a `.vercel.app` address. Open it. You
should see the map with vessels on it.

**Failure — a blank white page:** the browser cannot reach the API. Open the
Space address from step 3 directly; if that is asleep or failed, fix it there.

**Failure — map loads but no ships:** `vercel.json` still says `REPLACE-ME`, or
the Space address in it has a typo.

---

## The one thing to know before showing it to someone

**A free Space goes to sleep after 48 hours with no visitors.** Waking it takes
**30 to 90 seconds**, and whoever opens the link first does that waiting while
everyone watches.

Once awake it is quick — the heaviest screen answers in about **0.17 seconds**.

So: **open the link yourself ten minutes before any demo.** That is the entire
mitigation. If you demo it at least once every couple of days it never sleeps at
all.

---

## When your data changes

Rerun the pipeline locally, rebuild the UI, and push again:

```
cd frontend && npm run build && cd ..
bash deploy/push_to_space.sh YOUR-USERNAME/maritime-isr-api
```

Vercel redeploys itself whenever you push to GitHub. The Space does not — it
only changes when you run that script.
