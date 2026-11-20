"""Unit tests for chunking: pure functions, no database, no model. Fast."""

from mizan.rag.chunking import build_embedding_text, chunk_text, split_sentences


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("One sentence. Two sentences.") == ["One sentence. Two sentences."]


def test_empty_text_has_no_chunks() -> None:
    assert chunk_text("   ") == []


def test_splits_on_english_and_arabic_sentence_ends() -> None:
    text = "First rule. Second rule?\nهل يجوز ذلك؟ نعم."
    assert split_sentences(text) == ["First rule.", "Second rule?", "هل يجوز ذلك؟", "نعم."]


def test_long_text_respects_max_chars_and_overlaps() -> None:
    sentences = [f"Sentence number {i} has some words." for i in range(20)]

    chunks = chunk_text(" ".join(sentences), max_chars=120, overlap_sentences=1)

    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    # Overlap: each chunk starts with the last sentence of the previous one.
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.startswith(split_sentences(previous)[-1])
    # Nothing lost: every sentence appears in some chunk.
    assert all(any(s in chunk for chunk in chunks) for s in sentences)


def test_single_huge_sentence_is_cut_at_word_boundaries() -> None:
    sentence = " ".join(["word"] * 100)  # 499 chars, no full stop

    chunks = chunk_text(sentence, max_chars=50, overlap_sentences=0)

    assert all(len(chunk) <= 50 for chunk in chunks)
    assert " ".join(chunks) == sentence


def test_embedding_text_has_context_header() -> None:
    text = build_embedding_text(
        regulation_code="DEMO-X",
        regulation_title="Example Rules",
        article_number="3.2",
        heading="Reporting",
        content="Report within 24 hours.",
    )
    assert text == "DEMO-X | Example Rules | Article 3.2: Reporting\n\nReport within 24 hours."
