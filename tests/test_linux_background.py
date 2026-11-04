"""Linux background clicks: press a button through AT-SPI, never a text field."""

from types import SimpleNamespace

from dobby.perception.atspi import ATSPIInspector


class Action:
    def __init__(self, names):
        self.names, self.done = names, []

    def get_n_actions(self):
        return len(self.names)

    def get_action_name(self, i):
        return self.names[i]

    def do_action(self, i):
        self.done.append(self.names[i])
        return True


class Node:
    def __init__(self, role, parent=None, actions=(), enabled=True):
        self.role, self.parent, self.enabled = role, parent, enabled
        self.action = Action(list(actions)) if actions else None

    def get_role_name(self):
        return self.role

    def get_parent(self):
        return self.parent

    def get_action_iface(self):
        return self.action


def _inspector():
    insp = ATSPIInspector.__new__(ATSPIInspector)
    insp._states = lambda node: ["enabled" if node.enabled else "disabled"]
    return insp


def test_presses_a_button_through_its_click_action():
    button = Node("push button", actions=("click",))
    assert _inspector()._press(button) == "push button"
    assert button.action.done == ["click"]


def test_walks_up_from_the_label_to_the_button():
    button = Node("push button", actions=("click",))
    label = Node("label", parent=button)
    assert _inspector()._press(label) == "push button"


def test_never_presses_text_fields_or_disabled_buttons():
    assert _inspector()._press(Node("text", actions=("activate",))) is None
    off = Node("push button", actions=("click",), enabled=False)
    assert _inspector()._press(off) is None and off.action.done == []


def test_label_inside_a_panel_is_not_a_press():
    panel = Node("panel")
    assert _inspector()._press(Node("label", parent=panel)) is None
