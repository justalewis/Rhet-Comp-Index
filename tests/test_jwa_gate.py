"""The /jwa webtext companion is gated by HTTP Basic Auth and fails closed when
credentials are not configured."""

import base64

import pytest


def _basic(u, p):
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}


def test_jwa_503_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("PINAKES_JWA_USER", raising=False)
    monkeypatch.delenv("PINAKES_JWA_PASSWORD", raising=False)
    assert client.get("/jwa/").status_code == 503


def test_jwa_401_without_credentials(client, monkeypatch):
    monkeypatch.setenv("PINAKES_JWA_USER", "editor")
    monkeypatch.setenv("PINAKES_JWA_PASSWORD", "secret")
    resp = client.get("/jwa/")
    assert resp.status_code == 401
    assert "Basic" in resp.headers.get("WWW-Authenticate", "")


def test_jwa_401_with_wrong_credentials(client, monkeypatch):
    monkeypatch.setenv("PINAKES_JWA_USER", "editor")
    monkeypatch.setenv("PINAKES_JWA_PASSWORD", "secret")
    assert client.get("/jwa/", headers=_basic("editor", "nope")).status_code == 401


def test_jwa_serves_index_with_valid_credentials(client, monkeypatch):
    monkeypatch.setenv("PINAKES_JWA_USER", "editor")
    monkeypatch.setenv("PINAKES_JWA_PASSWORD", "secret")
    resp = client.get("/jwa/", headers=_basic("editor", "secret"))
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower()


def test_jwa_root_redirects_to_slash(client, monkeypatch):
    monkeypatch.setenv("PINAKES_JWA_USER", "editor")
    monkeypatch.setenv("PINAKES_JWA_PASSWORD", "secret")
    resp = client.get("/jwa", headers=_basic("editor", "secret"))
    assert resp.status_code in (301, 308)
    assert resp.headers["Location"].rstrip("/").endswith("/jwa")
