"""HarborClient.get_image_labels: decode OCI image config Labels field."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.services.harbor import HarborClient


@pytest.mark.asyncio
@respx.mock
async def test_get_image_labels_returns_dict() -> None:
    respx.get(
        "http://harbor.example/api/v2.0/projects/detectors/repositories/r1/artifacts/sha256:abc"
    ).mock(
        return_value=Response(
            200,
            json={
                "digest": "sha256:abc",
                "extra_attrs": {
                    "config": {
                        "Labels": {
                            "io.maldet.manifest": "eyJzY2hlbWFfdmVyc2lvbiI6IDF9",
                            "io.maldet.framework": "sklearn",
                            "org.opencontainers.image.version": "2.0.0",
                        }
                    }
                },
            },
        )
    )

    client = HarborClient("http://harbor.example", "u", "p")
    labels = await client.get_image_labels("detectors", "r1", "sha256:abc")
    assert labels["io.maldet.framework"] == "sklearn"
    assert labels["io.maldet.manifest"] == "eyJzY2hlbWFfdmVyc2lvbiI6IDF9"


@pytest.mark.asyncio
@respx.mock
async def test_get_image_labels_empty_if_no_config() -> None:
    respx.get(
        "http://harbor.example/api/v2.0/projects/detectors/repositories/r1/artifacts/sha256:def"
    ).mock(return_value=Response(200, json={"digest": "sha256:def"}))

    client = HarborClient("http://harbor.example", "u", "p")
    labels = await client.get_image_labels("detectors", "r1", "sha256:def")
    assert labels == {}


@pytest.mark.asyncio
@respx.mock
async def test_get_image_labels_empty_if_labels_null() -> None:
    respx.get(
        "http://harbor.example/api/v2.0/projects/detectors/repositories/r1/artifacts/sha256:ghi"
    ).mock(
        return_value=Response(
            200,
            json={"digest": "sha256:ghi", "extra_attrs": {"config": {"Labels": None}}},
        )
    )

    client = HarborClient("http://harbor.example", "u", "p")
    labels = await client.get_image_labels("detectors", "r1", "sha256:ghi")
    assert labels == {}
