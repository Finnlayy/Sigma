import pytest
from app.execution.KrakenCliBridge import _subprocess_runner

def test_subprocess_runner_valid_args():
    # Throws FileNotFoundError in _subprocess_runner (caught),
    # returns stdout, stderr, and returncode 127
    stdout, stderr, code = _subprocess_runner(["this_binary_does_not_exist", "--valid-arg"], 1.0)
    assert code == 127
    assert "not found" in stderr

def test_subprocess_runner_invalid_type():
    stdout, stderr, code = _subprocess_runner(["valid-binary", 123, "--flag"], 1.0)
    assert code == 1
    assert "EGeneral:Invalid argument type" in stderr
    assert "int" in stderr

def test_subprocess_runner_null_byte():
    stdout, stderr, code = _subprocess_runner(["kraken", "--malicious\0flag"], 1.0)
    assert code == 1
    assert "EGeneral:Invalid argument — contains control characters" in stderr

def test_subprocess_runner_newline():
    stdout, stderr, code = _subprocess_runner(["kraken", "--flag=value\nrm -rf /"], 1.0)
    assert code == 1
    assert "EGeneral:Invalid argument — contains control characters" in stderr

def test_subprocess_runner_carriage_return():
    stdout, stderr, code = _subprocess_runner(["kraken", "--flag=value\r"], 1.0)
    assert code == 1
    assert "EGeneral:Invalid argument — contains control characters" in stderr
