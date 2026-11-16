# Exact-time posting with cron-job.org (free)

GitHub's own cron can start late or skip a slot. cron-job.org (free plan) calls GitHub's API at the
exact minute instead. GitHub's cron stays enabled as a backup; the 6-hour guard stops double posts.

## Part A: create a GitHub token (2 minutes)

1. Open https://github.com/settings/personal-access-tokens/new (log in as lenababu).
2. Token name: `cron-job-trigger`
3. Expiration: choose **Custom** and pick a date one year from today. Note it down; the token must
   be renewed before then.
4. Repository access: **Only select repositories** → choose `instagram-automation`.
5. Permissions → Repository permissions → **Actions** → set to **Read and write**.
   (Metadata becomes Read-only automatically. Nothing else is needed.)
6. Click **Generate token** and copy it. It starts with `github_pat_`. You will not see it again.

## Part B: create the two jobs on cron-job.org (5 minutes)

1. Go to https://cron-job.org and click **Sign up** (free). Confirm the email they send.
2. Log in → **Create cronjob**.
3. Fill in:
   - Title: `Instagram 7 AM IST`
   - URL: `https://api.github.com/repos/lenababu/instagram-automation/actions/workflows/daily-instagram-post.yml/dispatches`
   - Enable job: on
   - Schedule: choose **Custom**. Timezone `Asia/Kolkata`. Days: every day. Hours: `7`. Minutes: `0`.
4. Open the **Advanced** tab:
   - Request method: **POST**
   - Headers: click "Add header" twice and enter
     - `Authorization` = `Bearer github_pat_...` (paste your token after the word Bearer and a space)
     - `Accept` = `application/vnd.github+json`
   - Request body:
     ```
     {"ref":"main","inputs":{"scheduled":"true"}}
     ```
   - Time-out: 30 seconds. Treat redirects: default.
5. Click **Create**.
6. Repeat steps 2 to 5 for a second job titled `Instagram 7 PM IST` with Hours `19`, Minutes `0`.
   Everything else identical.
7. Open either job and click **Run now** (or "Test run"). The result must show status **204**.
   A 401 means the token is wrong; a 404 means the URL or the token's repository access is wrong.
   A test run only starts a GitHub run, and that run posts nothing if a video went out in the
   last 6 hours, so it is safe.

## How it fits together

- cron-job.org → GitHub API at 07:00 and 19:00 IST sharp → the workflow runs with `scheduled=true`.
- GitHub's own cron also fires around those times. Whichever comes first posts; the rest see a post
  within 6 hours and exit.
- To pause posting: set the repository variable `AUTO_PUBLISH` to `false` on GitHub. Both triggers
  then run as dry runs.
- Token renewal: GitHub emails you before the token expires. Create a new one and replace the
  Authorization header in both cron-job.org jobs.
