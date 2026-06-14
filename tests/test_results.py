import json

from lillio_archive.results import RunResult


def test_run_result_writes_json_and_csv(tmp_path) -> None:
    result = RunResult(command="download")
    result.add(source_key="1:image", status="downloaded", bytes=42)
    result.add(source_key="2:image", status="failed", message="timeout")
    json_path, csv_path = result.write(tmp_path)
    assert json.loads(json_path.read_text())["counts"] == {
        "downloaded": 1,
        "failed": 1,
    }
    assert "2:image,failed" in csv_path.read_text()
    assert result.failed
