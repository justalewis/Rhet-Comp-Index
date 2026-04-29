"""Characterization tests for tagger.auto_tag — controlled-vocabulary
matching. The vocabulary is large; we exercise representative single-word
triggers, multi-word phrases, and word-boundary edge cases."""

import pytest

from tagger import auto_tag


def test_auto_tag_empty_inputs_returns_none():
    assert auto_tag(None, None) is None
    assert auto_tag("", "") is None
    assert auto_tag("   ", "") is None


def test_auto_tag_no_match_returns_none():
    assert auto_tag("Quokkas at sunset", "A meditation on Australian wildlife.") is None


def test_auto_tag_returns_pipe_delimited_with_outer_pipes():
    result = auto_tag("Composition theory revisited", None)
    assert result is not None
    assert result.startswith("|")
    assert result.endswith("|")


def test_auto_tag_simple_match():
    result = auto_tag("Notes on revision", None)
    assert "|revision|" in result


def test_auto_tag_multi_word_phrase():
    result = auto_tag("First-year composition pedagogy", None)
    assert "|first-year composition|" in result


def test_auto_tag_combines_title_and_abstract():
    result = auto_tag(
        "Untitled essay",
        "This piece engages digital rhetoric and disability studies.",
    )
    assert "|digital rhetoric|" in result
    assert "|disability studies|" in result


def test_auto_tag_each_tag_only_once():
    """Two trigger phrases for the same tag must not produce a duplicate."""
    result = auto_tag(
        "Revision and peer response in undergraduate writing",
        "Revision strategies and feedback on writing combined.",
    )
    # Only one |revision| occurrence
    assert result.count("|revision|") == 1


def test_auto_tag_word_boundary_no_false_positive():
    """'grammar' must not fire on 'programmatic' or 'programmer'."""
    result = auto_tag("Programmatic assessment in WPA work", None)
    # 'grammar' is in the VOCAB — verify it does NOT fire
    if result:
        assert "|grammar|" not in result


@pytest.mark.parametrize("title", [
    "An empirical study of revision strategies",
    "Notes on writing assessment",
    "Genre theory and the digital classroom",
])
def test_auto_tag_handles_various_titles(title):
    result = auto_tag(title, None)
    # Each title is intentionally written to hit a vocabulary trigger.
    assert result is not None
    assert result.startswith("|")
