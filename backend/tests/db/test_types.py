"""JSONType / UTCDateTime / LongText round-trips on the real test DB + dialect-level bind/result checks for MSSQL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, MetaData, String, Table, insert, select
from sqlalchemy.dialects import mssql, postgresql, sqlite
from sqlalchemy.orm.attributes import flag_modified

from app.db import models as m
from app.db.types import JSONType, LongText, UTCDateTime

UTC = timezone.utc


def _scratch_table(engine):  # noqa: ANN001, ANN202
    md = MetaData()
    t = Table("zz_types_roundtrip", md, Column("id", String(40), primary_key=True), Column("j", JSONType()),
              Column("t", UTCDateTime()), Column("s", LongText()))
    md.drop_all(engine)
    md.create_all(engine)
    return md, t


def test_json_roundtrip_values(clean_db):  # noqa: ANN001
    md, t = _scratch_table(clean_db)
    values = {
        "dict": {"nodes": [{"id": "n1", "config": {"x": 1.5, "ok": True, "none": None}}], "edges": []},
        "list": [1, "two", {"three": 3}, [4]],
        "unicode": {"name": "فاتورة · invoice", "emoji": "✓"},
        "empty_d": {},
        "empty_l": [],
        "scalar": "just a string",
        "num": 42,
        "none": None,
    }
    try:
        with clean_db.begin() as c:
            c.execute(insert(t), [{"id": k, "j": v} for k, v in values.items()])
        with clean_db.connect() as c:
            got = dict(c.execute(select(t.c.id, t.c.j)).all())
            nulls = c.execute(select(t.c.id).where(t.c.j.is_(None))).scalars().all()
        assert got == values
        assert nulls == ["none"]  # None is SQL NULL, not JSON 'null'
    finally:
        md.drop_all(clean_db)


def test_utc_datetime_roundtrip(clean_db):  # noqa: ANN001
    md, t = _scratch_table(clean_db)
    aware = datetime(2026, 3, 1, 12, 30, 15, 123456, tzinfo=timezone(timedelta(hours=4)))  # Dubai
    naive = datetime(2026, 3, 1, 8, 30, 15)
    try:
        with clean_db.begin() as c:
            c.execute(insert(t), [{"id": "aware", "t": aware}, {"id": "naive", "t": naive}, {"id": "null", "t": None}])
        with clean_db.connect() as c:
            got = dict(c.execute(select(t.c.id, t.c.t)).all())
        assert got["aware"] == aware and got["aware"].tzinfo is not None
        assert got["aware"].utcoffset() == timedelta(0)
        assert got["aware"].hour == 8  # converted to UTC
        assert got["naive"] == naive.replace(tzinfo=UTC)  # naive assumed UTC
        assert got["null"] is None
    finally:
        md.drop_all(clean_db)


def test_long_text_unicode(clean_db):  # noqa: ANN001
    md, t = _scratch_table(clean_db)
    text = "مرحبا " * 5000
    try:
        with clean_db.begin() as c:
            c.execute(insert(t).values(id="x", s=text))
        with clean_db.connect() as c:
            assert c.execute(select(t.c.s)).scalar_one() == text
    finally:
        md.drop_all(clean_db)


def test_orm_json_reassignment_is_persisted(db):  # noqa: ANN001
    draft = db.get(m.PipelineDraft, "pl_ap")
    graph = dict(draft.graph)
    graph["nodes"] = graph["nodes"][:1]
    draft.graph = graph  # reassign whole value (documented pattern)
    db.commit()
    db.expire_all()
    assert len(db.get(m.PipelineDraft, "pl_ap").graph["nodes"]) == 1

    d2 = db.get(m.PipelineDraft, "pl_ap")
    d2.graph["edges"] = []  # in-place mutation needs flag_modified
    flag_modified(d2, "graph")
    db.commit()
    db.expire_all()
    assert db.get(m.PipelineDraft, "pl_ap").graph["edges"] == []


def test_orm_timestamps_are_aware(db):  # noqa: ANN001
    p = db.get(m.Pipeline, "pl_ap")
    assert p.created_at.tzinfo is not None and p.updated_at.utcoffset() == timedelta(0)
    ev = m.AuditEvent(workspace_id="ws_default", actor={"kind": "user", "id": "usr_admin"}, action="t.test",
                      resource_type="pipeline", resource_id="pl_ap")
    db.add(ev)
    db.flush()
    assert ev.id.startswith("aud_") and ev.at == ev.created_at


# ── dialect-level (no server needed) ──────────────────────────────────────────

def test_mssql_json_bind_and_result():
    d = mssql.dialect()
    jt = JSONType()
    bound = jt.process_bind_param({"a": [1, "é"], "b": None}, d)
    assert bound == '{"a":[1,"é"],"b":null}'
    assert jt.process_result_value(bound, d) == {"a": [1, "é"], "b": None}
    assert jt.process_bind_param(None, d) is None
    assert jt.process_result_value(None, d) is None
    assert jt.process_result_value("[1,2]", d) == [1, 2]


def test_mssql_datetime_bind_and_result():
    d = mssql.dialect()
    ut = UTCDateTime()
    naive = datetime(2026, 1, 1, 10, 0)
    assert ut.process_bind_param(naive, d) == naive.replace(tzinfo=UTC)
    plus4 = datetime(2026, 1, 1, 14, 0, tzinfo=timezone(timedelta(hours=4)))
    assert ut.process_bind_param(plus4, d).hour == 10
    assert ut.process_result_value(naive, d).tzinfo is UTC  # driver returned naive → treated as UTC


def test_type_ddl_per_dialect():
    def ddl(t, d):  # noqa: ANN001, ANN202
        return d.type_compiler.process(t)

    assert ddl(JSONType(), postgresql.dialect()) == "JSONB"
    assert ddl(JSONType(), mssql.dialect()) == "NVARCHAR(max)"
    assert ddl(JSONType(), sqlite.dialect()) == "JSON"
    assert ddl(UTCDateTime(), postgresql.dialect()) == "TIMESTAMP WITH TIME ZONE"
    assert ddl(UTCDateTime(), mssql.dialect()) == "DATETIMEOFFSET"
    assert ddl(LongText(), postgresql.dialect()) == "TEXT"
    assert ddl(LongText(), mssql.dialect()) == "NVARCHAR(max)"
