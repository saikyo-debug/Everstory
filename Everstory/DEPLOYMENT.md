# Deploying Everstory

This guide takes you from "it runs on my computer" to a real website.
Read **"What was and wasn't tested"** first so there are no surprises.

---

## 0. What was and wasn't tested

| Area | Status |
|---|---|
| App code, database upgrade, all features (90 automated tests + 5 browser test suites) | ✅ Tested in Chromium (Chrome/Edge engine) |
| Production settings (Secure cookies, HSTS, proxy IP handling, refusing to start without a key) | ✅ Tested |
| `wsgi.py`, `gunicorn.conf.py`, backup and cleanup scripts | ✅ Imported / run successfully |
| **Docker build, gunicorn, waitress, nginx, systemd, a real HTTPS site** | ⚠️ **Not tested** (not available where this was built). The files follow each tool's standard usage, but expect to fix a small thing or two on your first deploy. |
| Safari, Firefox, real phones and tablets | ⚠️ Not tested |
| Voice unlock with real human voices | ⚠️ Only tested on simulated voices. See section 8. |

---

## 1. Before you deploy (checklist)

1. **Choose where the data lives.** Your diary is one SQLite file (`alaala.db`)
   plus an `uploads/` folder. Both must be on a **persistent disk**. On hosts
   with an "ephemeral" filesystem (many free tiers) they are erased on every
   restart. Check your host offers a persistent disk/volume.
2. **You need HTTPS.** Production mode turns on Secure cookies and HSTS, and
   browsers only allow the microphone, the offline mode and installing the app
   on HTTPS pages (plain `http://` works only on `localhost`).
3. **Create a secret key** and keep it private: `python generate_secret_key.py`
4. **Back up your current `alaala.db`** (just copy the file somewhere safe).
   The app upgrades it automatically on first start and never deletes data,
   but a backup costs nothing.
5. **There is no "forgot password" email feature yet.** If a user forgets their
   password, you would have to reset it by hand. See "Recommended next steps".

### Settings (environment variables)

| Variable | Meaning |
|---|---|
| `EVERSTORY_SECRET_KEY` | **Required.** 32+ random characters. |
| `EVERSTORY_ENV` | `production` on the live site (HTTPS). Leave unset/`development` on your own computer. |
| `EVERSTORY_DATA_DIR` | Folder for `alaala.db` and `uploads/`. Point it at the persistent disk. |
| `EVERSTORY_TRUST_PROXY` | `1` only when behind nginx / a hosting platform router. |
| `PORT`, `GUNICORN_BIND`, `WEB_CONCURRENCY`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT`, `FORWARDED_ALLOW_IPS` | Gunicorn tuning (optional). |

You can put them in a `.env` file (copy `.env.example`). Real environment
variables always win over the file. The old `ALAALA_*` names still work.

---

## 2. Running on your own computer (Windows)

```bat
cd Alaala
.venv\Scripts\activate
pip install -r requirements.txt
python generate_secret_key.py           :: copy the printed line into a file named .env
python app.py                           :: open http://127.0.0.1:5000
```

Run the tests any time:

```bat
python -m unittest discover -s tests
```

## 3. A Windows server (waitress)

```bat
pip install -r requirements-prod.txt
set EVERSTORY_ENV=production
set EVERSTORY_TRUST_PROXY=1
waitress-serve --listen=127.0.0.1:8000 wsgi:app
```

Put a reverse proxy that does HTTPS in front of it (IIS with URL Rewrite,
nginx for Windows, or a Cloudflare Tunnel). Only set `EVERSTORY_TRUST_PROXY=1`
when that proxy is the only way to reach the app.

## 4. A Linux server (gunicorn + nginx + HTTPS)

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin everstory
sudo mkdir -p /opt/everstory /var/lib/everstory
# copy the project into /opt/everstory (without .venv, *.db, uploads)
# copy your existing alaala.db to /var/lib/everstory/ if you have one
cd /opt/everstory
python3 -m venv .venv && .venv/bin/pip install -r requirements-prod.txt
cp .env.example .env && nano .env      # set the key, EVERSTORY_ENV=production,
                                       # EVERSTORY_DATA_DIR=/var/lib/everstory,
                                       # EVERSTORY_TRUST_PROXY=1,
                                       # GUNICORN_BIND=127.0.0.1:8000
sudo chown -R everstory:everstory /opt/everstory /var/lib/everstory
sudo chmod 600 /opt/everstory/.env
```

`/etc/systemd/system/everstory.service`:

```ini
[Unit]
Description=Everstory
After=network.target

[Service]
User=everstory
WorkingDirectory=/opt/everstory
EnvironmentFile=/opt/everstory/.env
ExecStart=/opt/everstory/.venv/bin/gunicorn wsgi:app -c gunicorn.conf.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now everstory
curl http://127.0.0.1:8000/healthz        # {"status":"ok", ...}
```

`/etc/nginx/sites-available/everstory` (then link it, and run
`sudo certbot --nginx -d diary.example.com` to add free HTTPS):

```nginx
server {
    listen 80;
    server_name diary.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name diary.example.com;
    # certbot fills in the ssl_certificate lines

    client_max_body_size 80m;        # photo/voice/video uploads

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

## 5. Docker

```bash
cp .env.example .env        # fill it in
docker compose up -d --build
docker compose logs -f
```

The database and uploads live in the `everstory-data` volume. **Never delete
that volume** unless you mean to erase the diary. Production mode needs HTTPS
in front (a reverse proxy such as Caddy or nginx). To try the container on your
own computer over plain `http://localhost:8000`, set `EVERSTORY_ENV=development`
in `.env`.

## 6. Hosting platforms (Render, Railway, Fly.io ...)

* Start command: `gunicorn wsgi:app -c gunicorn.conf.py`
* Add a **persistent disk/volume** and set `EVERSTORY_DATA_DIR` to its path.
  Without one, the diary is lost on every deploy.
* Set `EVERSTORY_SECRET_KEY`, `EVERSTORY_ENV=production`, `EVERSTORY_TRUST_PROXY=1`
  and, because the platform's proxy is not on 127.0.0.1, `FORWARDED_ALLOW_IPS=*`.
* Run **one instance only**. SQLite is a single-file database and does not
  support several servers writing to it from different machines.

---

## 7. Backups, updates and maintenance

**Back up** (database + uploads, safe while the app runs):

```bash
python scripts/backup_db.py --dest /path/on/another/disk --keep 30
```

Schedule it daily (Windows Task Scheduler / `cron`), and copy the result off the
server. **A backup that lives only on the same disk is not a backup.**

**Restore:** stop the app → copy a backup `alaala-….db` over `alaala.db` → unpack
the matching `uploads-….tar.gz` into the data folder → start the app.

**Update the app:** back up → copy the new code over the old (keep `.env`,
`alaala.db`, `uploads/`) → `pip install -r requirements-prod.txt` →
restart the service. Database upgrades run automatically and only ever add things.

**Clean up** files no memory points to (list first, then delete):

```bash
python scripts/cleanup_orphan_uploads.py
python scripts/cleanup_orphan_uploads.py --delete
```

**Health check** for uptime monitors: `GET /healthz` → `200 {"status":"ok"}`.

---

## 8. Voice unlock: read this before turning it on for others

Voice unlock is an **optional extra check** (for opening Time Capsules). It is
**not** a replacement for the password, and the password always works as a fallback,
so nobody can be locked out.

What was measured: on **simulated** voices, with 5 enrollment recordings, it
rejected about 3% of genuine attempts and accepted about 2% of other voices
(2 deliberately similar voices were rejected in the live browser test). Those
numbers come from a speech simulator, **not real people**. Real voices vary more
(colds, tiredness, microphones, background noise), so expect more rejections and
possibly more wrong accepts. It also cannot tell a live voice from a recording.

To check it with real people: enroll yourself, then try 10 times at different
moments; ask a family member to try 10 times. Adjust in `voiceprint.py`:

* too many rejections of the right person → raise `THRESHOLD_FLOOR` / `THRESHOLD_CEILING`
* wrong people getting in → lower them

Privacy: the browser analyses the audio itself; only 28 numbers per recording
leave the device, and only an averaged template is stored (never audio). Voice
data is generally treated as sensitive biometric data, and privacy laws (for
example the Philippine Data Privacy Act) may apply to it if other people use your
site. Check what applies to you, tell users clearly what is stored, and let them
delete it (Settings → Voice unlock → Turn off).

---

## 9. Security checklist for going live

- [ ] HTTPS works and `EVERSTORY_ENV=production` is set
- [ ] `EVERSTORY_SECRET_KEY` is long, random, private, and not in your code or Git
- [ ] `.env`, `alaala.db` and `uploads/` are not in a public folder or repository
- [ ] `EVERSTORY_TRUST_PROXY=1` only if a proxy really sits in front
- [ ] Daily backups run **and you have tested a restore once**
- [ ] The server's operating system and Python packages are kept up to date
- [ ] You decided how users will recover a forgotten password

What the app already does for you: CSRF protection on every form and API call;
strict Content-Security-Policy (no inline scripts, no `eval`); login rate limiting
with real-IP detection behind proxies; session timeout after 30 minutes idle and
logout-everywhere when a password changes; uploads checked by their real file
content, stored under random names outside the public folder, and served only to
their owner; no private page or API response is stored by the offline cache.

What it does **not** do: encrypt the diary text inside the database (the original
design stores it as plain text, so protect the server and its backups), send
emails, or offer two-factor login.

---

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `RuntimeError: A secure Flask secret key is missing…` | Set `EVERSTORY_SECRET_KEY` (32+ characters). |
| Can't log in on the live site; no error | Production mode needs HTTPS (Secure cookie). Check the site really uses `https://`. |
| Everyone shares one rate-limit / "Too many attempts" for all users | `EVERSTORY_TRUST_PROXY=1` missing behind a proxy, or `FORWARDED_ALLOW_IPS` wrong. |
| Upload fails with 413 | Raise `client_max_body_size` in nginx (this guide uses 80m). |
| Microphone button missing | The page must be HTTPS (or `localhost`) and the browser must support recording. |
| Data disappears after redeploy | No persistent disk; set `EVERSTORY_DATA_DIR` to one. |
| `database is locked` | Too many workers/instances. Use one instance and the default 2 workers. |

---

## Recommended next steps

1. **Password reset by email** (needs an email provider).
2. **Test on real phones** (Safari on iPhone records audio differently from Chrome).
3. **Encrypt diary text at rest** if the server is shared or backups are stored off-site.
4. **Exports:** let users download all their memories (PDF/zip) for portability.
5. Move from SQLite to PostgreSQL only if you ever need more than one server.
