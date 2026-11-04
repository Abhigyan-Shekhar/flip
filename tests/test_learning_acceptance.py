import asyncio

from switchcrew.learning_acceptance import run_two_task_learning


def test_two_task_learning_reuses_approved_lesson_behaviorally():
    result = asyncio.run(run_two_task_learning())

    assert result["status"] == "PASS"
    assert result["real_git_subprocesses"] is True
    assert result["live_waggle_called"] is False
    assert result["task_one"]["qa_failed_exit_code"] != 0
    assert result["task_one"]["regression_exit_code"] == 0
    assert result["task_two"]["baseline_exit_code"] != 0
    assert result["task_two"]["regression_exit_code_after_reuse"] == 0
    assert result["task_two"]["behavior_changed_after_memory_retrieval"] is True
