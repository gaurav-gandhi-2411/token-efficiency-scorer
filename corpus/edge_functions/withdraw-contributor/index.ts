// corpus/edge_functions/withdraw-contributor/index.ts
//
// Supabase Edge Function (Deno runtime). This is the ONLY code path in the
// whole tracegauge community-corpus system that can delete rows from
// corpus_contributions — the anon role used by the client has no
// select/update/delete RLS policy at all (see corpus/schema.sql), so the
// client itself is structurally incapable of deleting anything. This
// function runs with the Supabase-provided service-role key, which bypasses
// RLS entirely. That means the UUID validation below is not a nicety — it is
// the only thing standing between an arbitrary request body and an
// unauthenticated, unscoped-by-RLS DELETE against this table. A malformed,
// oversized, or crafted contributor_id must never reach the DELETE call.
//
// Deploy with: supabase functions deploy withdraw-contributor
// (see corpus/setup.md). SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are
// auto-provided by the Supabase Edge Functions runtime — no manual secret
// configuration is required for this function to run.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// Strict UUIDv4 validation. tracegauge's contributor_id is always generated
// client-side via Python's uuid.uuid4() (tes/contribution.py), so a
// legitimate request will always match this pattern exactly. Anything else
// (wrong length, non-hex characters, wrong version/variant nibbles, SQL
// metacharacters, etc.) is rejected with 400 before the Supabase client is
// even asked to build a query.
const UUID_V4_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "method not allowed" }), {
      status: 405,
      headers: { "Content-Type": "application/json" },
    });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const contributorId = (body as { contributor_id?: unknown })?.contributor_id;

  // Reject anything that is not a syntactically valid UUIDv4 string. This is
  // the critical safety check referenced in the module comment above: the
  // service-role client below bypasses RLS, so an unvalidated contributor_id
  // could otherwise be used to probe or manipulate the DELETE in ways RLS
  // would normally prevent for any other client.
  if (typeof contributorId !== "string" || !UUID_V4_RE.test(contributorId)) {
    return new Response(
      JSON.stringify({ error: "contributor_id must be a valid UUIDv4 string" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  // Both env vars are auto-provided by the Supabase Edge Functions runtime
  // for every deployed function — they do not need to be set manually via
  // `supabase secrets set`. The service role key never leaves this runtime
  // and is never given to the client.
  const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const supabase = createClient(supabaseUrl, serviceRoleKey);

  const { error, count } = await supabase
    .from("corpus_contributions")
    .delete({ count: "exact" })
    .eq("contributor_id", contributorId);

  if (error) {
    return new Response(JSON.stringify({ error: error.message }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(JSON.stringify({ deleted_count: count ?? 0 }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
});
