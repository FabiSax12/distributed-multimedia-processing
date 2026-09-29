#!/usr/bin/env python3
"""Smoke test contra AWS REAL, pensado para que el USUARIO lo corra a mano
(no lo ejecuta ningún agente/CI). Necesita:

  1. El coordinador corriendo localmente contra `.env.local`
     (`uv run --package coordinator uvicorn coordinator.main:app --reload`).
  2. Al menos un `fake_worker.py` corriendo por cada pool involucrado (video,
     audio, metadatos) para que el caso pueda cerrar solo.

Pasos: sube 4 archivos chiquitos vía `POST /api/uploads` (presigned PUT), crea
un caso heterogéneo con esos 4 archivos, hace polling a `GET /api/cases/{id}`
hasta que sea terminal, y verifica que:
  - el estado final sea `partially_completed` (el .txt es un formato no
    soportado, prefallido a propósito),
  - el reporte exista en S3 (`GET /api/cases/{id}/report`),
  - los conteos del caso cuadren con los del reporte.

Salida clara por stdout; exit code 1 si algo no cuadra.
"""

from __future__ import annotations

import sys
import time

import httpx
from coordinator.config import get_settings

from shared.states import TERMINAL_CASE

_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 180.0

# Bytes mínimos, NO necesitan ser reproducibles por un decoder real: el
# fake_worker no corre ffmpeg, solo enruta por extensión y simula el
# procesamiento. Alcanza con que existan en el bucket del dataset.
_FILES: dict[str, bytes] = {
    "smoke.mp4": b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
    "smoke.mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00fake-mp3-bytes",
    "smoke.png": b"\x89PNG\r\n\x1a\nfake-png-bytes",
    "smoke.txt": b"esto no es multimedia, tiene que prefallar",
}


def _fail(message: str) -> None:
    print(f"FALLO: {message}")
    sys.exit(1)


def main() -> None:
    settings = get_settings()
    base_url = f"http://localhost:{settings.API_PORT}"
    client = httpx.Client(base_url=base_url, timeout=30.0)

    print(
        f"== smoke test contra {base_url} (bucket dataset: {settings.DATASET_BUCKET}) =="
    )

    print(f"-> subiendo {len(_FILES)} archivos vía /api/uploads")
    upload_resp = client.post("/api/uploads", json={"filenames": list(_FILES)})
    if upload_resp.status_code != 201:
        _fail(
            f"POST /api/uploads devolvió {upload_resp.status_code}: {upload_resp.text}"
        )
    upload_body = upload_resp.json()

    input_keys: list[str] = []
    for entry in upload_body["urls"]:
        content = _FILES[entry["filename"]]
        put_resp = httpx.put(entry["url"], content=content, timeout=30.0)
        if put_resp.status_code not in (200, 204):
            _fail(
                f"PUT presignado para {entry['filename']} devolvió {put_resp.status_code}"
            )
        input_keys.append(entry["input_key"])
    print(f"   subidos: {input_keys}")

    print("-> creando caso heterogéneo")
    create_resp = client.post(
        "/api/cases",
        json={
            "files": [{"input_key": key} for key in input_keys],
            "priority": "normal",
            "label": "smoke test",
        },
    )
    if create_resp.status_code != 201:
        _fail(f"POST /api/cases devolvió {create_resp.status_code}: {create_resp.text}")
    case = create_resp.json()
    case_id = case["case_id"]
    print(
        f"   case_id={case_id} subtask_count={case['subtask_count']} "
        f"prefailed_count={case['prefailed_count']}"
    )
    if case["prefailed_count"] != 1:
        _fail(
            f"se esperaba 1 sub-tarea prefallida (el .txt), llegaron {case['prefailed_count']}"
        )

    print(f"-> polling GET /api/cases/{case_id} (timeout {_POLL_TIMEOUT_S:.0f}s)")
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    detail = None
    while time.monotonic() < deadline:
        detail_resp = client.get(f"/api/cases/{case_id}")
        if detail_resp.status_code != 200:
            _fail(f"GET /api/cases/{case_id} devolvió {detail_resp.status_code}")
        detail = detail_resp.json()
        status = detail["case"]["status"]
        print(f"   status={status} pending_count={detail['case']['pending_count']}")
        if status in {s.value for s in TERMINAL_CASE}:
            break
        time.sleep(_POLL_INTERVAL_S)
    else:
        _fail(
            "el caso no llegó a un estado terminal dentro del timeout (¿hay fake_worker corriendo?)"
        )

    assert detail is not None
    final_status = detail["case"]["status"]
    if final_status != "partially_completed":
        _fail(f"se esperaba status='partially_completed', llegó '{final_status}'")

    print("-> verificando que el reporte exista en S3")
    report_resp = client.get(f"/api/cases/{case_id}/report")
    if report_resp.status_code != 200:
        _fail(
            f"GET /api/cases/{case_id}/report devolvió {report_resp.status_code}: {report_resp.text}"
        )
    report_url = report_resp.json()["url"]
    report_body = httpx.get(report_url, timeout=30.0)
    if report_body.status_code != 200:
        _fail(f"la URL prefirmada del reporte devolvió {report_body.status_code}")
    report = report_body.json()

    if report["subtask_count"] != case["subtask_count"]:
        _fail(
            f"subtask_count del reporte ({report['subtask_count']}) no coincide "
            f"con el del caso ({case['subtask_count']})"
        )

    print(f"   reporte OK: {report['summary']}")
    print("== smoke test OK ==")


if __name__ == "__main__":
    main()
