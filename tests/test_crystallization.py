"""
Unit tests for Phase 3 GUI-to-Script Skill Crystallization Engine.
"""

from dobby.agent_loop.controller import FeedbackLoopController
from dobby.crystallization import (
    CorrelatedStep,
    CrystallizedSkillSpec,
    DynamicSkillRegistry,
    JournalCorrelator,
    TclTrajectoryCompiler,
)
from dobby.types import BoundingBox, WindowInfo


class MockHAL:
    def __init__(self):
        self.mouse_positions = []
        self.typed_text = []

    def get_screen_size(self):
        return 1920, 1080

    def capture_screen(self, region=None):
        from PIL import Image

        return Image.new("RGB", (100, 100), color="white")

    def mouse_click(self, x, y, button="left", click_count=1):
        self.mouse_positions.append((x, y, button, click_count))

    def mouse_move(self, x, y):
        pass

    def mouse_down(self, button="left"):
        pass

    def mouse_up(self, button="left"):
        pass

    def mouse_scroll(self, dx, dy):
        pass

    def keyboard_type(self, text, delay_ms=10.0):
        self.typed_text.append(text)

    def keyboard_press_key(self, key):
        pass

    def keyboard_release_key(self, key):
        pass

    def keyboard_hotkey(self, keys):
        pass

    def list_windows(self):
        return []

    def get_active_window(self):
        return WindowInfo(
            window_id="0x123",
            title="Vivado 2025.1 - my_proj",
            app_name="vivado",
            bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
            is_active=True,
        )

    def focus_window(self, window_id):
        return True


def test_journal_correlator_lifecycle(tmp_path):
    jou_file = tmp_path / "vivado.jou"
    jou_file.write_text("# Initial header\nopen_project my_proj.xpr\n")

    correlator = JournalCorrelator(str(jou_file))
    assert correlator.journal_path == str(jou_file)
    assert not correlator.is_recording

    correlator.start_recording()
    assert correlator.is_recording

    # Simulate GUI action appending to journal
    correlator.mark_pre_action()
    with open(jou_file, "a") as f:
        f.write("# Click flow navigator\n")
        f.write("launch_simulation -step all\n")
        f.write("gui_select_tab -name console\n")  # Should be filtered as noise

    step = correlator.harvest_post_quiescence(
        action_id="step_1",
        gui_intent="launch_simulation",
        ui_target_text="Run Simulation",
    )

    assert step.action_id == "step_1"
    assert step.gui_intent == "launch_simulation"
    assert "launch_simulation -step all" in step.journal_lines
    assert not any("gui_select_tab" in line for line in step.journal_lines)
    assert not any(line.startswith("#") for line in step.journal_lines)

    recorded = correlator.stop_recording()
    assert len(recorded) == 1
    assert not correlator.is_recording


def test_tcl_trajectory_compiler():
    compiler = TclTrajectoryCompiler()

    steps = [
        CorrelatedStep(
            action_id="act_1",
            gui_intent="select item 1",
            journal_lines=[
                "select_objects [get_nets clk_net]",
                "select_objects [get_nets rst_net]",
                "select_objects [get_nets data_bus]",
            ],
        ),
        CorrelatedStep(
            action_id="act_2",
            gui_intent="run sim",
            journal_lines=[
                "launch_runs impl_1 -jobs 4",
                "run 500ns",
                "highlight_objects [get_nets clk_net]",  # Should be stripped as noise
            ],
        ),
    ]

    spec = compiler.compile_trajectory(
        steps=steps,
        task_prompt="Run implementation and simulation for 500ns on impl_1",
        extracted_entities={
            "run_target": "impl_1",
            "sim_duration": "500ns",
        },
        skill_name="test_impl_and_sim",
    )

    assert spec.skill_name == "test_impl_and_sim"
    assert "run_target" in spec.parameters
    assert "sim_duration" in spec.parameters
    assert spec.parameters["run_target"]["default"] == "impl_1"

    # Redundant selections collapsed: only data_bus kept
    assert "select_objects [get_nets clk_net]" not in spec.tcl_template
    assert "select_objects [get_nets data_bus]" in spec.tcl_template

    # Parameterization applied
    assert "launch_runs {{run_target}} -jobs 4" in spec.tcl_template
    assert "run {{sim_duration}}" in spec.tcl_template

    # Sync barrier injected for launch_runs
    assert "wait_on_run {{run_target}}" in spec.tcl_template

    # Noise stripped
    assert "highlight_objects" not in spec.tcl_template


def test_dynamic_skill_registry_persistence_and_rendering(tmp_path):
    storage_file = tmp_path / "skills.json"
    registry = DynamicSkillRegistry(storage_path=str(storage_file))

    spec = CrystallizedSkillSpec(
        skill_name="run_sim_custom",
        description="Run custom simulation",
        app_target="vivado_2025.1",
        tcl_template="restart\nrun {{duration}}\nset_radix {{radix}} [get_waves {{signal}}]",
        parameters={
            "duration": {"type": "string", "default": "1000ns"},
            "radix": {"type": "string", "default": "hex"},
            "signal": {"type": "string", "default": "*"},
        },
        source_steps_count=3,
    )

    registry.register_skill(spec)
    assert storage_file.exists()

    # Test retrieval
    loaded_spec = registry.get_skill("run_sim_custom")
    assert loaded_spec is not None
    assert loaded_spec.skill_name == "run_sim_custom"

    # Test template rendering with defaults
    rendered_default = registry.render_script("run_sim_custom")
    assert "run 1000ns" in rendered_default
    assert "set_radix hex [get_waves *]" in rendered_default

    # Test template rendering with overrides
    rendered_custom = registry.render_script(
        "run_sim_custom",
        arguments={"duration": "50us", "radix": "unsigned", "signal": "tb_fifo/data_out"},
    )
    assert "run 50us" in rendered_custom
    assert "set_radix unsigned [get_waves tb_fifo/data_out]" in rendered_custom

    # Test reload from disk in fresh registry instance
    fresh_registry = DynamicSkillRegistry(storage_path=str(storage_file))
    assert len(fresh_registry.list_skills()) == 1
    assert fresh_registry.get_skill("run_sim_custom") is not None

    # Test deletion
    assert fresh_registry.delete_skill("run_sim_custom")
    assert len(fresh_registry.list_skills()) == 0


def test_controller_skill_crystallization_integration(tmp_path):
    hal = MockHAL()
    controller = FeedbackLoopController(hal)

    jou_file = tmp_path / "vivado_ctrl.jou"
    jou_file.write_text("# Start\n")
    controller.journal_correlator.set_journal_path(str(jou_file))

    controller.journal_correlator.start_recording()

    def _mock_action():
        with open(jou_file, "a") as f:
            f.write("current_time\n")

    res = controller.execute_with_verification(
        action_name="check_time",
        action_func=_mock_action,
        verify_delta=False,
    )

    assert res.success
    steps = controller.journal_correlator.stop_recording()
    assert len(steps) == 1
    assert "current_time" in steps[0].journal_lines
