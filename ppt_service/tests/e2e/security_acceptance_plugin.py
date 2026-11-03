"""Acceptance requires executed passing checks; skip/xfail/xpass fail the run."""
import pytest

_nonpassing = []


def pytest_runtest_logreport(report):
    if report.skipped or hasattr(report, 'wasxfail'):
        _nonpassing.append(report.nodeid)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if _nonpassing and exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if _nonpassing:
        reporter = session.config.pluginmanager.get_plugin('terminalreporter')
        if reporter:
            reporter.write_sep('=', 'acceptance rejected: skip/xfail/xpass present')
