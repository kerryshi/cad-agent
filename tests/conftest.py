"""Session-level skip guard for the slicecheck module.

CLAUDE.md rule made mechanical: "Slicecheck tests skip loudly when OrcaSlicer
is absent — a skip is not a pass." pytest exits 0 on skips, so the merge gate
would silently green while the real-slicer tests never ran. This hook turns
ANY skipped test in tests/test_slicecheck.py into a hard session failure by
forcing the process exit code non-zero in pytest_sessionfinish
(session.exitstatus is read by pytest's wrap_session AFTER this hook runs, so
setting it here provably drives the exit code).

Skips in every other module keep their normal pytest semantics. xfail-marked
outcomes (report.wasxfail) are not treated as skips.
"""

_SLICECHECK_SKIPS: list[str] = []


def _is_slicecheck(nodeid: str) -> bool:
    return nodeid.split("::")[0].replace("\\", "/").endswith(
        "tests/test_slicecheck.py"
    )


def pytest_runtest_logreport(report):
    if (
        report.skipped
        and not hasattr(report, "wasxfail")
        and _is_slicecheck(report.nodeid)
    ):
        _SLICECHECK_SKIPS.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if _SLICECHECK_SKIPS:
        session.exitstatus = 1
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = [
            "SKIP-GUARD REFUSAL: %d slicecheck test(s) skipped - a skip is "
            "not a pass (CLAUDE.md)." % len(_SLICECHECK_SKIPS),
            "OrcaSlicer must be present for the gate to mean anything: "
            "check CAD_AGENT_ORCA / C:/Users/PC/tools/OrcaSlicer.",
        ] + ["  skipped: %s" % n for n in _SLICECHECK_SKIPS]
        if tr is not None:
            for ln in lines:
                tr.write_line(ln, red=True)
        else:  # terminal plugin disabled; still say why we are failing
            import sys

            print("\n".join(lines), file=sys.stderr)
