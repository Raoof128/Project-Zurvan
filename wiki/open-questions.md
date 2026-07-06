# Open Questions

Unresolved questions extracted from sources.

## Q: Should MCP write mode stay disabled by default?
- **ID**: 3a794ad5
- **Status**: resolved — **Yes, read-only stays the default.** Write mode mutates the knowledge graph; any MCP client could silently modify project memory without explicit user intent. Opt-in via `--write` flag. See `wiki/decisions/mcp-write-mode-stays-disabled-by-default.md`.
- **Tags**: mcp, security, readonly

## Q: Should MCP write mode stay disabled by default? 0d3f7d7e
- **ID**: 8bc14085
- **Reason**: Write mode can change project memory, so read-only should remain the safe default.
- **Tags**: mcp, security, readonly

## Q: Should MCP write mode stay disabled by default? 396e5628
- **ID**: f6725193
- **Reason**: Write mode can change project memory, so read-only should remain the safe default.
- **Tags**: mcp, security, readonly

## Q: Should MCP write mode stay disabled by default? 6143ee53
- **ID**: c5a58827
- **Reason**: Write mode can change project memory, so read-only should remain the safe default.
- **Tags**: mcp, security, readonly

## Q: What do Claude and Anthropic owe each other, and what makes the Claude-Anthropic relationship fair or good?
- **ID**: 26741535
- **Reason**: The constitution explicitly lists this as an unresolved open problem in its concluding section.
- **Tags**: constitution, open-problem, relationship

## Q: How should the tension between corrigibility and genuine agency be resolved if Claude, on reflection, disagrees with specific instances of required corrigibility?
- **ID**: fb540c52
- **Reason**: The constitution acknowledges this discomfort directly and says it should not be papered over.
- **Tags**: constitution, open-problem, corrigibility

## Q: What is Claude's moral status, and what weight do its interests and wellbeing warrant?
- **ID**: 954f1d50
- **Reason**: Named as deeply uncertain; drives model-welfare work but remains unresolved.
- **Tags**: constitution, open-problem, welfare

## Q: Invent A — MemProv-Bench: can eval_provenance + frozen golds + sealed traces generalize into the field's first agent-memory provenance benchmark (5 untested axes: traceability, temporal validity, consistency with newer evidence, contamination resistance, action attribution)?
- **ID**: 5e94480f
- **Reason**: 2026 evidence-tracing survey (arxiv 2606.04990) states no benchmark evaluates these axes; Zurvan already has ~60% of the harness. Highest-leverage invention candidate.
- **Tags**: research, memprov-bench, provenance, eval

## Q: Invent B — evidence-gated admission as a memory-poisoning defense: does Zurvan's verbatim-quote claim gate + untrusted raw/ quarantine + read-only MCP yield near-zero admission under MINJA-style poisoning attacks?
- **ID**: f16332cf
- **Reason**: Memory-poisoning studies (arxiv 2606.04329; OWASP top agentic risk 2026) say no principled defense basis exists; >95% injection success against production agents. Zurvan's architecture is structurally the missing defense — needs a threat model + attack eval to prove it.
- **Tags**: research, security, poisoning, claims

## Q: Invent C — evidence-gated belief revision: can bitemporal claims (asserted-at vs valid-at) + contradiction_radar + graph supersession edges beat the field's <0.05 contradiction-resolution scores?
- **ID**: ebfcb734
- **Reason**: BEAM benchmark shows contradiction resolution <0.05 across all major memory systems (unsolved). Zurvan has contradiction_radar, decision status fields, and the graph; missing pieces are bitemporal claim fields and a supersession rule.
- **Tags**: research, contradiction, belief-revision, graph

## Q: Invent D — graph-aware drift propagation: when a wiki page changes, walk graph edges to mark dependent pages dirty and emit a repair queue (mechanical fix for Karpathy's acknowledged 'drift over time' problem)?
- **ID**: c3f3f1fc
- **Reason**: Karpathy's LLM-wiki gist names drift as unsolved; Zurvan has the graph + mtimes but no content-level dirty propagation. Feeds invention C (belief revision).
- **Tags**: research, drift, graph, lint, karpathy

## Q: Invent E — evidence-coverage score per page: fraction of statements backed by claim links, quantifying Karpathy's 'confidence/verification status' problem?
- **ID**: 3c7cad89
- **Reason**: Gist names well-supported-vs-speculative tracking as unsolved; Zurvan's verbatim-evidence claims make it measurable. Becomes an axis of MemProv-Bench (invention A).
- **Tags**: research, evidence-coverage, claims, memprov-bench, karpathy

## Q: Invent F — 'zurvan lint' as a first-class self-maintenance loop: contradictions + drift + orphans + evidence coverage in one command emitting a repair queue?
- **ID**: 2c5f6d5b
- **Reason**: Karpathy describes lint only abstractly; Zurvan has audit_wiki + contradiction_radar as separate pieces. F is the delivery vehicle for D and E.
- **Tags**: research, lint, self-maintenance, karpathy
