"""Tests for ``experiments_proxy.download_artifact`` RFC 6266 ``Content-Disposition``.

Background: previously the endpoint returned ``application/octet-stream`` with no
``Content-Disposition`` header. The frontend ``<a download>`` (without value) falls
back to the URL basename, which is the literal string "download" because the URL
ends with ``/artifacts/download?path=…``. Browsers therefore saved every artifact
as ``download``, losing the file extension.

Spec §5.2 / plan Task 2.3: send ``Content-Disposition: attachment; filename="<ascii>";
filename*=UTF-8''<percent-encoded>`` per RFC 6266 / RFC 5987 so the browser saves with
the artifact's actual basename, regardless of frontend hints.
"""

from urllib.parse import quote

import httpx
import pytest
import respx


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_download_artifact_sets_content_disposition_ascii(user_client) -> None:
    """Browser save dialog should default to the artifact basename, not "download"."""
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {
                            "run_id": "abc123",
                            "artifact_uri": "mlflow-artifacts:/0/abc123/artifacts",
                        },
                        "data": {"metrics": [], "params": [], "tags": []},
                    }
                },
            )
        )
        mock.get(
            "http://mlflow.lolday.svc:5000/api/2.0/mlflow-artifacts/artifacts/"
            "0/abc123/artifacts/predictions.csv"
        ).mock(
            return_value=httpx.Response(
                200, content=b"file_name,pred_label\nabc,Malware\n"
            )
        )

        resp = await user_client.get(
            "/api/v1/runs/abc123/artifacts/download?path=predictions.csv"
        )

    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert 'filename="predictions.csv"' in cd
    # RFC 5987 form for Unicode-safe browsers
    assert "filename*=UTF-8''predictions.csv" in cd


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_download_artifact_unicode_filename(user_client) -> None:
    """Non-ASCII filenames go through RFC 5987 percent-encoding while ASCII fallback uses ?-replacement."""
    encoded_path = quote("混淆樣本.csv", safe="")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {
                            "run_id": "abc123",
                            "artifact_uri": "mlflow-artifacts:/0/abc123/artifacts",
                        },
                        "data": {"metrics": [], "params": [], "tags": []},
                    }
                },
            )
        )
        mock.get(
            "http://mlflow.lolday.svc:5000/api/2.0/mlflow-artifacts/artifacts/"
            f"0/abc123/artifacts/{encoded_path}"
        ).mock(return_value=httpx.Response(200, content=b"data"))

        resp = await user_client.get(
            f"/api/v1/runs/abc123/artifacts/download?path={encoded_path}"
        )

    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    # ASCII fallback present (using "?" replacement chars for non-ASCII bytes)
    assert 'filename="' in cd
    # RFC 5987 percent-encoded UTF-8 form present and contains the encoded basename.
    assert "filename*=UTF-8''" in cd
    assert encoded_path in cd


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_download_artifact_path_with_quotes_does_not_inject(user_client) -> None:
    """A path basename containing a literal quote must not break the header."""
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {
                            "run_id": "abc123",
                            "artifact_uri": "mlflow-artifacts:/0/abc123/artifacts",
                        },
                        "data": {"metrics": [], "params": [], "tags": []},
                    }
                },
            )
        )
        mock.get(
            "http://mlflow.lolday.svc:5000/api/2.0/mlflow-artifacts/artifacts/"
            '0/abc123/artifacts/foo"bar.csv'
        ).mock(return_value=httpx.Response(200, content=b"data"))

        resp = await user_client.get(
            "/api/v1/runs/abc123/artifacts/download?path=foo%22bar.csv"
        )

    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    # Quote in ASCII fallback replaced with _ to defend against header injection.
    assert 'filename="foo_bar.csv"' in cd
    # Faithful UTF-8 form is still present and percent-encodes the quote.
    assert "filename*=UTF-8''foo%22bar.csv" in cd


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_download_artifact_uses_guessed_media_type(user_client) -> None:
    """``Content-Type`` should reflect the file extension when known (text/csv, etc.)."""
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {
                            "run_id": "abc123",
                            "artifact_uri": "mlflow-artifacts:/0/abc123/artifacts",
                        },
                        "data": {"metrics": [], "params": [], "tags": []},
                    }
                },
            )
        )
        mock.get(
            "http://mlflow.lolday.svc:5000/api/2.0/mlflow-artifacts/artifacts/"
            "0/abc123/artifacts/metrics.json"
        ).mock(return_value=httpx.Response(200, content=b'{"f1": 0.9}'))

        resp = await user_client.get(
            "/api/v1/runs/abc123/artifacts/download?path=metrics.json"
        )

    assert resp.status_code == 200
    # mimetypes.guess_type recognises .json regardless of platform.
    assert resp.headers["content-type"].startswith("application/json")
    assert 'filename="metrics.json"' in resp.headers["content-disposition"]
