"""Tests for GUIN web API routes."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from guin.api import create_app


def _mk_record(path: Path, extent: str) -> None:
    payload = [
        {
            "@id": "https://guin.dev/prov#tool_invocation_00001",
            "@type": ["http://www.w3.org/ns/prov#Activity"],
            "https://guin.dev/prov#tool_name": [{"@value": "dwidenoise"}],
            "https://guin.dev/prov#tool_version": [{"@value": "3.0.4"}],
            "https://guin.dev/prov#container_digest": [{"@value": "digest-A"}],
            "http://www.w3.org/ns/prov#startedAtTime": [{"@value": "2026-01-01T00:00:00+00:00"}],
            "https://guin.dev/prov#input_parameters": [{"@value": json.dumps({"extent": extent})}],
            "https://guin.dev/prov#output_files": [{"@value": json.dumps({"out.nii.gz": "a" * 64})}],
        }
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_config_get_put_roundtrip(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr("guin.core.config.CONFIG_PATH", config_path)
    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/v1/config")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    put = client.put(
        "/api/v1/config",
        json={"model": "claude-3-5-haiku-20241022", "container_dir": str(tmp_path)},
    )
    assert put.status_code == 200
    body = put.json()
    assert body["status"] == "ok"
    assert body["data"]["config"]["model"] == "claude-3-5-haiku-20241022"
    assert config_path.is_file()


def test_provenance_diff_endpoint(tmp_path: Path) -> None:
    a = tmp_path / "a.jsonld"
    b = tmp_path / "b.jsonld"
    _mk_record(a, "5,5,5")
    _mk_record(b, "7,7,7")
    app = create_app()
    client = TestClient(app)

    resp = client.post(
        "/api/v1/provenance/diff",
        json={"record_a": str(a), "record_b": str(b)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "Parameter Differences" in body["data"]["markdown"]


def test_validate_endpoint_returns_envelope(tmp_path: Path) -> None:
    app = create_app()
    client = TestClient(app)
    ds = tmp_path / "ds"
    ds.mkdir()
    resp = client.post("/api/v1/validate", json={"dataset_path": str(ds)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "validation" in body["data"]
