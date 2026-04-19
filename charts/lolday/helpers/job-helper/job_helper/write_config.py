"""Init container: fetch resolved config + CSVs from backend, write to /mnt/config."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx


async def main() -> None:
    job_id = os.environ["JOB_ID"]
    backend = os.environ["BACKEND_URL"].rstrip("/")
    token = os.environ["JOB_TOKEN"]
    config_dir = Path(os.environ.get("CONFIG_DIR", "/mnt/config"))
    config_dir.mkdir(parents=True, exist_ok=True)

    url = f"{backend}/api/v1/internal/jobs/{job_id}/config"
    headers = {"Authorization": f"Bearer {token}"}

    last_err: Exception | None = None
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                break
            if r.status_code in (401, 403, 404):
                print(f"fatal: backend returned {r.status_code}: {r.text}", file=sys.stderr)
                sys.exit(2)
            last_err = RuntimeError(f"HTTP {r.status_code}: {r.text}")
        except httpx.HTTPError as e:
            last_err = e
        await asyncio.sleep(2 ** attempt)
    else:
        print(f"fatal: backend unreachable after 5 attempts: {last_err!r}", file=sys.stderr)
        sys.exit(3)

    (config_dir / "config.json").write_text(json.dumps(data["config"], indent=2))

    csv_map = {
        "train_csv": "train.csv",
        "test_csv": "test.csv",
        "predict_csv": "predict.csv",
    }
    for key, filename in csv_map.items():
        content = data.get(key)
        if content is not None:
            (config_dir / filename).write_text(content)

    print(f"config written to {config_dir}")


if __name__ == "__main__":
    asyncio.run(main())
