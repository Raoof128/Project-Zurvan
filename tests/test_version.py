from scripts.version import print_version

def test_print_version(capsys):
    print_version()
    captured = capsys.readouterr()
    assert "Zurvan Version:" in captured.out
    assert "Python Version:" in captured.out
    assert "Project Root:" in captured.out
    assert "Snapshots & Release Packaging" in captured.out
