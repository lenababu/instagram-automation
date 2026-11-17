# From one account to a product for companies

Two separate questions are answered here.

1. Adding LinkedIn, Facebook and X to the posting run.
2. Turning this into a system that serves other companies.

They are separate because the hard part of (2) is not code. It is Meta's and LinkedIn's
approval process, and that work should start before the rewrite, because it is the long pole.

---

## Part 1: the other three networks

| Network | Cost | Gate | Effort | Status |
|---|---|---|---|---|
| Facebook Page | Free | `pages_manage_posts` on the existing token | Built | Waiting on a new token |
| LinkedIn | Free | App + product approval; company Pages need Community Management API review | ~1 day of code after approval | Not started |
| X (Twitter) | **Paid**, see below | Billing account | ~1 day of code | Not started |
| YouTube Shorts | Free, 6 uploads/day quota | OAuth consent screen | ~1 day | Suggested later |

### Facebook: done, one action left
The code is in `app/facebook_publisher.py`. It reuses the Meta app that already publishes to
Instagram, and asks Facebook to pull the video straight from the Cloudinary URL, so nothing extra
is uploaded. Cross-posting is best effort: if Facebook fails, the run logs it and the Instagram
post is untouched.

To switch it on, regenerate the Meta token with `pages_manage_posts` added to the existing five
permissions. The connection check reports `facebook: ready, page <id>` once that is done.

A zero-code alternative exists: in the Instagram app, Settings, "Sharing to other apps", turn on
sharing Reels to Facebook. It gives you no separate caption and no link back, which is why the
API path is worth the one extra permission.

### LinkedIn: free, but approval-gated
Posting to a personal profile needs the "Share on LinkedIn" product and the `w_member_social`
scope, which is close to self-serve. Posting to a **company page** needs `w_organization_social`,
which only comes with the Community Management API, and that is reviewed by hand in two stages:
Development access first, then Standard access, where LinkedIn asks for a screen recording of the
app in use. The authorising person must hold an Administrator or Content Admin role on the page.

Video is supported: register the upload, PUT the bytes, poll until the asset is `AVAILABLE`, then
create the post referencing `urn:li:video:...`. Tokens last 60 days like Meta's.

**Recommendation:** apply for Community Management API access now, in parallel with everything
else. Build the publisher while the application is pending.

### X: this is the one that costs money
X moved to pay-per-use pricing in February 2026 and there is no free tier for new developers any
more. Free access is now reserved for apps X itself classifies as public utilities.

Published rates: about $0.015 to create a post, and about **$0.20 if the post contains a URL**.
At two posts a day that is roughly $0.90 a month without links, or about $12 a month with a link
in every post. Media upload pricing is not published clearly and should be confirmed in the
developer console before you rely on it.

This breaks the ₹0 rule the project was built on, so I have not implemented it. It is cheap in
absolute terms for one account, but per-tenant it becomes a real line item: 50 companies posting
twice a day with a link is roughly $600 a month. Decide whether X is worth that before building
it, and if it is, charge for it as an add-on rather than bundling it.

---

## Part 2: making this a system for companies

### What blocks it today
The current design assumes one of everything: one set of GitHub secrets, one Drive folder, one
sheet, one Instagram account. That is the right design for one brand and the wrong design for
fifty. But the code is not the blocker.

**The real blocker is Meta App Review.** The app publishes today only because you are its admin,
so it works in development mode on your own accounts. The moment it must publish to a *client's*
Instagram account, Meta requires Advanced Access for `instagram_content_publish`, which requires:

- A business that has completed Business Verification. Legal registration documents, and the
  details must match your Meta Business Manager exactly.
- App Review, with a screen recording showing the permission actually in use. Current guidance
  puts this at up to 20 business days per submission, and rejections are common and slow.

Start this now. Everything else can be built while it is pending.

### Target architecture

```
  Client browser
        |
   [ Web app ]  onboarding, OAuth connect, schedule, caption editor, run history
        |
   [ Postgres ]  tenants, social_accounts, assets, posts, post_targets, runs
        |
   [ Scheduler ]  one process; every minute, claim posts whose run_at has passed
        |
   [ Worker ]  download asset -> host -> publish to each target -> record
        |
   [ Publishers ]  instagram | facebook | linkedin | x | youtube
```

**Tenancy.** One row per company. Every table carries `tenant_id` and every query filters on it.
Each tenant owns a timezone and a posting schedule, so "7 AM and 7 PM IST" becomes per-client
configuration rather than a cron expression in a YAML file.

**State.** Postgres replaces the Google Sheet. Supabase and Neon both have free tiers that
comfortably hold this. Keep the idea that already works: a unique key on
`(tenant_id, asset_id, network)` makes a double post impossible, which is the same guarantee the
Drive file ID gives today.

**Credentials.** This is the part that changes most. Today you and I paste tokens by hand. In the
product, a company clicks "Connect Instagram", completes Meta's OAuth flow, and the app stores the
long-lived token encrypted, with a daily job that refreshes anything inside 14 days of expiry.
Tokens must be encrypted at rest with a key that is not in the database. Never log them.

**Media.** Cloudinary's free tier will not survive many tenants. Cloudflare R2 is the usual answer:
no egress fees, and the publishers only need a public URL for a few minutes. Keep the existing
pattern of deleting the hosted copy once the platform has fetched it.

**Scheduling.** Drop GitHub Actions. It was the right free choice for one account and it is the
wrong runtime for a product: cron is unreliable, there is no per-tenant timezone, and concurrency
is awkward. A single small worker (Fly.io, Railway, or a cheap VPS) that polls a `posts` table
handles hundreds of tenants on one machine.

**Publishers.** The existing modules are already close to the right shape. Formalise them behind
one interface:

```python
class Publisher(Protocol):
    network: str
    def publish(self, media_url: str, caption: str, options: dict) -> PublishResult: ...
    def verify_credentials(self) -> CredentialStatus: ...
```

`instagram_publisher.py` and `facebook_publisher.py` already match it in spirit. Adding TikTok or
YouTube later then becomes one new class and one row in a registry, not a change to the scheduler.

### What carries over unchanged
The parts worth keeping are the ones that took the longest to get right: the status machine that
survives an interrupted run, the duplicate guard, the resilience of treating cross-posting as best
effort, the pre-written captions in version control, and the secret-redacting log formatter. That
is most of the value in this repository.

### Suggested sequence

| Phase | Work | Blocked by |
|---|---|---|
| 0 | Regenerate the token with `pages_manage_posts`; Facebook goes live | You |
| 1 | Submit Meta Business Verification and App Review. Apply for LinkedIn Community Management | Meta, LinkedIn, weeks |
| 2 | Postgres schema, tenant model, migrate this account's history into it | Nothing |
| 3 | Worker and scheduler on a small host; retire the GitHub Actions timer | Phase 2 |
| 4 | OAuth onboarding web app and the token refresh job | Phase 1 approvals |
| 5 | LinkedIn publisher; X only if the per-post cost is acceptable | Phase 1 |

Phases 2 and 3 are the real engineering and can start immediately. Phase 1 is paperwork and
waiting, which is exactly why it should be submitted first.

### One honest warning
A dozen funded companies already sell this exact product, and they have cleared the Meta and
LinkedIn reviews that you have not. The defensible part is unlikely to be the posting pipeline.
It is more likely the thing this repository already does unusually well for LIGENIX: turning a
folder of finished videos into a consistent posting rhythm with captions that someone actually
approved. If you sell to a specific niche and own the content side, the pipeline is a feature. If
you sell the pipeline alone, you are competing on price with Buffer and Metricool.
