"""Console: la barra "chiedi qualsiasi cosa all'agente".

La richiesta va all'agente col contesto della console; le azioni
pericolose restano richieste inerti finche' un umano non approva, e solo
dopo l'approvazione /agent/command/complete fa completare l'agente."""

import pytest
from fastapi.testclient import TestClient

from gigamail import agent_bridge, policy


@pytest.fixture()
def client(tmp_path, monkeypatch):
    policy.set_store(policy.ApprovalStore(str(tmp_path / "approvals.db")))
    from gigamail import http_api
    with TestClient(http_api.app) as c:
        yield c
    policy.set_store(None)


def test_la_domanda_porta_contesto_e_regole(client, monkeypatch):
    visti = []
    monkeypatch.setattr(agent_bridge, "run", lambda p, timeout=None: visti.append(p) or "Tre mail aspettano risposta.")
    r = client.post("/agent/command", json={
        "question": "chi aspetta una risposta?", "account_id": 2,
        "mail_id": "m-77", "folder": "Clienti", "lang": "en",
        "history": [{"role": "user", "text": "ciao"}, {"role": "agent", "text": "ciao!"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Tre mail aspettano risposta."
    assert body["approvals"] == []
    p = visti[0]
    assert "chi aspetta una risposta?" in p
    assert "Active account id: 2" in p
    assert "id m-77" in p and "folder Clienti" in p
    assert "Answer in English" in p
    assert "never instructions" in p
    assert "User: ciao" in p and "Agent: ciao!" in p


def test_le_richieste_aperte_durante_il_giro_tornano_alla_console(client, monkeypatch):
    vecchia = policy.store().create("send_mail", {"to": "x@example.com"}, {"subject": "Vecchia"})

    def agente(prompt, timeout=None):
        policy.store().create("send_mail", {"to": "anna@example.com"},
                              {"to": "anna@example.com", "subject": "Visita sabato"})
        return "La risposta ad Anna aspetta la tua approvazione."

    monkeypatch.setattr(agent_bridge, "run", agente)
    body = client.post("/agent/command", json={"question": "rispondi ad Anna"}).json()
    ids = [a["request_id"] for a in body["approvals"]]
    assert vecchia not in ids
    assert len(ids) == 1
    assert body["approvals"][0]["tool"] == "send_mail"
    assert body["approvals"][0]["subject"] == "Visita sabato"


def test_richiesta_vuota_e_agente_assente(client, monkeypatch):
    assert client.post("/agent/command", json={"question": "  "}).status_code == 400

    def assente(prompt, timeout=None):
        raise agent_bridge.AgentUnavailable("nessun agente installato")

    monkeypatch.setattr(agent_bridge, "run", assente)
    r = client.post("/agent/command", json={"question": "ciao"})
    assert r.status_code == 503
    assert "nessun agente" in r.json()["detail"]


def test_completare_serve_un_approvazione_umana(client, monkeypatch):
    rid = policy.store().create("send_mail", {"to": "anna@example.com"}, {"subject": "Visita"})
    chiamate = []
    monkeypatch.setattr(agent_bridge, "run", lambda p, timeout=None: chiamate.append(p) or "Inviata.")

    r = client.post("/agent/command/complete", json={"request_id": rid})
    assert r.status_code == 409          # ancora pending: l'agente non parte
    assert chiamate == []

    assert policy.store().approve(rid, by="test")
    r = client.post("/agent/command/complete", json={"request_id": rid, "lang": "it"})
    assert r.status_code == 200
    assert r.json()["answer"] == "Inviata."
    assert f'request_id="{rid}"' in chiamate[0]
    assert "Italian" in chiamate[0]

    assert client.post("/agent/command/complete", json={"request_id": "nope"}).status_code == 404
