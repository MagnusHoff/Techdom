from techdom.services.prospect_jobs import ProspectJobService


def test_in_memory_job_lifecycle(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("PROSPECT_REDIS_URL", raising=False)

    service = ProspectJobService(redis_url=None)
    job = service.create("123456", enqueue=True)

    reserved = service.reserve_next(timeout=1)
    assert reserved is not None
    assert reserved.id == job.id

    service.mark_running(job.id, progress=10, message="starter")
    service.store_artifact(job.id, "step", {"foo": "bar"})
    service.mark_done(
        job.id,
        pdf_path="/tmp/test.pdf",
        pdf_url="https://example.com/prospect.pdf",
        result={"ok": True},
        message="ferdig",
    )

    data = service.get(job.id)
    assert data is not None
    assert data["status"] == "done"
    assert data["message"] == "ferdig"
    assert data["artifacts"]["step"]["foo"] == "bar"
    assert data["result"] == {"ok": True}
    assert data["pdf_url"] == "https://example.com/prospect.pdf"

    service.mark_failed(job.id, "feil", error="boom")
    data = service.get(job.id)
    assert data is not None
    assert data["status"] == "failed"
    assert data["error"] == "boom"

    service.delete(job.id)
    assert service.get(job.id) is None
