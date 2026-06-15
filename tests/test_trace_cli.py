import json
import subprocess
import sys


def test_cli_trace_list_inspect_validate_and_replay(tmp_path):
    trace_dir = tmp_path / "data" / "traces"
    trace_dir.mkdir(parents=True)
    trace_path = trace_dir / "trace-20260614T010203Z-abcdef12.json"
    trace_path.write_text(
        json.dumps(
            {
                "schema_version": "zurvan.trace.v1",
                "trace_id": "trace-20260614T010203Z-abcdef12",
                "created_at": "2026-06-14T01:02:03Z",
                "title": "CLI trace",
                "summary": "Trace visible through CLI.",
                "events": [],
            }
        )
    )

    base = [sys.executable, "scripts/cli.py", "--project-root", str(tmp_path), "trace"]

    listed = subprocess.run(base + ["list"], capture_output=True, text=True)
    assert listed.returncode == 0
    assert "trace-20260614T010203Z-abcdef12" in listed.stdout

    inspected = subprocess.run(
        base + ["inspect", "trace-20260614T010203Z-abcdef12"],
        capture_output=True,
        text=True,
    )
    assert inspected.returncode == 0
    assert '"title": "CLI trace"' in inspected.stdout

    validated = subprocess.run(
        base + ["validate", "trace-20260614T010203Z-abcdef12"],
        capture_output=True,
        text=True,
    )
    assert validated.returncode == 0
    assert "valid" in validated.stdout.lower()

    replayed = subprocess.run(
        base + ["replay", "trace-20260614T010203Z-abcdef12"],
        capture_output=True,
        text=True,
    )
    assert replayed.returncode == 0
    assert "# Trace Replay: CLI trace" in replayed.stdout
