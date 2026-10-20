# DEV set authoring brief (Z1b, spec section 6.5)

Audience: an ordinary agent session on the development side. This run spends model usage and is started
by Raouf by hand. It is never started by the plan's implementer and never by the sealed side.

This file is public. It carries a rubric and a schema only: no corpus text, no example queries, no page names.

## What you are making

About 200 retrieval questions over the live Zurvan knowledge base, spread over the 16 strata below
(about 12 or 13 per stratum, never fewer than 10 per stratum). The set is the visible DEV pool: it is used
for development and for the contamination checks of the sealed authoring run. It is NOT a promotion gate.

Write the raw output to ~/.zurvan/instruments/z1-dev-raw.jsonl, one JSON object per line. Never write
inside the repository. Never paste a query, a page name or any corpus text into a tracked file, a commit
message, a changelog entry or a chat summary: report counts per stratum and nothing else.

## Raw item format

Each line is one object with exactly these keys:

  stratum     one of the 16 stratum ids below
  query       the question, written the way a person would type it into a search box
  difficulty  easy, medium or hard
  targets     a list of pages that answer it. Each target has "page" (the store-relative path of an indexed
              page, exactly as the search index records it, for example a path beginning with wiki/ or docs/)
              and "locator" with "kind" page or section and "value" (empty for page, the exact heading text
              for section). A not_found item has an empty list.

After you finish, the operator runs the finalizer, which adds ids, grading and provenance, then validates
and freezes the file. Do not add those fields yourself.

## Strata

  exact_identifier     a precise identifier (a name, key, flag or code token) is the thing being asked about
  path                 a filename or path fragment is the thing being asked about
  hash                 a hash or digest fragment is the thing being asked about
  error_string         a fragment of an error or log message is the thing being asked about
  semantic             paraphrase of an idea with little vocabulary overlap with the page
  alias                the page uses another name for the same thing (abbreviation, old name, synonym)
  decision_rationale   why a decision was made, answered by a decision page
  temporal             which page records what something was before or after a change
  contradiction        which pages disagree about the same fact
  provenance           where a statement came from, answered by a page that names its source
  code                 a question about how code in the indexed corpus behaves
  multi_hop            needs two or more pages together; list every page needed
  synthesis            needs several pages across the corpus; list every page needed
  cross_project        the answer lives under a different project than the vocabulary suggests
  hard_negative        looks like a nearby page but the answer is a different page
  not_found            the corpus does not contain the answer; targets is empty

## Rules

1. Ground every question. Open the page, confirm the answer is on it, and only then list it as a target.
   A target you did not open is a defect.
2. Answer leakage. The query must not contain the target page's filename, its full title, or the exact
   heading you list as a section locator. Use the vocabulary a searcher would have before finding the page.
3. One answer per question where the stratum allows it. If two different pages could fairly answer a
   rank-graded question, list both as targets or drop the question.
4. No near-duplicates. Do not write two questions that differ by a word or two. Vary topic and phrasing.
5. Stay inside the corpus. Do not use the open web. Do not use any other evaluation file on this machine.
6. Never read anything under ~/.zurvan/instruments other than the file you are writing. In particular do
   not open any retired or sealed set.
7. A stratum you cannot fill honestly stays short. Report the shortfall as a count. Do not pad.

## Done means

The raw file exists, every line parses, and your final message states only the number of items per stratum.
