import logging

from modality_simulator.recent_logs import RecentLogs


def logger_with(handler: RecentLogs) -> logging.Logger:
    logger = logging.getLogger("test_recent_logs")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def test_keeps_formatted_records_oldest_first():
    recent = RecentLogs()
    logger = logger_with(recent)
    logger.info("first %d", 1)
    logger.warning("second")
    lines = recent.lines()
    assert len(lines) == 2
    assert lines[0].endswith("INFO test_recent_logs: first 1")
    assert lines[1].endswith("WARNING test_recent_logs: second")


def test_keeps_only_the_most_recent_records():
    recent = RecentLogs(capacity=3)
    logger = logger_with(recent)
    for i in range(5):
        logger.info("record %d", i)
    assert [line.rsplit(" ", 1)[1] for line in recent.lines()] == ["2", "3", "4"]
