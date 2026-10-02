"""Unit tests for Autonomous Chain-of-Thought (CoT) and Multi-Instance Window Indexing."""

from pathlib import Path
import pytest
from app.tools.desktop.window_manager import WindowManager, WindowInfo
from app.tools.system.file_ops import file_manager
from app.workers.cot_worker import cot_worker, COT_TOOLS, COT_TOOL_MAP


def test_classify_window():
    wm = WindowManager()
    assert wm.classify_window("WindowsTerminal.exe", "CASCADIA_HOSTING_WINDOW_CLASS", "pwsh") == "terminal"
    assert wm.classify_window("powershell.exe", "ConsoleWindowClass", "Windows PowerShell") == "terminal"
    assert wm.classify_window("cmd.exe", "ConsoleWindowClass", "Command Prompt") == "terminal"
    assert wm.classify_window("explorer.exe", "CabinetWClass", "voice-desktop - File Explorer") == "explorer"
    assert wm.classify_window("brave.exe", "Chrome_WidgetWin_1", "OpenRouter") == "browser"
    assert wm.classify_window("chrome.exe", "Chrome_WidgetWin_1", "Google") == "browser"
    assert wm.classify_window("Code.exe", "Chrome_WidgetWin_1", "app.py - VS Code") == "code"
    assert wm.classify_window("notepad.exe", "Notepad", "Untitled - Notepad") == "editor"


def test_window_indexing_mock(monkeypatch):
    wm = WindowManager()
    # Mock list_windows to return deterministic test windows
    mock_windows = [
        WindowInfo(hwnd=101, title="Terminal A", process_name="WindowsTerminal.exe", pid=1, rect=(0,0,100,100), width=100, height=100, is_minimized=False, is_active=True, class_name="CASCADIA_HOSTING_WINDOW_CLASS"),
        WindowInfo(hwnd=102, title="Browser 1", process_name="chrome.exe", pid=2, rect=(0,0,100,100), width=100, height=100, is_minimized=False, is_active=False, class_name="Chrome_WidgetWin_1"),
        WindowInfo(hwnd=103, title="Terminal B", process_name="WindowsTerminal.exe", pid=3, rect=(0,0,100,100), width=100, height=100, is_minimized=False, is_active=False, class_name="CASCADIA_HOSTING_WINDOW_CLASS"),
        WindowInfo(hwnd=104, title="Folder X", process_name="explorer.exe", pid=4, rect=(0,0,100,100), width=100, height=100, is_minimized=False, is_active=False, class_name="CabinetWClass"),
        WindowInfo(hwnd=105, title="Folder Y", process_name="explorer.exe", pid=5, rect=(0,0,100,100), width=100, height=100, is_minimized=False, is_active=False, class_name="CabinetWClass"),
    ]

    # Assign aliases as list_windows does
    category_counts = {}
    for w in mock_windows:
        cat = wm.classify_window(w.process_name, w.class_name, w.title)
        count = category_counts.get(cat, 0) + 1
        category_counts[cat] = count
        w.category = cat
        w.index = count
        w.alias = f"{cat} {count}"

    monkeypatch.setattr(wm, "list_windows", lambda: mock_windows)

    # Test direct alias resolution
    t1 = wm.find_window("terminal 1")
    assert t1 is not None and t1.hwnd == 101 and t1.alias == "terminal 1"

    t2 = wm.find_window("terminal 2")
    assert t2 is not None and t2.hwnd == 103 and t2.alias == "terminal 2"

    # Test ordinal and word variations
    t_two = wm.find_window("terminal two")
    assert t_two is not None and t_two.hwnd == 103

    t_second = wm.find_window("second terminal")
    assert t_second is not None and t_second.hwnd == 103

    # Test explorer indexing
    exp1 = wm.find_window("explorer 1")
    assert exp1 is not None and exp1.hwnd == 104 and exp1.alias == "explorer 1"

    exp2 = wm.find_window("explorer 2")
    assert exp2 is not None and exp2.hwnd == 105 and exp2.alias == "explorer 2"


def test_find_directory():
    res = file_manager.find_directory("voice-desktop")
    assert res.get("success") is True
    assert "voice-desktop" in res.get("best_match", "").lower()


def test_cot_tools_map():
    assert "find_directory" in COT_TOOL_MAP
    assert "open_terminal_in_directory" in COT_TOOL_MAP
    assert "focus_window" in COT_TOOL_MAP
    assert "write_to_window" in COT_TOOL_MAP
    assert "list_open_windows" in COT_TOOL_MAP
    assert "remember_fact" in COT_TOOL_MAP
    assert "recall_facts" in COT_TOOL_MAP
    assert "get_directory_tree" in COT_TOOL_MAP
    assert "grep_code" in COT_TOOL_MAP
    assert "get_git_status" in COT_TOOL_MAP
    assert "execute_python_code" in COT_TOOL_MAP
    assert len(COT_TOOLS) >= 12


def test_memory_store():
    from app.tools.system.memory import MemoryStore
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryStore(storage_path=Path(tmp) / "test_mem.json")
        mem.remember("test_agent_key", "test_value_123", category="test")
        recalled = mem.recall("test_agent_key")
        assert len(recalled) == 1
        assert recalled[0]["value"] == "test_value_123"
        assert mem.forget("test_agent_key") is True
        assert len(mem.recall("test_agent_key")) == 0


def test_developer_ops():
    from app.tools.system.developer_ops import dev_ops
    tree = dev_ops.get_directory_tree(".", max_depth=1)
    assert "app/" in tree or "tests/" in tree

    res = dev_ops.execute_python_code("print('harness_ok')")
    assert "harness_ok" in res

    git_res = dev_ops.get_git_status(".")
    assert "Git Repo" in git_res
