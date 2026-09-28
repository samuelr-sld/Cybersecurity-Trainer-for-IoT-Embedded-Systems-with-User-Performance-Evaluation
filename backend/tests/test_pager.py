"""Unit tests for the generic output pager (app/pager.py).

Pure logic only — no WebSocket, no session, no command router. The
WebSocket-level integration (frame shapes, `strings` actually getting paged,
pager input never reaching `default_router.dispatch`) is covered separately
in tests/test_hack_pager.py.
"""

from __future__ import annotations

from app.pager import PAGER_PROMPT, Pager, PagerAction, page_size_for


def _lines(n: int) -> tuple[str, ...]:
    return tuple(f"line {i}" for i in range(n))


# --- page_size_for -----------------------------------------------------


def test_page_size_reserves_one_row_for_the_prompt() -> None:
    assert page_size_for(24) == 23


def test_page_size_never_drops_below_one() -> None:
    assert page_size_for(1) == 1
    assert page_size_for(0) == 1


# --- 1: output shorter than one page ------------------------------------


def test_output_shorter_than_one_page_all_returned_at_once() -> None:
    pager = Pager(lines=_lines(5), terminal_rows=24)
    page = pager.next_page()
    assert page == _lines(5)
    assert pager.has_more is False


# --- 2: output exactly one page -------------------------------------------


def test_output_exactly_one_page() -> None:
    pager = Pager(lines=_lines(23), terminal_rows=24)
    page = pager.next_page()
    assert page == _lines(23)
    assert pager.has_more is False


# --- 3: output spanning multiple pages -------------------------------------


def test_output_spanning_multiple_pages() -> None:
    pager = Pager(lines=_lines(50), terminal_rows=24)  # page_size = 23
    first = pager.next_page()
    assert first == _lines(23)
    assert pager.has_more is True

    second = pager.next_page()
    assert second == _lines(50)[23:46]
    assert pager.has_more is True

    third = pager.next_page()
    assert third == _lines(50)[46:50]
    assert pager.has_more is False


# --- 4 & 5: Enter/Space advance --------------------------------------------


def test_enter_and_space_classify_as_advance() -> None:
    for key in ("\r", "\n", "\r\n", " "):
        assert PagerAction.from_input(key) is PagerAction.ADVANCE


# --- 6 & 7: q / Ctrl+C exit -------------------------------------------------


def test_q_and_ctrl_c_classify_as_exit() -> None:
    for key in ("q", "Q", "\x03"):
        assert PagerAction.from_input(key) is PagerAction.EXIT


# --- 8: unrelated input does not advance ------------------------------------


def test_unrelated_input_is_ignored() -> None:
    for key in ("a", "1", "\t", "\x7f", "", "xyz", "\x1b"):
        assert PagerAction.from_input(key) is PagerAction.IGNORE


# --- 9: final page completes cleanly ---------------------------------------


def test_has_more_is_false_exactly_after_the_final_page() -> None:
    pager = Pager(lines=_lines(24), terminal_rows=24)  # page_size = 23
    pager.next_page()
    assert pager.has_more is True
    pager.next_page()
    assert pager.has_more is False


# --- 10: page size changes with terminal row count (resize) ----------------


def test_resize_changes_page_size_for_future_pages_only() -> None:
    pager = Pager(lines=_lines(10), terminal_rows=24)  # page_size = 23
    first = pager.next_page()
    assert first == _lines(10)  # fits in one page at this height
    assert pager.has_more is False

    pager2 = Pager(lines=_lines(10), terminal_rows=5)  # page_size = 4
    first_page = pager2.next_page()
    assert first_page == _lines(10)[:4]
    assert pager2.has_more is True

    pager2.resize(24)  # widen mid-pager
    rest = pager2.next_page()
    assert rest == _lines(10)[4:10]
    assert pager2.has_more is False


def test_resize_does_not_reflow_a_page_already_sent() -> None:
    pager = Pager(lines=_lines(10), terminal_rows=5)  # page_size = 4
    first_page = pager.next_page()
    assert len(first_page) == 4
    pager.resize(24)
    # position already advanced past the first 4 lines; resize cannot
    # un-send them or change what was already returned.
    assert pager.position == 4


# --- 11: empty output --------------------------------------------------


def test_empty_output() -> None:
    pager = Pager(lines=(), terminal_rows=24)
    page = pager.next_page()
    assert page == ()
    assert pager.has_more is False


# --- 12: output without a trailing newline ----------------------------------


def test_last_line_with_no_special_terminator_is_paged_normally() -> None:
    # CommandResult.lines never carries terminators (see app/commands/base.py)
    # — this pins that a line with no trailing whitespace of any kind is
    # handled exactly like any other.
    lines = _lines(30) + ("last line, no newline",)
    pager = Pager(lines=lines, terminal_rows=24)
    first = pager.next_page()
    assert first == lines[:23]
    second = pager.next_page()
    assert second == lines[23:]
    assert second[-1] == "last line, no newline"
    assert pager.has_more is False


def test_pager_prompt_text_is_stable() -> None:
    assert PAGER_PROMPT == "-- More -- Press Enter/Space for next page, q/Ctrl+C to exit"
