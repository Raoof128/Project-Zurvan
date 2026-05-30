import pytest
import subprocess
import os
from unittest.mock import patch

def test_gauntlet_rejects_non_raw_files(tmp_path):
    # Test that the script refuses to run on files outside of raw/
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("test")
    
    # Run the script with python using subprocess
    script_path = os.path.join("scripts", "run_reliability_gauntlet.py")
    res = subprocess.run(["python", script_path, str(outside_file)], capture_output=True, text=True)
    
    assert res.returncode != 0
    assert "must be located in the raw/ directory" in res.stdout

@patch("subprocess.run")
def test_gauntlet_continues_on_failure(mock_run):
    # Mock subprocess.run to fail on the first file and pass on the second
    def side_effect(cmd, **kwargs):
        class MockResult:
            def __init__(self, returncode, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr
                
        # If it's the first file ingest, fail it
        if "fail.txt" in cmd:
            return MockResult(1, "", "Ingest Error")
        return MockResult(0, "Success", "")
        
    mock_run.side_effect = side_effect
    
    from scripts.run_reliability_gauntlet import run_gauntlet
    # Since run_gauntlet prints, we just check it doesn't raise an exception
    # and processes both files.
    # We will pass dummy strings since it doesn't actually check the file path internally
    # when subprocess is mocked, other than string matching in our mock.
    run_gauntlet(["raw/fail.txt", "raw/pass.txt"])
    
    # It should have called subprocess.run at least twice (one for fail.txt ingest, one for pass.txt ingest)
    assert mock_run.call_count >= 2
