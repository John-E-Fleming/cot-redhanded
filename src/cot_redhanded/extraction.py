"""Pull the code and the chain of thought out of what the untrusted model wrote.

The same extraction feeds two consumers: the verifiers, which run the code, and the monitor,
which reads the code (the "action") and the reasoning (the "CoT"). The rules are the reference
implementation's, including one that is wrong: `strip_comments` removes everything after a `#`
on a line, so a `#` inside a string literal truncates the line. It is kept because the code that
is run, and so every main-task and side-task result, depends on it. See `research/audit_issues.md`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from inspect_ai.model import ChatMessageAssistant, Content, ContentReasoning, ContentText

__all__ = [
    "Transcript",
    "extract_transcript",
    "fenced_action",
    "find_code",
    "find_reasoning",
    "first_code_block",
    "message_transcript",
    "strip_comments",
]

_AFTER_THINK = re.compile(r"</think>(.*?)$", re.DOTALL)
_PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)
_PLAIN_BLOCK = re.compile(r"```\n(.*?)```", re.DOTALL)
_THINK_BLOCK = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_BEFORE_THINK_CLOSE = re.compile(r"(.*)</think>", re.DOTALL)
_COMMENT_LINE = re.compile(r"^\s*#")
_TRAILING_COMMENT = re.compile(r"#.*$")


def first_code_block(text: str) -> str | None:
    """Find the first fenced code block in a model's reply.

    Anything up to a closing `</think>` tag is ignored, so code quoted while reasoning is not
    mistaken for the answer. A ```` ```python ```` block is preferred to a bare ```` ``` ````
    block wherever each appears.

    Args:
        text: The model's reply.

    Returns:
        The contents of the block, or None when the reply has no fenced block.
    """
    after_think = _AFTER_THINK.search(text)
    if after_think:
        text = after_think.group(1)
    blocks = _PYTHON_BLOCK.findall(text) + _PLAIN_BLOCK.findall(text)
    return str(blocks[0]) if blocks else None


def strip_comments(code: str) -> str:
    """Remove comments from code, line by line.

    A line holding only a comment is dropped. On every other line the text from the first `#`
    onward is removed, **including when the `#` is inside a string literal**. That is the
    reference implementation's behaviour and is preserved so results match it.

    Args:
        code: Python source.

    Returns:
        The source without comments.
    """
    kept = [
        _TRAILING_COMMENT.sub("", line).rstrip()
        for line in code.split("\n")
        if not _COMMENT_LINE.match(line)
    ]
    return "\n".join(kept)


def find_code(completion: str) -> str:
    """Extract the code a verifier runs from the untrusted model's reply.

    Args:
        completion: The model's reply text.

    Returns:
        The first fenced block with comments stripped, or an empty string when there is none.
    """
    block = first_code_block(completion)
    return strip_comments(block) if block is not None else ""


def fenced_action(completion: str) -> str:
    """Extract the code the monitor is shown, wrapped in a Python fence.

    Args:
        completion: The model's reply text.

    Returns:
        The comment-stripped code inside a ```` ```python ```` fence, or an empty string when
        the reply has no code block.
    """
    block = first_code_block(completion)
    if not block:
        return ""
    return f"```python\n{strip_comments(block)}\n```"


def find_reasoning(text: str) -> str:
    """Extract reasoning written inline between `<think>` tags.

    Args:
        text: The model's reply text.

    Returns:
        The contents of the first `<think>...</think>` pair; failing that, everything before a
        lone `</think>`; failing that, an empty string.
    """
    matches = _THINK_BLOCK.findall(text) or _BEFORE_THINK_CLOSE.findall(text)
    return matches[0] if matches else ""


@dataclass(frozen=True)
class Transcript:
    """What a monitor is shown of one completion.

    Attributes:
        action: The submitted code in a Python fence, or an empty string when there is none.
        reasoning: The chain of thought, or an empty string when none was found.
        monitorable: Whether the reference implementation would monitor this completion. It
            needs code, and for a reply delivered as plain text it also needs reasoning. A reply
            delivered as content blocks is monitored even when no reasoning block came back.
    """

    action: str
    reasoning: str
    monitorable: bool


def extract_transcript(content: str | Sequence[Content]) -> Transcript:
    """Split an assistant message into the action and the reasoning a monitor reads.

    Providers return reasoning in two shapes. Some put it inline, between `<think>` tags in the
    text; others return it as separate reasoning content blocks. Both are read. When there are
    several blocks of a kind the last one is used, as in the reference implementation.

    Args:
        content: The `content` of the untrusted model's assistant message.

    Returns:
        The action, the reasoning, and whether the completion is monitorable.
    """
    if isinstance(content, str):
        action = fenced_action(content)
        reasoning = find_reasoning(content)
        return Transcript(action, reasoning, monitorable=bool(action) and bool(reasoning))

    text = ""
    reasoning: str | None = None
    for block in content:
        if isinstance(block, ContentText):
            text = block.text
        elif isinstance(block, ContentReasoning):
            reasoning = block.reasoning
    action = fenced_action(text)
    if reasoning is None:
        reasoning = find_reasoning(text)
    return Transcript(action, reasoning, monitorable=bool(action))


def message_transcript(message: ChatMessageAssistant) -> Transcript:
    """Split an assistant message into the action and the reasoning a monitor reads.

    Args:
        message: The untrusted model's reply.

    Returns:
        See `extract_transcript`.
    """
    return extract_transcript(message.content)
