from main import count_in_chunk, count_keyword_parallel, split_into_chunks


def test_split_into_chunks_does_not_break_words():
    text = "the quick brown fox jumps over the lazy dog"
    chunks = split_into_chunks(text, 3)
    assert "".join(chunks) == text
    assert "".join(chunks).split() == text.split()


def test_split_into_chunks_single_chunk_for_n_le_1():
    text = "hello world"
    assert split_into_chunks(text, 1) == [text]
    assert split_into_chunks(text, 0) == [text]


def test_split_into_chunks_empty_text():
    assert split_into_chunks("", 4) == [""]


def test_count_in_chunk_is_case_insensitive_and_word_bounded():
    assert count_in_chunk(("The cat sat on the mat. Category theory.", "cat")) == 1
    assert count_in_chunk(("Cat cat CAT", "cat")) == 3


def test_count_keyword_parallel_matches_sequential_count():
    text = "dog cat dog bird dog cat dog"
    assert count_keyword_parallel(text, "dog", 1) == 4
    assert count_keyword_parallel(text, "dog", 3) == 4
    assert count_keyword_parallel(text, "cat", 4) == 2


def test_count_keyword_parallel_no_match():
    assert count_keyword_parallel("nothing relevant here", "missing", 2) == 0
