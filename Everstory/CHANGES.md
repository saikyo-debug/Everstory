# What changed in this version

## What I found in your original project

I ran your 34 existing tests (all passed, so the **backend was solid**) and then
clicked through every page in a real browser. The problems were in the front end
and in how the files connected:

| # | Problem found | Fixed by |
|---|---|---|
| 1 | `new_entry.html` contained **two full HTML pages** glued together, so the form showed twice | One `entry_form.html` (used for writing *and* editing) |
| 2 | `settings.html` loaded `js/settings.js`, which **did not exist** (404): the password Show buttons were dead | Wrote `settings.js` |
| 3 | **Text size did nothing**: every `font-size` in every stylesheet was in `px` | Converted all of them to `rem` (Medium is pixel-identical to your design) |
| 4 | Dashboard and Calendar never showed flash messages, so "Welcome back" appeared later on another page | One shared layout shows messages on every page |
| 5 | My Entries and New Entry had **no sidebar / mobile menu** | All signed-in pages share `base_app.html` |
| 6 | Dashboard stats were hard-coded: **Favorites 0, Days 0** | Real numbers |
| 7 | You could not view, edit or delete a single memory (the database functions existed, the routes did not) | Added the routes and pages |
| 8 | Dark mode: unreadable welcome card, odd logout button, bright patches | Fixed; verified on all 9 pages |
| 9 | Name was Alaala in some places, Everstory in others | One setting, `APP_NAME` in `config.py` |
| 10 | The theme was applied only after a download, causing a flash of the wrong colours | The server writes the theme into every page |
| 11 | An expired session on an API call returned a redirect page instead of an error | API calls now get a proper 401 |
| 12 | Login/register pages ignored your theme | Still light-only by design (they appear before you are known) |

## Your roadmap

| Roadmap item | Status |
|---|---|
| Core diary and authentication | ✅ Added view / edit / delete / favorite / search / pagination, "return to the page you wanted" after login |
| Calendar and navigation | ✅ Shared navigation everywhere, working mobile menu, calendar links to each memory |
| Appearance and personalization | ✅ Text size, font, season, dark mode, reduced motion now apply everywhere, with no flash |
| Security hardening | ✅ Strict CSP (no inline code at all), cookie flags, HSTS, upload checks, password limits, timing-safe login, safer redirects, per-account rate limits. See DEPLOYMENT.md §9 |
| Voice and media memories | ✅ Photos, voice notes (recorded in the browser) and videos |
| AI assistant | ✅ Runs on your server; not a cloud AI (see below) |
| Offline functionality and sync | ✅ Installable app, offline writing and reading, safe re-sync |
| Voice biometrics | ⚠️ Built and working, but **only tested on simulated voices** (see below) |
| Digital Time Capsule | ✅ |
| Production deployment | ⚠️ Files written and the app tested in production mode; Docker/gunicorn/nginx **not testable here** |

## Decisions I made for you (easy to change)

1. **App name = Everstory** everywhere (your roadmap and requirements say Everstory). One line in `config.py`. The database file is still `alaala.db`, and `ALAALA_*` settings still work.
2. **The assistant is local**, not connected to a cloud AI. Your diary text never leaves your server. It is smart search (with related words in English and Filipino), date phrases, stats and prompts, **not** a chatbot that understands anything. If you want a real LLM, that is a separate, opt-in addition because it would send diary text to a third party.
3. **Offline reading copy is ON by default** (matches the landing page promise "keep using your diary without internet"). It stores your memories in the browser, is wiped on logout, and can be turned off in Settings → Offline use. Memories you wrote offline that have not synced are kept even after logout so nothing is lost.
4. **Voice unlock protects opening Time Capsules** and is optional, with your password always available as a fallback. You can change what it protects.
5. **Time-capsule times use Philippine time**, like the rest of the diary.

## Honest limitations

* **Voice unlock** was measured on a speech *simulator*, not real people: about 3% wrong rejections and 2% wrong accepts there. It also cannot tell a live voice from a recording. My first version would have accepted almost anyone (96% wrong accepts); testing caught that and it was recalibrated. Please test it with real voices before relying on it (DEPLOYMENT.md §8).
* A sealed capsule is sealed by the *app*. Someone with access to the server's database could still read it. The diary is not encrypted at rest (as in the original).
* Tested in **Chromium only** (Chrome/Edge). Safari, Firefox and real phones are untested.
* Editing, deleting and attaching files need a connection. Only plain-text new memories work offline.
* There is **no "forgot password"** email flow yet.

## How it was tested

* **90 automated tests** (your 34 + 56 new): `python -m unittest discover -s tests`. They also pass with Python deprecation warnings treated as errors.
* **176 checks in a real browser** (login, writing, uploads, security, dark mode, text size, mobile, assistant, capsules, offline/sync, voice recorder).
* Your real database was copied and every page was rendered for all 4 existing users: **no errors, and the data is unchanged**. The upgrade only adds tables and columns.
* Found by testing and fixed: webm voice notes classified as video; a CSS rule that kept "hidden" buttons visible; the voice matcher's accept-almost-anyone calibration; assistant search ranking; a stat-card layout slip.

## Files

**Added:** `config.py` `auth_utils.py` `media.py` `assistant.py` `capsules.py` `voiceprint.py` `sync_api.py` `wsgi.py` `gunicorn.conf.py` `Dockerfile` `docker-compose.yml` `.env.example` `generate_secret_key.py` `scripts/` `README.md` `DEPLOYMENT.md` `CHANGES.md`, templates (`base_app` `base_public` `_macros` `entry_form` `entry_detail` `assistant` `capsules` `capsule_form` `capsule_view` `unlock` `offline` `error` `sw.js`), static JS/CSS/icons, `tests/test_features.py`.

**Rewritten:** `app.py`, `database.py` (every original function kept), `theme.js` and all templates.

**Removed:** `templates/new_entry.html` (replaced by `entry_form.html`), `static/css/entries.css` and `new_entry.css` (their rules now live in `app.css`).

**Kept as-is:** your `alaala.db` (not included in the zip, so extracting never overwrites your data), `requirements.txt`, `tests/test_app.py` (one small addition so it runs without setting a key by hand).
