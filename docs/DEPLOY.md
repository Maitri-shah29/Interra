# Deploying the judge pages and workers

The judge page tails the trace file that the three LiveKit workers (voice,
camera and home) write, so all four processes run in **one container**. `python -m agent.serve` starts them,
restarts any that exit, and applies settings saved from the page. Serverless
hosts such as Vercel cannot run this: the workers hold a long-lived websocket to
LiveKit Cloud and keep VAD models in memory.

`Dockerfile.deploy` builds the hosted image. `Dockerfile` remains the
submission image and still starts only the voice worker.

**AWS EC2:** see [DEPLOY_AWS.md](DEPLOY_AWS.md), which covers one instance with
automatic HTTPS, set up from the AWS CLI. Railway (below) works too, if you can open an account there.

## Railway

1. **New project → Deploy from GitHub repo**, pick this repository and branch.
   `railway.json` selects `Dockerfile.deploy` and the `/api/health` health check.
2. **Variables** (service → Variables):

   | Name | Value |
   |---|---|
   | `LIVEKIT_URL` | `wss://<project>.livekit.cloud` |
   | `LIVEKIT_API_KEY` | LiveKit key |
   | `LIVEKIT_API_SECRET` | LiveKit secret |
   | `INTERRA_ADMIN_PASSWORD` | A long password, at least 8 characters. Turns on `/settings` |
   | `INTERRA_SITE_PASSWORD` | Optional. Puts every page behind a browser password, so strangers cannot mint tokens and spend Inference credit |
   | `RAILWAY_RUN_UID` | `0` — needed with a volume, because Railway mounts volumes as root |

3. **Volume** (service → Settings → Volumes): mount at `/app/artifacts`. It
   keeps the session traces and the saved settings across redeploys.
4. **Networking → Generate Domain.** Railway sets `PORT`, and the page listens on it.
   The browser needs HTTPS for the microphone and camera, and the Railway domain provides it.
5. Open `https://<domain>/settings` and sign in with the admin password.

Size: the page plus the three workers use about 2–2.5 GB of memory. No GPU is
needed, because speech-to-text, the LLM and text-to-speech run on LiveKit Inference.
Run **one replica**: the trace and the settings file are per container.

## Settings page

`/settings` lists every variable the code reads, shows each one's source
(deployment, dashboard, or code default), and lets you change any of them or
add new ones. Saving writes `artifacts/settings/overrides.json`, and the
supervisor then restarts the page and the workers with the new values. Live
sessions end, and the voice worker can take up to 10 s to stop.

- Dashboard values override Railway variables. Delete one (trash icon) to fall
  back to the Railway value.
- Secret values (names containing KEY, SECRET, TOKEN or PASSWORD) are never sent
  to the browser. At most their last four characters are shown.
- The page cannot change `INTERRA_ADMIN_PASSWORD`, `INTERRA_SITE_PASSWORD`,
  `PATH`, `PORT`, or names starting with `PYTHON`, `LD_` or `RAILWAY_`. Set those in Railway.
- Sign-in lasts 12 hours, survives restarts, and ends when the admin password
  changes. After 10 wrong passwords in 15 minutes, sign-in is refused for everyone until the window passes.
- Values are stored as plain text on the volume.

If a worker log says `worker is at full capacity`, raise
`INTERRA_AGENT_LOAD_THRESHOLD` (LiveKit's default is 0.7) from the page.

## Local run

```bash
INTERRA_ADMIN_PASSWORD='change me please' .venv/bin/python -m agent.serve --port 8780
```

The workers also read `.env`. With real LiveKit keys there, they register with that project.
