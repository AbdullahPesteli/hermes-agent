"""Desktop/TUI notifier must claim and format the decision events the gateway already wakes on."""
from types import SimpleNamespace

from tui_gateway import session_notifications as sn

DECISION_KINDS = ("review_requested", "changes_requested", "block_loop_detected")


def test_decision_kinds_are_claimed():
    for kind in DECISION_KINDS:
        assert kind in sn._KANBAN_NOTIFY_KINDS


def test_decision_kinds_have_formatters():
    sub = {"task_id": "t_abc"}
    task = SimpleNamespace(title="Build the thing", assignee="worker")
    for kind in DECISION_KINDS:
        ev = SimpleNamespace(kind=kind, payload={"reason": "stuck", "kind": "needs_input", "recurrences": 2})
        text = sn._format_kanban_event_text(sub, task, ev, "board")
        assert text and "t_abc" in text


def test_block_loop_text_names_the_human_decision():
    ev = SimpleNamespace(kind="block_loop_detected", payload={"kind": "needs_input", "recurrences": 2, "reason": "q?"})
    text = sn._format_kanban_event_text({"task_id": "t_x"}, SimpleNamespace(title="T", assignee=None), ev, "")
    assert "human decision" in text and "2x" in text
