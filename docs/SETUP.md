# Setup and operations

## What runs

| Service | Image | Purpose |
|---|---|---|
| `db` | `postgres:16.4-alpine` | PostgreSQL 16. Data in the `pgdata` volume |
| `web` | `larder-api:1.0.0` (built from `backend/`) | JSON API. Runs migrations on start, then uvicorn |
| `worker` | same image, `worker` command | Imports, OCR, receipt parsing, nightly backups, housekeeping. Memory and PID limits |
| `caddy` | `larder-web:1.0.0` (Caddy 2.8.4 plus the built PWA) | HTTPS, security headers, serves the app and proxies `/api` |

Volumes: `pgdata` (database), `larder_data` (recipe/product photos and receipt originals), `larder_backups`, and
`caddy_data`/`caddy_config` (certificates). Nothing leaves the machine except calls to the AI provider (if you set a
key) and Open Food Facts barcode lookups (if enabled).

## Install on a fresh machine

Requirements: Docker Engine 24+ with the Compose plugin; ports 80 and 443 free (or choose others); about 2 GB of disk.

1. Get the code and create your settings:

   ```bash
   git clone <this repository> larder
   ```

   ```bash
   cd larder && cp .env.example .env
   ```

2. Edit `.env`:
   - `POSTGRES_PASSWORD`: a long random string (`openssl rand -base64 24`).
   - `LARDER_SITE_ADDRESS`: the name you'll type on your phone, e.g. `larder.home.arpa`. Point it at this machine in your
     router's DNS, or in each device's hosts settings.
   - `LARDER_TLS`: leave as `internal` for a home network (see below). For a public domain with ports 80/443
     forwarded, set it to your email address and Caddy will get a Let's Encrypt certificate.
   - Optional: `LARDER_ANTHROPIC_API_KEY` for AI help, and `LARDER_BACKUP_PASSPHRASE` to encrypt backups.

3. Start everything:

   ```bash
   docker compose up -d --build
   ```

   ```bash
   docker compose ps
   ```

   Wait until all four services show `healthy`.

4. Open `https://<LARDER_SITE_ADDRESS>` and create the household. You become the administrator. Add the second adult
   under **Settings → Adults who can sign in**, and the children and pets under **People** and **Pets**.

5. Optional: load the full UK CoFID nutrition dataset (about 2,900 foods; a 47-food subset is bundled):

   ```bash
   docker compose exec web python -m app.cli import-cofid https://assets.publishing.service.gov.uk/media/60538b91e90e07527df82ae4/McCance_Widdowsons_Composition_of_Foods_Integrated_Dataset_2021..xlsx
   ```

### HTTPS on phones and tablets (home network)

Camera scanning and installing the app both need HTTPS. With `LARDER_TLS=internal`, Caddy runs its own small
certificate authority. Each device needs to trust its root certificate once:

1. Export the root certificate:

   ```bash
   docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./larder-root.crt
   ```

2. Install it:
   - **iPhone/iPad**: AirDrop or e-mail `larder-root.crt` to the device and open it. Then go to **Settings → General →
     VPN & Device Management** and install the profile. Finally, under **Settings → General → About → Certificate
     Trust Settings**, turn on full trust for "Caddy Local Authority".
   - **Android**: **Settings → Security → Encryption & credentials → Install a certificate → CA certificate**.
   - **Desktop browsers**: import it into the system or browser certificate store as a trusted root.
3. Open the site and add it to the home screen: **Share → Add to Home Screen** in Safari, or **Install app** in Chrome.

The root certificate stays valid for years. Keep the `caddy_data` volume so it doesn't change; if it is recreated,
reinstall the new root on each device.

**Using different host ports.** If 80/443 are taken, set `LARDER_HTTP_PORT`/`LARDER_HTTPS_PORT` and include the port
in the address, e.g. `LARDER_SITE_ADDRESS=larder.home.arpa:8443` with `LARDER_HTTPS_PORT=8443`.

**Remote access.** Prefer a VPN (WireGuard or Tailscale) to the home network over exposing the site to the internet. If
you do expose it, use a real domain with `LARDER_TLS=<your email>` and strong passwords. Login and import endpoints
are rate-limited.

### TLS-inspecting proxies

If image builds fail with certificate errors because your network inspects TLS, put the proxy's CA certificate in
`certs/` as a `.crt` file. Both images trust it during the build and at runtime. Leave `certs/` empty otherwise.

## Configuration reference

All settings live in `.env` and use the `LARDER_` prefix (see `backend/app/config.py`). The main ones:

| Setting | Default | Notes |
|---|---|---|
| `LARDER_ANTHROPIC_API_KEY` | empty | Enables AI help. Kept server-side and never logged or sent to the browser |
| `LARDER_AI_MODEL` / `LARDER_AI_EFFORT` | `claude-opus-5-5` / `medium` | Model and effort for AI requests |
| `LARDER_OFF_ENABLED` | `true` | Open Food Facts barcode lookups, cached for 30 days |
| `LARDER_BACKUP_HOUR_UTC` / `LARDER_BACKUP_KEEP` | `3` / `14` | Nightly backup time and how many to keep |
| `LARDER_BACKUP_PASSPHRASE` | empty | Encrypts backups with AES-256-GCM (scrypt key). Without it, encrypted backups can't be restored |
| `LARDER_MAX_UPLOAD_MB` | `15` | Per-file upload limit (Caddy also caps requests at 20 MB) |
| `LARDER_TIMEZONE` | `Europe/London` | Household timezone for dates and intake days |

## Upgrading

```bash
git pull
```

```bash
docker compose up -d --build
```

Database migrations run automatically when `web` starts (`alembic upgrade head`) and are transactional. Take a backup
first (below) so you can roll back by restoring it.

## Backups

- **Automatic**: the worker writes a backup every night at `LARDER_BACKUP_HOUR_UTC` and keeps the newest
  `LARDER_BACKUP_KEEP`. Each archive contains a `pg_dump` of the database, all photos and receipt originals, and a
  manifest with the schema revision. It is encrypted if `LARDER_BACKUP_PASSPHRASE` is set.
- **On demand**: **Settings → Server → Back up now**, or:

  ```bash
  docker compose exec worker python -m app.cli backup
  ```

- **Copy backups off the machine** regularly:

  ```bash
  docker compose cp worker:/data/backups ./larder-backups
  ```

  Alternatively, write backups straight to a host folder: create it, run `sudo chown 10001 <folder>`, and set
  `LARDER_BACKUP_DIR=<folder>` in `.env`.
- **Full household export** (portable JSON + CSV + media zip): **Settings → Server → Export everything**, or
  `GET /api/v1/export`. Passwords, sessions and tokens are never included.

## Restoring a backup

This replaces all current data with the backup's. It works on the same machine or a brand-new one.

1. On a new machine, follow the install steps up to `docker compose up -d` (don't create a household).
2. Stop the app containers, keeping the database running:

   ```bash
   docker compose stop web worker
   ```

3. Restore by streaming the archive in. Add `--passphrase '<passphrase>'` if `.env` on this machine doesn't already
   contain the one the backup was made with.

   ```bash
   docker compose run --rm -T worker cli restore - --yes < ./larder-backups/larder-20261105T034026Z.enc.tar.gz
   ```

4. Start the app again and sign in with your usual password:

   ```bash
   docker compose start web worker
   ```

5. Check stock consistency (prints `0 mismatched lots`):

   ```bash
   docker compose exec web python -m app.cli check
   ```

This exact procedure was tested end to end, destroying every volume in between; see
[TESTING.md](TESTING.md#acceptance-test-7).

**Restoring from a household export** (instead of a backup) into a fresh install:

```bash
docker compose exec -T web python -m app.cli import /path/in/container/export.zip --admin-email you@example.com
```

Other users then need their passwords reset by the administrator:

```bash
docker compose exec web python -m app.cli reset-password <email>
```

## Admin and troubleshooting

- **Settings → Server** shows whether AI is on, AI calls and estimated cost over 30 days, the backup state, and failed
  background jobs with a **Retry** button.
- Logs: `docker compose logs -f web worker`. Logs record job progress and errors but never receipt contents, prompts or
  keys.
- Health: `https://<site>/api/v1/health`. The worker's health is a heartbeat file updated every loop.
- If the AI provider or Open Food Facts is down, manual entry, existing data, OCR and structured URL import keep
  working, and the UI explains what's unavailable.
- Deleting personal data: delete a person (removes their intake history), delete receipt originals from the receipt
  screen, revoke tokens in **Settings → Connected apps**.
