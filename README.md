# Dotdigital-Lite SUT

This repository contains the web app and its deployment configuration. The app is currently running as a web service on a Hostinger VPS, with Caddy providing HTTPS and Docker Compose managing the app and proxy.

## Current state

- The dashboard shows sales KPIs based on imported order data.
- Contacts can be searched and filtered by consent status. Signed-in users can add a contact in a dialog or delete an individual contact from the list.
- The AI assistant generates marketing copy using OpenAI when configured, and provides a local preview response otherwise.
- Contact and order data is stored in SQLite and persisted on the VPS.
- Pushes to `main` that change `sut_app/` or the deployment workflow trigger a GitHub Actions deployment. The independent Playwright suite is maintained beside this repository at `../playwright`.

The dashboard, contacts, and assistant require app sign-in. Browser write requests use CSRF protection; `/api/*` routes require a separately configured API key. Keep deployment credentials and customer data private.

This is a demo/staging service, not a production system for real customer data. Do not enable the test-only `simulate_bug` or `hallucinate` options on a production service.
