import csv
import io
from datetime import date, timedelta

import pytest

from cashflow_services.service import Service

OWNER = {"id": "alice", "accounts": []}


def csv_bytes(rows):
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "company_id",
            "operation_id",
            "operation_date",
            "amount",
            "direction",
            "counterparty_inn",
            "counterparty_name",
            "payment_purpose",
            "balance_eod",
        ],
        delimiter=";",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def transaction(
    account="A",
    operation="one",
    day="2026-01-01",
    amount="100.01",
    balance="1100.01",
    direction="Поступление",
    purpose="Оплата услуг",
    inn="0012345678",
):
    return dict(
        company_id=account,
        operation_id=operation,
        operation_date=day,
        amount=amount,
        direction=direction,
        counterparty_inn=inn,
        counterparty_name="Обезличенный клиент",
        payment_purpose=purpose,
        balance_eod=balance,
    )


@pytest.fixture
def rows():
    result = []
    balance = 10000000
    for i in range(240):
        d = date(2025, 10, 1) + timedelta(days=i)
        day_rows = [transaction(operation=f"in-{i}", day=str(d), amount="500.01", inn="0012345678")]
        net = 50001
        if d.day == 15:
            day_rows.append(
                transaction(
                    operation=f"rent-{i}",
                    day=str(d),
                    amount="8000.03",
                    direction="Списание",
                    purpose="Аренда помещения",
                    inn="0098765432",
                )
            )
            net -= 800003
        if d.weekday() == 4:
            day_rows.append(
                transaction(
                    operation=f"weekly-{i}",
                    day=str(d),
                    amount="3000.02",
                    purpose="Оплата услуг по договору",
                    inn="0011111111",
                )
            )
            net += 300002
        balance += net
        for row in day_rows:
            row["balance_eod"] = f"{balance // 100}.{balance % 100:02}"
        result.extend(day_rows)
    return result


@pytest.fixture
def svc(tmp_path, rows):
    service = Service(tmp_path / "cashflow.sqlite", tmp_path / "artifacts")
    service.import_transactions(csv_bytes(rows), OWNER)
    return service


@pytest.fixture
def client(svc):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    return TestClient(
        create_app(
            svc,
            users={"alice-token": OWNER, "bob-token": {"id": "bob", "accounts": []}},
            auth_mode="token",
        )
    )


@pytest.fixture
def auth():
    return {"Authorization": "Bearer alice-token"}
