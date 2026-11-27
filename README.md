# Dotdigital-Lite SUT

This repository contains the FastAPI microsite. The independent Playwright suite lives beside this folder at `../playwright`.

## Run locally on Windows

From a PowerShell terminal:

```powershell
Set-Location "$HOME\Desktop\microsite"
.\run-local.ps1
```

The script installs Python 3.12 and app dependencies with `uv` when needed, sets local defaults, then starts the app. It listens only on `127.0.0.1`; press Ctrl+C in that terminal to stop it. The default local app login is `demo-admin` / `choose-a-long-local-password`. You can override these by setting `ASSISTANT_USERNAME`, `ASSISTANT_PASSWORD`, and `ASSISTANT_SESSION_SECRET` in PowerShell before running the script. Set `APP_PORT` before launching if port 8000 is already in use.

SQLite is written to `sut_app/data.sqlite3`; set `DATABASE_PATH` to change it. The dashboard, contacts, and AI assistant pages require the app login, which uses a signed, eight-hour browser session. Every `/api/*` route, including read-only routes, requires the `X-API-Key` header matching `APP_API_KEY`. An unset API key or an example `replace-with-...` value disables API access. The local launcher generates a random API key for that run and listens only on `127.0.0.1`; set `APP_API_KEY` before launching if a local API client needs to use the same key. Never reuse a local key on a public host. For non-local deployments, use unique credentials and a session secret of at least 32 characters.

To call OpenAI locally, set `$env:OPENAI_API_KEY` in the same terminal before running the script. If it is unset, the assistant returns a local preview response instead.

To run the Playwright suite, open a second terminal and follow `../playwright/README.md`. The suite can use the running server or start it automatically when localhost is unavailable and it can find this checkout.

The sidebar links to the sales dashboard, `/contacts`, and `/assistant`. The contacts page lists imported contacts, supports filtering by name/email or consent status, and lets signed-in users add one contact at a time. The assistant generates marketing copy from product, audience, and discount-code details using `OPENAI_API_KEY`; without an AI key it returns a local preview response. App-login credentials are never sent to the browser. The UI controls use accessible labels and roles, so they are easy to locate with Playwright's `getByRole` and `getByLabel` APIs.

## Hostinger deployment

Use a Hostinger **VPS** for this Docker/Uvicorn app. Shared Web Hosting is suitable only if your specific plan explicitly supports long-running ASGI applications or Docker; a Python/WSGI feature alone is not enough for FastAPI.

1. Create a GitHub repository for this microsite and push the contents of `Desktop/microsite` to its `main` branch.
2. Create a Hostinger VPS, point your domain's DNS A record to its public IP, and allow inbound SSH, HTTP (80), and HTTPS (443) in the VPS firewall.
3. Install Git, Docker Engine, and the Docker Compose plugin on the VPS. Clone the GitHub repository to a stable directory, for example `/opt/saas-quality-blueprint-sut`.
4. Copy `sut_app/.env.example` to `sut_app/.env`. Set `DOMAIN` to your domain, replace `APP_API_KEY` with a long random secret, and set a private app-login username, password, and session secret (at least 32 random characters). The example `replace-with-...` values are rejected for app login/API access until replaced. The Compose deployment always sets the session cookie's `Secure` flag for HTTPS. Add an `OPENAI_API_KEY` if the assistant should call OpenAI. Keep `.env` on the VPS only; it is excluded from Git and the Docker build context.
5. From `sut_app`, start the app and Caddy reverse proxy:

   ```bash
   cd /opt/saas-quality-blueprint-sut/sut_app
   docker compose up -d --build
   ```

   Caddy obtains and renews the HTTPS certificate. The SQLite database is persisted under `sut_app/data/`.

### Deploy on each GitHub push

The included `.github/workflows/deploy-hostinger.yml` SSHes into the VPS after a push to `main`, pulls the repository, and rebuilds/restarts the Compose services. Add these GitHub Actions repository secrets:

- `HOSTINGER_HOST`: VPS hostname or IP.
- `HOSTINGER_USER`: SSH user allowed to deploy.
- `HOSTINGER_SSH_KEY`: private SSH key for that account.
- `HOSTINGER_KNOWN_HOSTS`: verified SSH host-key line(s) for the VPS.
- `HOSTINGER_APP_DIR`: absolute checkout path, such as `/opt/saas-quality-blueprint-sut`.

The VPS checkout needs read access to the GitHub repository. For a private repository, install a repository-scoped read-only deploy key on the VPS. Protect the `main` branch and review changes before deploying.

The API and test-cleanup route are intended for a dedicated demo/staging system, not real customer data. Keep `APP_API_KEY` private and do not enable the deliberate `simulate_bug` or `hallucinate` test toggles for a production service.
