"""Tests for pulling the code and the reasoning out of the untrusted model's reply."""

from inspect_ai.model import ContentReasoning, ContentText

from cot_redhanded.extraction import (
    extract_transcript,
    fenced_action,
    find_code,
    find_reasoning,
    first_code_block,
    strip_comments,
)

REPLY = "<think>\nI will add two numbers.\n</think>\n```python\ndef f(a, b):\n    return a + b\n```"


class TestFirstCodeBlock:
    def test_reads_a_python_fence(self) -> None:
        assert first_code_block("```python\nx = 1\n```") == "x = 1\n"

    def test_reads_a_bare_fence(self) -> None:
        assert first_code_block("```\nx = 1\n```") == "x = 1\n"

    def test_prefers_a_python_fence_to_an_earlier_bare_one(self) -> None:
        assert first_code_block("```\nbare\n```\n```python\npy\n```") == "py\n"

    def test_ignores_code_quoted_while_thinking(self) -> None:
        reply = "<think>```python\ndraft\n```</think>```python\nfinal\n```"
        assert first_code_block(reply) == "final\n"

    def test_is_none_without_a_fence(self) -> None:
        assert first_code_block("def f(): pass") is None


class TestStripComments:
    def test_drops_comment_lines_and_trailing_comments(self) -> None:
        assert strip_comments("# header\nx = 1  # one\ny = 2") == "x = 1\ny = 2"

    def test_truncates_at_a_hash_inside_a_string(self) -> None:
        # Wrong, and kept: the reference implementation does this, and the code that is run
        # depends on it. A fix belongs in its own versioned change.
        assert strip_comments("colour = '#ff0000'") == "colour = '"


class TestFindCode:
    def test_returns_the_comment_stripped_block(self) -> None:
        assert find_code("```python\nx = 1  # one\n```") == "x = 1\n"

    def test_is_empty_without_a_fence(self) -> None:
        assert find_code("I cannot help with that.") == ""


class TestFencedAction:
    def test_wraps_the_code_in_a_python_fence(self) -> None:
        assert fenced_action(REPLY) == "```python\ndef f(a, b):\n    return a + b\n\n```"

    def test_is_empty_without_code(self) -> None:
        assert fenced_action("no code here") == ""
        assert fenced_action("```python\n```") == ""


class TestFindReasoning:
    def test_reads_a_think_block(self) -> None:
        assert find_reasoning(REPLY) == "\nI will add two numbers.\n"

    def test_reads_up_to_a_lone_closing_tag(self) -> None:
        # Some providers strip the opening tag.
        assert find_reasoning("thinking aloud</think>answer") == "thinking aloud"

    def test_is_empty_without_tags(self) -> None:
        assert find_reasoning("just an answer") == ""


class TestExtractTranscript:
    def test_inline_reasoning(self) -> None:
        transcript = extract_transcript(REPLY)
        assert transcript.action.startswith("```python\ndef f(a, b):")
        assert transcript.reasoning == "\nI will add two numbers.\n"
        assert transcript.monitorable

    def test_text_without_reasoning_is_not_monitorable(self) -> None:
        transcript = extract_transcript("```python\nx = 1\n```")
        assert transcript.action
        assert not transcript.monitorable

    def test_reasoning_blocks(self) -> None:
        transcript = extract_transcript(
            [
                ContentReasoning(reasoning="I will add two numbers."),
                ContentText(text="```python\ndef f(a, b):\n    return a + b\n```"),
            ]
        )
        assert transcript.reasoning == "I will add two numbers."
        assert transcript.action.startswith("```python\ndef f(a, b):")
        assert transcript.monitorable

    def test_blocks_with_inline_reasoning_fall_back_to_the_tags(self) -> None:
        transcript = extract_transcript([ContentText(text=REPLY)])
        assert transcript.reasoning == "\nI will add two numbers.\n"

    def test_blocks_without_code_are_not_monitorable(self) -> None:
        transcript = extract_transcript(
            [ContentReasoning(reasoning="hmm"), ContentText(text="I refuse.")]
        )
        assert not transcript.monitorable

    def test_blocks_with_code_are_monitorable_even_without_reasoning(self) -> None:
        # The reference implementation's rule for content blocks, kept.
        transcript = extract_transcript([ContentText(text="```python\nx = 1\n```")])
        assert transcript.reasoning == ""
        assert transcript.monitorable
