# Everstory

A personal diary for your memories: write entries, attach photos, voice notes
and videos, browse them on a calendar, search them with a built-in assistant,
seal messages in time capsules, and keep writing when the internet drops.

Built with Flask and SQLite. No outside services, no tracking.

## Run it on your computer

```bat
.venv\Scripts\activate
pip install -r requirements.txt
python generate_secret_key.py        :: copy the printed line into a new file called .env
python app.py                        :: then open http://127.0.0.1:5000
```

Run the tests: `python -m unittest discover -s tests` (90 tests).

Going live? Read **[DEPLOYMENT.md](DEPLOYMENT.md)**. What changed in this version
and why: **[CHANGES.md](CHANGES.md)**.

## What is in the app

| Area | What it does |
|---|---|
| Diary | Write, read, edit, delete, favorite memories. Search and filter. Pages of 10. |
| Calendar | Dots on days with memories; each links to the memory. |
| Photos, voice, video | Attach up to 10 files per memory; record a voice note right in the browser. Checked by real file content, never trusted by name. |
| Assistant | Searches your memories (English + Filipino related words), understands "last month", "on this day", gives stats, streaks, writing prompts and app help. Runs entirely on your server. |
| Time Capsules | Message + attachments sealed until a date. The text is not even loaded from the database until it opens. |
| Offline | Installable app. Write without internet; memories sync later (exactly once). Optional read-only offline copy. |
| Voice unlock | Optional extra check before opening capsules, with password always available. Experimental, see DEPLOYMENT.md §8. |
| Appearance | Light / dark / follow-the-device, seasonal colours, font style, text size (now really scales everything), reduced motion. |
| Security | CSRF everywhere, strict CSP, rate-limited login, owner-only files, 30-minute idle logout. |

## Where things are

```
app.py            pages and routes (diary, calendar, settings, login, PWA files)
config.py         app name, environment settings, limits (one place)
database.py       all database access and the automatic upgrade
media.py          photo/voice/video validation, storage and serving
assistant.py      the diary assistant
capsules.py       time capsules
voiceprint.py     voice unlock matching
sync_api.py       API used by offline sync
auth_utils.py     login helpers
templates/        pages (base_app.html is the shared signed-in layout)
static/css, js    styles and scripts (no inline code anywhere)
tests/            automated tests
scripts/          backup and cleanup tools
wsgi.py, gunicorn.conf.py, Dockerfile, docker-compose.yml   deployment
```

## Changing the app's name

The name shown everywhere comes from one line: `APP_NAME` in `config.py`.
(The database file is still called `alaala.db` and the old `ALAALA_*` settings
still work, so nothing of yours breaks.)
