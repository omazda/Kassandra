import csv
import io
from datetime import date

import pytest
from conftest import OWNER
from test_events_scenarios import make_event

from cashflow_domain.schemas import EventCreate
from cashflow_services.service import ConflictError


def register_bytes(events):
    values = [e.model_dump(mode="json") for e in events]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(values[0]), delimiter=";")
    writer.writeheader()
    writer.writerows(values)
    return stream.getvalue().encode("utf-8-sig")


def test_registry_repeat_import_is_idempotent(svc):
    payload = register_bytes([make_event(source="csv")])
    svc.import_events(payload, OWNER)
    version = svc.store.account("A")["event_version"]
    svc.import_events(payload, OWNER)
    assert svc.store.account("A")["event_version"] == version
    assert len(svc.events("A", date(2026, 4, 30))) == 1


def test_registry_conflict_rolls_back_whole_batch(svc):
    svc.add_event(EventCreate(event=make_event(id="existing", source="csv")), OWNER)
    payload = register_bytes(
        [
            make_event(id="new", source="csv"),
            make_event(id="existing", amount_minor=200000, source="csv"),
        ]
    )
    with pytest.raises(ConflictError):
        svc.import_events(payload, OWNER)
    assert {e.id for e in svc.events("A", date(2026, 4, 30))} == {"existing"}
