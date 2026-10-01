# Sealed authoring rubric (Z1b, spec sections 4 and 6)

This file is public. It carries definitions and rules only: no corpus text, no example queries, no page
names. It is read by the author and by the reviewer. The independent re-solver never sees it.

## What an item is

A retrieval question over a private knowledge base, with the page or pages that answer it. The unit of
truth is the page (and, for a section locator, the heading). A retriever is credited when a returned chunk
resolves to a listed target.

An author candidate is one JSON object with exactly these keys:

  query       the question as a person would type it into a search box, one to three sentences
  difficulty  easy, medium or hard (definitions below)
  targets     list of objects, each with "page" (store-relative path of an indexed page, exactly as the
              index records it, for example a path starting with wiki/ or docs/) and "locator", an object
              with "kind" (page or section) and "value" (empty text for page, the exact heading text for
              section). A not_found candidate has an empty list.

The pipeline adds the identifier, the grading rule and the provenance. A candidate must not set them.

## Strata

  exact_identifier     a precise identifier (a name, key, flag or code token) is what is being asked about
  path                 a filename or path fragment is what is being asked about
  hash                 a hash or digest fragment is what is being asked about
  error_string         a fragment of an error or log message is what is being asked about
  semantic             a paraphrase of an idea with little vocabulary overlap with the answering page
  alias                the answering page uses another name for the same thing
  decision_rationale   why a decision was made, answered by a decision page
  temporal             which page records the state of something before or after a change
  contradiction        which pages disagree about the same fact (every disagreeing page is a target)
  provenance           where a statement came from, answered by a page that names its source
  code                 how code in the indexed corpus behaves
  multi_hop            needs two or more pages together (set grading: list every page needed)
  synthesis            needs several pages across the corpus (set grading: list every page needed)
  cross_project        the answer lives under a different project than the query vocabulary suggests
  hard_negative        resembles a nearby page, but the answer is a different page
  not_found            the corpus does not contain the answer (abstain grading: empty target list)

## Difficulty

  easy     the query shares distinctive words with the answering page; the answer is one clear page
  medium   the query shares some vocabulary but also needs interpretation, or several pages look relevant
  hard     little shared vocabulary, or the answer needs combining pages, or near-misses are common

## Grounding rules

1. Open every page you list. A target you did not read is a defect. A target must contain the answer, not
   merely the topic.
2. Cite the page path exactly as it appears in the indexed corpus (store-relative). A page that does not
   exist is rejected by a deterministic filter before any reviewer sees it.
3. A section locator is used only when the answer sits under one heading; its value is that heading's
   exact text. Otherwise use a page locator.
4. A rank-graded question has one answering page, or lists every page that fairly answers it. If two pages
   could answer and you list one, the question is ambiguous: drop it.
5. A not_found question must be answerable-sounding yet absent. Search the corpus before claiming absence.

## Answer-leakage rules

A query must not hand the retriever the answer's address. The deterministic filter rejects a query that
contains any of: a target's store-relative path, a long filename stem, the page's full title, a section
locator's heading, or most of the distinctive words of the page title. Write the query in the words a
searcher has before finding the page. For the identifier, path, hash and error_string strata the item
asked about may appear in the query, but never the page that explains it.

## Diversity and independence

1. No two candidates may be near-duplicates, within or across strata. The filter rejects exact, lexically
   overlapping and MinHash near-duplicate queries.
2. No candidate may resemble the DEV set. The filter rejects near-duplicates of any DEV query.
3. Spread questions across topics, projects and page types. Do not mine one page repeatedly.
4. Do not use the open web. Do not read any file outside the corpus directory.

## Reviewer standard

Reject a candidate that is ambiguous, unsolvable from the corpus, assigned to the wrong stratum, trivial,
artificial, ungrounded, or whose query leaks its answer. When in doubt, reject. A reviewer verdict is a
decision per candidate; reasons are optional and are never retained by the pipeline.

## Output format

Every role answers with one JSON value and nothing else of substance:

  author     an object with key "candidates": a list of candidate objects as defined above
  reviewer   an object with key "verdicts": a list of objects with "n" (candidate number), "keep" (true or
             false) and an optional short "reason"
  resolver   an object with key "results": a list of objects with "page" and "heading" (null when none),
             best first, at most ten; an empty list when the corpus holds no answer
