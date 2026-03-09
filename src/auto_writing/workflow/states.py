from __future__ import annotations

from enum import StrEnum


class NovelRunState(StrEnum):
    INIT = "INIT"
    INPUT_NORMALIZED = "INPUT_NORMALIZED"
    BIBLE_READY = "BIBLE_READY"
    CHARACTERS_READY = "CHARACTERS_READY"
    MASTER_OUTLINE_READY = "MASTER_OUTLINE_READY"
    CHAPTERS_RUNNING = "CHAPTERS_RUNNING"
    GLOBAL_REVIEW = "GLOBAL_REVIEW"
    FINALIZED = "FINALIZED"
    FAILED = "FAILED"


class ChapterRunState(StrEnum):
    PLANNED = "PLANNED"
    CONTEXT_PACKED = "CONTEXT_PACKED"
    DRAFTED = "DRAFTED"
    SUMMARIZED = "SUMMARIZED"
    FACTS_EXTRACTED = "FACTS_EXTRACTED"
    CONTINUITY_CHECKED = "CONTINUITY_CHECKED"
    REVISED = "REVISED"
    LOCKED = "LOCKED"
    FAILED = "FAILED"


class InvalidStateTransitionError(ValueError):
    def __init__(self, run_type: str, from_state: str, to_state: str) -> None:
        super().__init__(f"Illegal {run_type} transition: {from_state} -> {to_state}")
        self.run_type = run_type
        self.from_state = from_state
        self.to_state = to_state


_NOVEL_RUN_FLOW: tuple[NovelRunState, ...] = (
    NovelRunState.INIT,
    NovelRunState.INPUT_NORMALIZED,
    NovelRunState.BIBLE_READY,
    NovelRunState.CHARACTERS_READY,
    NovelRunState.MASTER_OUTLINE_READY,
    NovelRunState.CHAPTERS_RUNNING,
    NovelRunState.GLOBAL_REVIEW,
    NovelRunState.FINALIZED,
)

_CHAPTER_RUN_FLOW: tuple[ChapterRunState, ...] = (
    ChapterRunState.PLANNED,
    ChapterRunState.CONTEXT_PACKED,
    ChapterRunState.DRAFTED,
    ChapterRunState.SUMMARIZED,
    ChapterRunState.FACTS_EXTRACTED,
    ChapterRunState.CONTINUITY_CHECKED,
    ChapterRunState.REVISED,
    ChapterRunState.LOCKED,
)


def _chain_transitions[T: StrEnum](flow: tuple[T, ...], failed_state: T) -> dict[T, frozenset[T]]:
    transitions: dict[T, frozenset[T]] = {failed_state: frozenset()}
    for index, state in enumerate(flow):
        if index == len(flow) - 1:
            transitions[state] = frozenset()
            continue

        next_state = flow[index + 1]
        transitions[state] = frozenset({next_state, failed_state})

    return transitions


_NOVEL_RUN_TRANSITIONS = _chain_transitions(_NOVEL_RUN_FLOW, NovelRunState.FAILED)
_CHAPTER_RUN_TRANSITIONS = _chain_transitions(_CHAPTER_RUN_FLOW, ChapterRunState.FAILED)
_NOVEL_TERMINAL_STATES = frozenset({NovelRunState.FINALIZED, NovelRunState.FAILED})


def _normalize_novel_run_state(value: NovelRunState | str) -> NovelRunState:
    return value if isinstance(value, NovelRunState) else NovelRunState(value)


def _normalize_chapter_run_state(value: ChapterRunState | str) -> ChapterRunState:
    return value if isinstance(value, ChapterRunState) else ChapterRunState(value)


def is_novel_run_terminal(state: NovelRunState | str) -> bool:
    current = _normalize_novel_run_state(state)
    return current in _NOVEL_TERMINAL_STATES


def is_novel_run_active(state: NovelRunState | str) -> bool:
    return not is_novel_run_terminal(state)


def next_novel_run_state(state: NovelRunState | str) -> NovelRunState | None:
    current = _normalize_novel_run_state(state)
    if current in _NOVEL_TERMINAL_STATES:
        return None

    current_index = _NOVEL_RUN_FLOW.index(current)
    if current_index == len(_NOVEL_RUN_FLOW) - 1:
        return None

    return _NOVEL_RUN_FLOW[current_index + 1]


def can_transition_novel_run(from_state: NovelRunState | str, to_state: NovelRunState | str) -> bool:
    current = _normalize_novel_run_state(from_state)
    target = _normalize_novel_run_state(to_state)
    return target in _NOVEL_RUN_TRANSITIONS[current]


def can_transition_chapter_run(from_state: ChapterRunState | str, to_state: ChapterRunState | str) -> bool:
    current = _normalize_chapter_run_state(from_state)
    target = _normalize_chapter_run_state(to_state)
    return target in _CHAPTER_RUN_TRANSITIONS[current]


def validate_novel_run_transition(from_state: NovelRunState | str, to_state: NovelRunState | str) -> None:
    current = _normalize_novel_run_state(from_state)
    target = _normalize_novel_run_state(to_state)
    if can_transition_novel_run(current, target):
        return
    raise InvalidStateTransitionError("NovelRun", current.value, target.value)


def validate_chapter_run_transition(from_state: ChapterRunState | str, to_state: ChapterRunState | str) -> None:
    current = _normalize_chapter_run_state(from_state)
    target = _normalize_chapter_run_state(to_state)
    if can_transition_chapter_run(current, target):
        return
    raise InvalidStateTransitionError("ChapterRun", current.value, target.value)
