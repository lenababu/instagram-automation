# Instagram Reels Auto-Publisher (GitHub Actions, ₹0/month)

Publishes one Instagram Reel twice a day, at **7:00 AM and 7:00 PM IST**, from videos in your Google Drive.
Runs entirely inside **GitHub Actions**, so your laptop can be off for weeks.

```
Google Drive (Videos + Thumbnails)
        │  next unposted file (natural order, Drive file ID = identity)
        ▼
GitHub Actions runner (cron 30 13 * * * UTC = 19:00 IST)
   ├─ FFmpeg: probe, extract audio, 5 frames
   ├─ Groq Whisper (whisper-large-v3): transcript + English translation
   ├─ Groq Qwen 3.8 27B (vision): what is actually shown
   ├─ Groq GPT-OSS 120B: hook, caption, CTA, 5–8 hashtags  (prompts/caption_prompt.txt)
   ├─ Cloudinary (free): public HTTPS URLs for video + existing thumbnail
   ├─ Instagram Graph API: REELS container → wait FINISHED → media_publish → permalink
   ├─ Google Sheets "Instagram Posting History": status, media id, permalink, errors
   └─ Telegram (optional): success / failure / quota / token alerts
```

## Project layout

```
app/
  main.py                 entry point (--check, --dry-run)
  config.py               env-based settings + secret-redacting logger
  google_auth.py          OAuth refresh-token credentials (no browser in CI)
  drive_service.py        list/sort/match/download (read-only)
  sheets_service.py       posting database (14 columns, status machine)
  duplicate_checker.py    selection + idempotency + resume rules
  video_processor.py      ffmpeg: probe, audio, frames, shrink, cleanup
  audio_transcription.py  Groq Whisper, chunking for >25 MB audio
  visual_analysis.py      Groq vision on extracted frames
  caption_generator.py    Groq text model, structured JSON caption
  media_hosting.py        Cloudinary upload (idempotent by Drive file id), URL verification
  instagram_publisher.py  Graph API flow, token-expiry + rate-limit detection
  notifications.py        Telegram (optional)
  scheduler.py            the daily job, step by step, with sheet checkpoints
prompts/                  caption_prompt.txt, visual_analysis_prompt.txt (edit freely)
scripts/                  one-time helpers for Google refresh token and Meta token
tests/                    31 unit tests (no network needed)
.github/workflows/daily-instagram-post.yml
```

## How the daily run works

1. **Token health**: verifies the Meta token; warns on Telegram when ≤7 days remain.
2. **Select**: lists Drive videos (`.mp4`, `.mov`) in natural order, loads the sheet, and picks
   the first file ID that is not `Published`/`Skipped`. An interrupted job
   (`Processing`/`Analysed`/`Ready`) is resumed first. `Failed` rows retry up to 3 times.
3. **Thumbnail**: `001.mp4` → `001.jpg`/`.jpeg`/`.png`, or the same stem with a `_thumbnail`,
   `_thumb` or `_cover` suffix (`001_thumbnail.jpg`). Missing → Telegram warning, Instagram
   auto-cover is used.
4. **Caption** (default `CAPTION_MODE=files`): reads `captions/<video stem>.txt` from this
   repository and posts it as-is. No AI models are called. A video without a caption file is
   marked `Skipped` and the run stops, so nothing unreviewed is ever posted. Edit or add a
   caption by editing the `.txt` file on GitHub; the change applies from the next run.
5. **AI caption** (optional, `CAPTION_MODE=ai`): audio → Whisper + translation, 3 frames →
   vision model, then a Groq text model writes the caption from the real content. Used only
   when the repository variable `CAPTION_MODE` is set to `ai`.
6. **Host**: upload to Cloudinary under `ig/<drive-file-id>` (re-used if already there),
   verify the URLs answer publicly. Videos over `MAX_UPLOAD_MB` are re-encoded first.
7. **Publish**: create REELS container (`video_url`, `caption`, `cover_url`, `share_to_feed`),
   poll `status_code` until `FINISHED`, call `media_publish`, fetch `permalink`.
   If the publish call errors ambiguously, the account's recent media is checked before
   declaring failure, so a timeout never causes a duplicate.
8. **Record**: sheet row → `Published` with media ID, permalink, date. Cloudinary copies are
   deleted after confirmed publish to keep the free credits low.
9. **Notify** and clean temporary files.

Dry run (`DRY_RUN=true`) performs steps 1–6 and the Telegram preview, but never creates an
Instagram container and never writes to the sheet.

---

## Deployment (beginner-friendly)

### 1. Create a private GitHub repository
1. github.com → **New repository** → name `instagram-automation` → **Private** → Create.
2. On your laptop, in this folder:
   ```bash
   git add .
   git commit -m "Instagram Reels automation"
   git branch -M main
   git remote add origin https://github.com/<you>/instagram-automation.git
   git push -u origin main
   ```
3. Repository → **Settings → Actions → General** → "Allow all actions" and
   "Read and write permissions" for the workflow token (needed by the keep-alive step).

### 2. Python project locally (optional, for local dry runs)
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # fill in values
python -m pytest -q
python -m app.main --check
python -m app.main --dry-run
```
FFmpeg must be on PATH locally (it is installed automatically in the GitHub runner).

### 3. Google Cloud project, Drive + Sheets APIs
1. console.cloud.google.com → New project (e.g. `ig-automation`).
2. **APIs & Services → Library** → enable **Google Drive API** and **Google Sheets API**.
3. **APIs & Services → OAuth consent screen** → External → fill name/email → Save.
   Add your Google account under **Test users**.
   Then click **Publish app** (status "In production"). This matters: in "Testing" status the
   refresh token stops working after 7 days.
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → Download JSON
   (save as `client_secret.json`, never commit it).

### 4. Connect Google Drive securely
```bash
pip install google-auth-oauthlib
python scripts/get_google_refresh_token.py client_secret.json
```
A browser opens, you sign in once, and the script prints `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`. Scopes requested: Drive **read-only** and Sheets.

Folder IDs: open the Drive folder in the browser; the ID is the last part of the URL
`https://drive.google.com/drive/folders/<THIS_IS_THE_ID>`. Do this for **Videos** and **Thumbnails**.

Google Sheet: create a new spreadsheet named **Instagram Posting History** with the same Google
account. Its ID is in the URL `https://docs.google.com/spreadsheets/d/<THIS_IS_THE_ID>/edit`.
The header row and the tab "Posting History" are created automatically on first run.

### 5. Groq API key
console.groq.com → **API Keys → Create API Key** → copy as `GROQ_API_KEY`.
One key covers Whisper, the vision model and the caption model. No card required.

### 6. Vision API
Groq's `qwen/qwen3.8-27b` is a true multimodal model on the free tier,
so **no separate vision key is needed**. Leave `VISION_API_KEY` empty (it falls back to
`GROQ_API_KEY`). If you ever want a different Groq vision model, set `GROQ_VISION_MODEL`.

### 7. Cloudinary
cloudinary.com → Sign up free → Dashboard shows **Cloud name**, **API Key**, **API Secret**.
Free plan: 25 credits/month (1 credit = 1 GB storage or 1 GB bandwidth or 1,000 transformations),
100 MB per video upload. One Reel/day with cleanup after publishing uses roughly 3–8 credits/month.

### 8. Meta Developer App
1. developers.facebook.com → **My Apps → Create App** → use case **"Other"** → type **Business**.
2. Add product **Instagram** → "Instagram API setup with Facebook login" (your Instagram
   Business account must be linked to a Facebook Page: Instagram app → Settings → Business
   tools → Connect a Facebook Page).
3. **App settings → Basic**: note **App ID** and **App Secret**.
4. Open **Tools → Graph API Explorer**, select your app, add permissions
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`,
   `business_management`, click **Generate Access Token**, log in, and allow the Page + IG account.
   Copy the short-lived token.
5. Make it long-lived (60 days) and find your IG user id:
   ```bash
   python scripts/exchange_meta_token.py APP_ID APP_SECRET SHORT_LIVED_TOKEN
   ```
   Copy `META_ACCESS_TOKEN` and `INSTAGRAM_USER_ID` from the output.
   While the app is in **Development** mode this works for admins/testers of the app, which is
   you. No App Review is needed to post to your own account.

**Renewing the Meta token** (every <60 days; the bot warns you 7 days ahead on Telegram):
repeat steps 4–5 and update the `META_ACCESS_TOKEN` secret. Alternative with no expiry:
Business Settings → **System Users** → create one, assign the Page + IG asset, "Generate token"
with the same permissions, and tick "never expires".

### 9. Connect Instagram Business account
Already done in step 8.2 (Page ↔ Instagram link). Confirm the account type is **Business**
(Creator accounts cannot publish via the API).

### 10. Add GitHub repository secrets
Repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | from step 4 |
| `GOOGLE_CLIENT_SECRET` | from step 4 |
| `GOOGLE_REFRESH_TOKEN` | from step 4 |
| `GOOGLE_DRIVE_VIDEO_FOLDER_ID` | Videos folder ID |
| `GOOGLE_DRIVE_THUMBNAIL_FOLDER_ID` | Thumbnails folder ID |
| `GOOGLE_SHEETS_ID` | spreadsheet ID |
| `GROQ_API_KEY` | step 5 |
| `CLOUDINARY_CLOUD_NAME` | step 7 |
| `CLOUDINARY_API_KEY` | step 7 |
| `CLOUDINARY_API_SECRET` | step 7 |
| `META_ACCESS_TOKEN` | step 8 (long-lived) |
| `INSTAGRAM_USER_ID` | step 8 |
| `TELEGRAM_BOT_TOKEN` | optional: from @BotFather |
| `TELEGRAM_CHAT_ID` | optional: message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id` |
| `VISION_API_KEY` | optional, leave unset |

Repository **Variables** (same page, "Variables" tab):

| Variable | Purpose |
|---|---|
| `AUTO_PUBLISH` | `false` (default) = scheduled runs are dry runs. Set `true` to go live. |
| `META_API_VERSION` | optional, default `v24.0` |
| `BRAND_CONTEXT` | optional sentence about your brand for captions |

### 11. First manual dry run
Repository → **Actions → Daily Instagram Reel → Run workflow**:
- first with **check_only = true** → confirms every credential (Drive listing, sheet, Groq,
  Cloudinary ping, Instagram token + username).
- then with **dry_run = true** → downloads the first video, transcribes, analyses, generates the
  caption, uploads to Cloudinary, verifies the URLs, and sends you the caption + video URL on
  Telegram (or prints them in the log). Nothing is published; the sheet is untouched.

Review the caption in the log. Edit `prompts/caption_prompt.txt` if the style needs changing,
push, and re-run the dry run.

### 12. Approve the first Instagram publication
When the dry-run caption looks right, **Run workflow** again with **dry_run = false**.
This publishes exactly one Reel (the first unposted video) and writes the `Published` row.
Check the permalink from the log/Telegram.

### 13. Enable daily 7 AM and 7 PM IST publishing
Settings → Secrets and variables → Actions → **Variables** → set `AUTO_PUBLISH` = `true`.
From then on the 13:30 UTC schedule publishes one Reel per day. To pause, set it back to `false`
(the schedule keeps running as a dry run) or disable the workflow in the Actions tab.

---

## Exact-time posting (the "Post timer" workflow)

GitHub's own cron can run hours late. `.github/workflows/post-timer.yml` is a relay that waits
until exactly 07:00 / 19:00 IST, fires the posting workflow with `scheduled=true`, and relaunches
itself. It runs continuously, which is free on a public repository (unlimited Actions minutes).

- Start: `gh workflow run post-timer.yml`
- Stop: `gh workflow disable post-timer.yml` (cancelling a run just relaunches the chain)
- The hourly cron inside it is only a watchdog that restarts a dead chain.
- The posting workflow has **no cron of its own**, on purpose. GitHub's scheduler ran up to
  8 hours late, which posted at the wrong time and then blocked the real slot through the
  duplicate guard. The timer is the only thing that decides posting times.
- Double posts are impossible: a slot run exits if anything was published within 3 hours.

## Operations notes

- **Scheduling delays**: GitHub may start cron jobs 5–30 minutes late under load. The job itself
  is idempotent and uses a `concurrency` group, so a late run overlapping a manual run cannot
  double-post.
- **Inactivity**: GitHub disables schedules in repositories with no activity for 60 days. The
  last workflow step re-enables the workflow through the API on every run as insurance.
- **Failures**: any real failure makes the run red (GitHub emails you) and sends a Telegram
  message with the run link. The video is marked `Failed` with the error, not `Published`.
- **Quota exhaustion** (Groq 429, Instagram rate limit): the day is skipped with a notification.
  No paid fallback is ever used.
- **Nothing left to post**: notification "No unposted videos left", exit 0.
- **Secrets in logs**: the logger replaces every configured secret with `***`.

## Free-tier limits (verified Nov 2026, re-check periodically)

| Service | Free allowance | Daily usage here |
|---|---|---|
| GitHub Actions (private repo) | 2,000 min/month | ~5–10 min/day ≈ 150–300 min/month |
| Groq Whisper `whisper-large-v3` | ~20 req/min, 2,000 req/day, 28,800 audio-sec/day | 1–3 requests |
| Groq Qwen 3.8 27B (vision) | ~30 req/min, 1,000 req/day | 1 request |
| Groq GPT-OSS 120B | ~30 req/min, 1,000 req/day, 100k tokens/day | 1 request |
| Cloudinary | 25 credits/month, 100 MB/video | ~0.2 credits/day, deleted after publish |
| Google Drive/Sheets API | 20,000 / 300 requests per minute per project | <20 requests |
| Meta Graph API | 25 content-publishing posts per 24 h per account | 1 post |
| Telegram Bot API | free | 1–2 messages |

**Expected monthly cost: ₹0.** The only things that can cost money are things this project does
not do (Cloudinary paid plans, paid AI APIs). If Cloudinary's 100 MB/video limit is hit, the
video is re-encoded to fit rather than upgrading the plan.

## Tests
```bash
python -m pytest -q
```
Covers natural sorting, thumbnail matching, duplicate prevention and resume logic, sheet
schema/round-trip, caption prompt building and parsing, Instagram API flow (mocked HTTP),
token-expiry/rate-limit detection, secret redaction, and the FFmpeg pipeline on a synthetic video.
