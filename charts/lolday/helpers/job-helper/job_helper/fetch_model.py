"""Init container: download MLflow run artifacts to /mnt/source-model.

mlflow.artifacts.download_artifacts(artifact_path="model", dst_path=target) places
files at `target/model/*`, but the detector loads from `target` directly. So after
download we flatten `target/<artifact_path>/*` into `target/*`.
"""

import os
import shutil
import sys
from pathlib import Path

import mlflow.artifacts


def main() -> None:
    os.environ.setdefault("MLFLOW_TRACKING_URI", "http://mlflow.lolday.svc:5000")
    run_id = os.environ["SOURCE_RUN_ID"]
    artifact_path = os.environ.get("ARTIFACT_PATH", "model")
    target = Path(os.environ.get("TARGET_DIR", "/mnt/source-model"))
    target.mkdir(parents=True, exist_ok=True)

    try:
        mlflow.artifacts.download_artifacts(
            run_id=run_id,
            artifact_path=artifact_path,
            dst_path=str(target),
        )
    except Exception as e:
        print(f"fatal: artifact download failed: {e!r}", file=sys.stderr)
        sys.exit(4)

    inner = target / artifact_path
    if inner.is_dir():
        for child in inner.iterdir():
            shutil.move(str(child), str(target / child.name))
        inner.rmdir()

    print(f"downloaded run {run_id}:{artifact_path} to {target}")


if __name__ == "__main__":
    main()
