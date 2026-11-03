"""The one place a quotation is admitted as evidence.

Before this module (audit 2026-10-22, F2) each path checked `quote in source`
on its own, and the empty string passes that test: `memory.add_claim`, the MCP
`zurvan_claim_add` handler and the extraction validator all minted claims with
no evidence behind them. Every path now calls `admit_quote`.

What the check means: the quote exists in the source, byte for byte. It does
not mean the quote supports the claim — membership is not entailment, and no
normalisation (whitespace, NBSP, PDF ligatures) is applied on either side,
because a quote that is only in the source after normalisation is not the
source's text.
"""


class EvidenceRejected(ValueError):
    """The quotation cannot be admitted as evidence."""


def admit_quote(quote: str | None, source_text: str) -> str:
    """Return `quote` unchanged if it is non-blank and appears verbatim in
    `source_text`; raise `EvidenceRejected` otherwise."""
    if quote is None or not quote.strip():
        raise EvidenceRejected("evidence quote is empty")
    if quote not in source_text:
        raise EvidenceRejected("evidence quote not found verbatim in source")
    return quote
