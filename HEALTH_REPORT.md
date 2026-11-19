# Phase 1 automation health report

Date: 19 November 2026
Repository: `lenababu/instagram-automation` (public)
Instagram account: `@ligenixai` (ID 17841426611724795)
Facebook Page: Ligenix ai (ID 1490553930802649)

---

## 1. Summary

The automation had **two separate faults**, not one. The timing fault was found and fixed on
17 November. The second fault was found today and is the reason nothing posted on 18 and
19 November, and it had nothing to do with scheduling.

| # | Fault | Found | Status |
|---|---|---|---|
| 1 | GitHub Actions cron ran hours late and posted at the wrong time | 17 Nov | Fixed, verified |
| 2 | Queued `Pending` rows were never selected, so the whole queue stalled | 19 Nov | Fixed, verified in dry run |

Six Reels have been published through the API in total. The scheduler now fires within two
seconds of the target minute. One thing is still unproven: a 7 PM slot has never completed a
publish end to end, because fault 2 was blocking it. Tonight's 19:00 IST slot is that proof.

---

## 2. Fault 1: scheduled execution ran hours late

**What the audit found.** The cron expressions themselves were correct
(`30 1 * * *` and `30 13 * * *` UTC are exactly 07:00 and 19:00 IST). The workflow was on the
default branch and enabled. Nothing was timing out and no run was overlapping.

The problem is that **GitHub does not guarantee when a scheduled workflow starts.** Scheduled
runs are best-effort and are dropped or delayed under load. Measured on this repository:

| Slot | Should start | Actually started | Late by |
|---|---|---|---|
| 16 Nov, 19:00 IST | 13:30 UTC | 22:58 UTC | 9 h 28 m |
| 17 Nov, 07:00 IST | 01:30 UTC | 09:26 UTC | 7 h 56 m |

The second of those published video 03 at **14:56 IST**, nowhere near a posting slot.

It then caused a second, subtler failure. Because that post landed at 14:56, the duplicate
guard (then 6 hours) saw a recent post when the 19:00 slot arrived and skipped it. So GitHub's
lateness both posted at the wrong time *and* suppressed the right one.

**The fix.** Posting times are no longer decided by GitHub's cron.
`.github/workflows/post-timer.yml` is a relay: it computes the next 07:00 or 19:00
Asia/Kolkata instant, sleeps until that second, fires the posting workflow, and relaunches
itself for the following slot. Long waits are split into hops of 5 h 30 m because a job may
run at most 6 hours. The posting workflow's own `schedule:` block was removed entirely, so
nothing but the timer can decide a posting time.

Safeguards:

- An hourly watchdog cron restarts the chain if it ever dies. Lateness there costs at most an
  hour, instead of nine.
- Only one relay may be alive. A second one exits immediately. **Verified live** at 08:45 UTC
  today: `Another relay is running (44476466995); this one exits so only one chain stays alive.`
- The duplicate guard was reduced from 6 hours to 3, since only the timer fires slots now.
- The timer keeps its own workflow enabled, so GitHub's 60-day inactivity rule cannot silently
  disable it.

**Measured result since the fix.** Every slot has fired within two seconds:

| Slot | Target | Fired | Delay |
|---|---|---|---|
| 18 Nov 07:00 IST | 01:30:00 UTC | 01:30:01 UTC | 1 s |
| 18 Nov 19:00 IST | 13:30:00 UTC | 13:30:02 UTC | 2 s |
| 19 Nov 07:00 IST | 01:30:00 UTC | 01:30:02 UTC | 2 s |

End to end, including Instagram's own media processing, the 18 Nov morning post was live
**81 seconds** after the slot opened.

---

## 3. Fault 2: the queue stalled silently

**Symptom.** The 18 Nov evening and 19 Nov morning runs both fired on time, then reported
`No unposted videos left in Google Drive` and exited, while Drive held 58 videos and only 5
were published.

**Root cause.** A logic gap in `select_next()` in `app/duplicate_checker.py`. It chose a video
only if the row did not exist yet, or had status `Failed` with retries remaining. A row whose
status was `Pending` matched no branch at all and fell through the loop:

```
is_already_published(rec)  -> False   (Pending is not terminal)
rec is None                -> False   (the row exists)
rec.status == Failed       -> False   (it is Pending)
                           -> skipped forever
```

So the code never selected a row carrying the exact status it defines as the starting state.
This went unnoticed while rows were created by the job itself. Between 18 Nov 01:31 and
13:48 the sheet was pre-filled with all 58 videos as `Pending`, with captions written in.
From that moment every video was invisible to the selector and the queue reported empty.

**The fix.** `select_next()` now also selects rows with status `Pending` or a blank status,
with three regression tests. The tests were confirmed to **fail against the previous code and
pass against the new code**, so they genuinely cover the defect.

**A second fault this exposed.** Videos 29 to 58 have captions in the sheet but no
`captions/<stem>.txt` file. Fixing only the selector would have reached each of them, found no
caption file, and marked them `Skipped`, which is terminal and permanent. Thirty videos would
have been destroyed silently.

Caption resolution was therefore changed and extracted into `resolve_caption()`:

1. the caption already on the sheet row, if any;
2. otherwise `captions/<stem>.txt`;
3. otherwise, only when `CAPTION_MODE=ai`, a generated one;
4. otherwise the video is skipped with a clear reason.

A caption a person wrote is never rewritten or regenerated, in any mode. The run now logs
which source it used.

**Verified.** A dry run after the fix selected the correct next video and the correct source:

```
Drive: 58 videos, 58 with thumbnails
Selected 05_LIGENIX_Late_Payments_Ravis_Print_Shop.mp4
Caption source: sheet
Outcome: dry_run
```

---

## 4. Audit checklist

| Check | Finding |
|---|---|
| Cron expression | Correct, but removed; GitHub's scheduler is not dependable |
| UTC vs IST conversion | Correct. 01:30 and 13:30 UTC = 07:00 and 19:00 IST |
| Workflow on default branch | Yes, `main` |
| Workflow enabled | Yes, and the timer re-enables itself each cycle |
| GitHub delaying scheduled events | **Yes, by up to 9.5 hours.** The primary fault |
| Previous execution still running | No overlap found; a concurrency group prevents it |
| API requests timing out | No. Retry with backoff is in place and now tested |
| Selecting the correct video | **Was broken.** Fixed today |
| Loading the correct caption | **Was at risk.** Fixed today; sheet is now first source |
| Publishing completing successfully | Yes. 6 Reels confirmed by media ID and permalink |
| Recording the post correctly | Yes. Status, media ID, permalink and timestamps all written |
| Authentication method | Meta Graph API `v24.0`, long-lived user token in GitHub Secrets |

**Token note.** Meta now reports the token with `expires_at: 0`, meaning it does not expire,
rather than the January date reported earlier. `token_days_left()` already treats 0 as
non-expiring. `data_access_expires_at` is still set to around February 2027, which is when
re-authorisation may be needed instead.

---

## 5. Phase 1 test results

| # | Test | Method | Result |
|---|---|---|---|
| 1 | Manual publishing | Live | **Pass.** 6 Reels, each with a media ID and permalink |
| 2 | Scheduled 7 AM | Live | **Pass.** Fired 01:30:01 UTC, live 81 s later |
| 3 | Scheduled 7 PM | Live | **Trigger passes** (13:30:02 UTC). Publish unproven; blocked by fault 2. Tonight is the proof |
| 4 | Correct caption | Unit + live dry run | **Pass.** `test_sheet_caption_wins_over_the_file`; dry run logged `Caption source: sheet` |
| 5 | Correct video | Unit + live dry run | **Pass.** Order and Drive-file-ID identity covered; dry run selected 05 |
| 6 | Invalid media | Unit | **Pass.** `test_validate_for_reels` rejects bad dimensions and duration |
| 7 | Expired token | Unit | **Pass.** `test_token_expired_detected`; codes 190/102 and subcodes 463/467/460 raise `TokenExpired` |
| 8 | Network failure | Unit | **Pass.** Added today. Retries twice then succeeds; gives up cleanly after three attempts |
| 9 | Duplicate trigger | Unit + live | **Pass.** Three runs on 17 Nov, one posted and two correctly no-opped. Relay guard verified live today |
| 10 | Recovery after failed publish | Unit | **Pass.** `test_publish_error_checks_actual_state_before_failing` reconciles against recent media before reporting failure |

Full suite: **53 tests passing.**

---

## 6. What is still unproven

Only one thing: **a 7 PM slot completing a publish.** The trigger has fired on time three
times, but the two most recent evening slots hit fault 2. Tonight at 19:00 IST the queue
should publish video 05 with the caption from the sheet. That is the single remaining Phase 1
gate.

Separately, Facebook cross-posting is coded and tested but **inactive**: the Meta token is
missing `pages_manage_posts`. The connection check reports this explicitly. It is not a Phase 1
blocker.

---

## 7. Scheduler decision

The spec asks for Supabase Cron as the primary engine. For Phase 1 that is the wrong trade:
it would mean standing up Supabase, moving state out of the sheet and rewriting the worker
before knowing whether the posting pipeline is sound. The timer solves the timing fault now,
at no cost, and is already verified to the second.

For Phase 2 the picture changes, because per-company timezones and schedules cannot live in a
YAML file. At that point Supabase Cron polling a `posting_jobs` table every minute is the right
design, and the timer retires. That migration should happen once, with the database, rather
than twice.

Cost position today: unchanged at zero. Unlimited Actions minutes because the repository is
public, Cloudinary free tier, Google free tier, Meta Graph API free.
