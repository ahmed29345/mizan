"""Split article text into chunks for embedding.

Trade-off: chunks too BIG -> the vector is a blurry average and the LLM prompt gets long;
chunks too SMALL -> a chunk loses the context it needs to make sense. We split on sentence
boundaries (never mid-sentence), pack sentences up to `max_chars`, and repeat the last
sentence at the start of the next chunk (overlap) so an idea split across chunks isn't lost.
"""

import re

# Sentence ends: . ! ? and the Arabic question mark ؟, followed by whitespace, or a newline.
_SENTENCE_END = re.compile(r"(?<=[.!?؟])\s+|\n+")


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]


def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    """Last resort for a single sentence longer than max_chars: cut at word boundaries."""
    pieces: list[str] = []
    current = ""
    for word in sentence.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            pieces.append(current)
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text: str, *, max_chars: int = 800, overlap_sentences: int = 1) -> list[str]:
    """Characters, not tokens: no tokenizer needed, and 800 chars is far below bge-m3's limit."""
    sentences: list[str] = []
    for sentence in split_sentences(text):
        sentences.extend(_split_long_sentence(sentence, max_chars))

    chunks: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if current and len(" ".join([*current, sentence])) > max_chars:
            chunks.append(" ".join(current))
            # Carry the last sentence(s) over, unless that alone would overflow the next chunk.
            carry = current[-overlap_sentences:] if overlap_sentences else []
            if len(" ".join([*carry, sentence])) > max_chars:
                carry = []
            current = [*carry]
        current.append(sentence)
    if current:
        chunks.append(" ".join(current))
    return chunks


def build_embedding_text(
    *,
    regulation_code: str,
    regulation_title: str,
    article_number: str,
    heading: str | None,
    content: str,
) -> str:
    """Contextual header: "DEMO-PDPL | Personal Data... | Article 29: Cross-border transfer".

    A chunk like "within 24 hours" means little alone. Prefixing WHERE it comes from makes the
    vector carry that context too, so "SAMA incident reporting" finds it. We embed this text
    but store/show only `content`.
    """
    title = f"Article {article_number}" + (f": {heading}" if heading else "")
    return f"{regulation_code} | {regulation_title} | {title}\n\n{content}"
