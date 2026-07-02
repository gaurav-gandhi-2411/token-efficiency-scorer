# Community Corpus Setup

> **Status as of 2026-07-02: NOT DONE.** No Supabase project has been
> provisioned for tracegauge's community corpus. `tes corpus contribute`
> ships built and tested but dormant — see `CURRENT_STATE.md`'s "Activation
> checklist" section. This file is the detailed how-to for that checklist;
> follow it in full (through step 6 below) when the decision is made to
> activate. Until then, running any command in this doc is optional and has
> not been done — it does not describe tracegauge's current live state.

This is the one-time setup for standing up the tracegauge community corpus:
a Supabase (hosted Postgres + Edge Functions) project that receives the
content-free session-aggregate rows built by `tes.contribution` and sent by
`tes.corpus_client`. Nothing here is required to use tracegauge locally —
`tes corpus contribute` is entirely opt-in and does nothing unless
`TES_CORPUS_URL` / `TES_CORPUS_ANON_KEY` / `TES_CORPUS_WITHDRAW_URL` are set.

## 1. Create the Supabase project

1. Go to [supabase.com](https://supabase.com) and create a free account/project.
2. **Region: `eu-west-1` (Ireland).** This is a deliberate choice, not a
   default: GDPR is the strictest privacy regime that plausibly applies to an
   anonymous, opt-in, EU-and-rest-of-world contributor base. Hosting in an
   EU region is the most defensible choice for a dataset that may include EU
   contributors, regardless of where any individual contributor is located.
3. Note the project's **Project URL** and **anon public key** — both are on
   the project's **Settings → API** page. You will need them in step 4.

## 2. Run the schema

1. Open the Supabase **SQL Editor** for the project.
2. Paste the full contents of [`corpus/schema.sql`](./schema.sql) and run it.
3. Confirm in **Table Editor** that `public.corpus_contributions` exists with
   the 14 payload columns (`task_type`, `real_tokens`, `token_count_input`,
   `token_count_output`, `cache_creation`, `cache_read`, `waste_event_count`,
   `waste_detectors_fired`, `model`, `turn_count`, `week_bucket`,
   `tracegauge_version`, `schema_version`, `contributor_id`) plus the
   server-managed `id` and `inserted_at` columns.
4. Confirm RLS is enabled and only one policy exists (`anon can insert
   contributions`, `INSERT`). If you see any select/update/delete policy for
   `anon`, something has diverged from `corpus/schema.sql` — stop and
   reconcile before going further; that is the layer that prevents the
   anon key (which ships inside the tracegauge client) from being usable to
   read or destroy other contributors' data.

## 3. Deploy the withdrawal Edge Function

Requires the [Supabase CLI](https://supabase.com/docs/guides/cli) and the
project linked locally (`supabase login`, then `supabase link --project-ref
<ref>` from the repo root, run once per machine).

```bash
supabase functions deploy withdraw-contributor
```

This deploys [`corpus/edge_functions/withdraw-contributor/index.ts`](./edge_functions/withdraw-contributor/index.ts).
No manual secret configuration is needed: `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY` are automatically injected into every deployed
Edge Function's runtime environment by Supabase. The service-role key is
never entered, stored, or referenced anywhere outside that runtime — it is
not an environment variable you set, and it must never be given to the
tracegauge client.

After deploy, the function's invoke URL is:

```
{project_url}/functions/v1/withdraw-contributor
```

This is the value for `TES_CORPUS_WITHDRAW_URL` below.

## 4. Configure environment variables

Three environment variables point the tracegauge client at this project:

| Variable | Value | Notes |
|---|---|---|
| `TES_CORPUS_URL` | Project URL from Settings → API | e.g. `https://xxxxx.supabase.co` |
| `TES_CORPUS_ANON_KEY` | `anon` `public` key from Settings → API | **Safe to embed/publish** — see below |
| `TES_CORPUS_WITHDRAW_URL` | `{project_url}/functions/v1/withdraw-contributor` | From step 3 |

**The anon key is safe to make public.** It is not a secret in the usual
sense — it identifies the `anon` Postgres role, which is scoped entirely by
the RLS policy in `corpus/schema.sql` (insert-only, nothing else). Exposing
it does not grant read, update, or delete access to anything, because no
such policy exists for that role. This is why it can be embedded directly in
a distributed CLI tool without secret-management infrastructure.

**The service role key must never be given to the client, checked into this
repo, or set as a `TES_CORPUS_*` variable anywhere.** It lives only inside
the Edge Function's Supabase-managed runtime (step 3) and bypasses RLS
entirely — that is precisely why deletion is only possible through that one
validated, service-role-authenticated function, never directly from the
client.

## 5. Verify it worked

With a real tracegauge session already scored locally:

```bash
TES_CORPUS_URL=https://xxxxx.supabase.co \
TES_CORPUS_ANON_KEY=<anon-key> \
TES_CORPUS_WITHDRAW_URL=https://xxxxx.supabase.co/functions/v1/withdraw-contributor \
tes corpus contribute
```

Confirm in the Supabase **Table Editor** that new row(s) appear in
`corpus_contributions`, with content-free columns only (no session_id, no
paths, no prompt/code text — see the field list in step 2).

Then run:

```bash
TES_CORPUS_URL=https://xxxxx.supabase.co \
TES_CORPUS_ANON_KEY=<anon-key> \
TES_CORPUS_WITHDRAW_URL=https://xxxxx.supabase.co/functions/v1/withdraw-contributor \
tes corpus withdraw
```

Confirm the row(s) tied to that contributor_id have disappeared from the
Table Editor, and that the local `~/.tes/contributor_id.txt` has been
removed (a future `tes corpus contribute` will generate a fresh,
unlinked ID).

## 6. Close out the dormancy notices, then publish

Once step 5's round-trip is confirmed:

1. In `PRIVACY.md`, remove the "NOT currently active" callout in the
   "Community corpus contribution" section and change the conditional
   language ("would be sent" / "would be stored") back to present tense.
2. In `README.md`, remove the "not currently active" / "not currently
   provisioned" phrasing in the three places it appears (top summary,
   Features list, Scope & Limitations).
3. Update `CURRENT_STATE.md`: replace the "0.9.0 BUILT, NOT PUBLISHED —
   dormant by choice" section with the live status, recording which Supabase
   project and region were used and the date the round-trip was confirmed.
4. Re-run the full suite (`python -m pytest -q`) and
   `git diff --exit-code tes/_waste_detectors.py` one more time.
5. The PyPI publish itself remains escalation-gated per the project's
   standing rule on the transmission boundary — get sign-off before running it.

## Cost

**$0.** The Supabase free tier (500MB Postgres, Edge Functions included) is
sufficient at this scale — content-free rows are tiny (14 short fields, no
free text), and a community corpus of even tens of thousands of sessions
stays well under the free-tier storage limit. No other paid resources are
used anywhere in this setup.
