import json
from pathlib import Path

import httpx
import pytest
import respx


@pytest.mark.asyncio
@respx.mock
async def test_write_config_writes_all_files(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_ID", "aabbccdd-0000-0000-0000-000000000000")
    monkeypatch.setenv("BACKEND_URL", "http://backend")
    monkeypatch.setenv("JOB_TOKEN", "mytoken")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))

    respx.get(
        "http://backend/api/v1/internal/jobs/aabbccdd-0000-0000-0000-000000000000/config"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "config": {"data": {"train": "/mnt/config/train.csv"}},
                "train_csv": "file_name,label\naaa,Malware\n",
                "test_csv": "file_name,label\nbbb,Benign\n",
                "predict_csv": None,
            },
        )
    )

    from job_helper import write_config
    await write_config.main()

    cfg = json.loads((config_dir / "config.json").read_text())
    assert cfg["data"]["train"] == "/mnt/config/train.csv"

    train = (config_dir / "train.csv").read_text()
    assert "aaa,Malware" in train

    test = (config_dir / "test.csv").read_text()
    assert "bbb,Benign" in test

    assert not (config_dir / "predict.csv").exists()


@pytest.mark.asyncio
@respx.mock
async def test_write_config_retries_on_500(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_ID", "aabbccdd-0000-0000-0000-000000000000")
    monkeypatch.setenv("BACKEND_URL", "http://backend")
    monkeypatch.setenv("JOB_TOKEN", "mytoken")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))

    call_count = 0
    def _maybe_fail(request):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={
            "config": {"x": 1}, "train_csv": None, "test_csv": None, "predict_csv": None,
        })

    respx.get(
        "http://backend/api/v1/internal/jobs/aabbccdd-0000-0000-0000-000000000000/config"
    ).mock(side_effect=_maybe_fail)

    from job_helper import write_config
    await write_config.main()
    assert call_count == 3
    assert (config_dir / "config.json").read_text()


def test_fetch_model_downloads_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow")
    monkeypatch.setenv("SOURCE_RUN_ID", "run123")
    monkeypatch.setenv("ARTIFACT_PATH", "model")
    target = tmp_path / "source-model"
    target.mkdir()
    monkeypatch.setenv("TARGET_DIR", str(target))

    from unittest.mock import patch

    def _fake_download(run_id, artifact_path, dst_path):
        from pathlib import Path as P
        d = P(dst_path) / artifact_path
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.pkl").write_bytes(b"binary")
        (d / "label_encoder.pkl").write_bytes(b"binary")
        return str(d)

    with patch("mlflow.artifacts.download_artifacts", side_effect=_fake_download):
        from job_helper import fetch_model
        fetch_model.main()

    assert (target / "model" / "model.pkl").exists()
    assert (target / "model" / "label_encoder.pkl").exists()
