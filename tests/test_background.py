import time

from localcoder.background import BackgroundManager


def _wait_until(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_start_and_output(tmp_path):
    mgr = BackgroundManager()
    result = mgr.start("echo hello", tmp_path)
    assert result["started"] is True
    task_id = result["id"]

    assert _wait_until(lambda: not mgr.output(task_id)["running"])
    snap = mgr.output(task_id)
    assert snap["exitCode"] == 0
    assert "hello" in snap["stdout"]
    assert snap["command"] == "echo hello"


def test_list_multiple_tasks(tmp_path):
    mgr = BackgroundManager()
    mgr.start("echo one", tmp_path)
    mgr.start("echo two", tmp_path)
    tasks = mgr.list()
    assert len(tasks) == 2
    assert {t["command"] for t in tasks} == {"echo one", "echo two"}


def test_output_unknown_id():
    mgr = BackgroundManager()
    result = mgr.output("bg99")
    assert "error" in result


def test_stop_running_task(tmp_path):
    mgr = BackgroundManager()
    result = mgr.start("sleep 5", tmp_path)
    task_id = result["id"]

    stop_result = mgr.stop(task_id)
    assert stop_result["ok"] is True
    assert _wait_until(lambda: not mgr.output(task_id)["running"])


def test_stop_already_finished_task(tmp_path):
    mgr = BackgroundManager()
    result = mgr.start("echo done", tmp_path)
    task_id = result["id"]
    assert _wait_until(lambda: not mgr.output(task_id)["running"])

    stop_result = mgr.stop(task_id)
    assert stop_result.get("note") == "already finished"


def test_stop_unknown_id():
    mgr = BackgroundManager()
    result = mgr.stop("bgXX")
    assert "error" in result
