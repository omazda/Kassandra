"""External data that is not in the bank statement.

Every builder returns PlannedEvent objects with a concrete date, a visible `source` and a stable id,
so a repeated sync of the same document does not create a copy. Parsers never log document text.
"""

import calendar
import csv
import hashlib
import io
import json
import os
import re
import statistics
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import httpx

from cashflow_domain.feeds import (
    BankFeed,
    ContractSpec,
    InvoiceSpec,
    MarketplaceFeed,
    OfdFeed,
)
from cashflow_domain.money import to_minor
from cashflow_domain.schemas import PlannedEvent
from cashflow_ml.calendar_ru import add_working_days, next_working_day
from cashflow_ml.documents import parse_purpose, party_key
from cashflow_services.categories import categorize


def event_id(prefix: str, account_id: str, *parts) -> str:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12]
    return f"{prefix}:{account_id}:{digest}"[:160]


def _event(account_id, known_at, **fields) -> PlannedEvent:
    day = fields.pop("day")
    return PlannedEvent(
        account_id=account_id,
        due_date=day,
        expected_date=day,
        known_at=known_at,
        status="confirmed",
        **fields,
    )


# ------------------------------------------------------------------ contracts and invoices


def contract_events(spec: ContractSpec, account_id: str, known_at: date):
    """(events, skipped): prepayment, remainder after delivery and deferral, or explicit schedule."""
    if spec.schedule:
        parts = [
            (p.date, p.amount_minor, f"платёж {i + 1} из {len(spec.schedule)}")
            for i, p in enumerate(spec.schedule)
        ]
    else:
        parts = []
        pre = int(
            (Decimal(spec.total_minor) * Decimal(str(spec.prepayment_pct)) / 100).quantize(
                Decimal(1), ROUND_HALF_UP
            )
        )
        if pre:
            parts.append(
                (spec.signed_date + timedelta(days=spec.prepayment_days), pre, "предоплата")
            )
        rest = spec.total_minor - pre
        if rest:
            first = spec.delivery_date + timedelta(days=spec.deferral_days)
            share, extra = divmod(rest, spec.installments)
            for k in range(spec.installments):
                amount = share + (extra if k == spec.installments - 1 else 0)
                stage = (
                    "окончательный расчёт"
                    if spec.installments == 1
                    else f"оплата {k + 1} из {spec.installments}"
                )
                parts.append(
                    (first + timedelta(days=k * spec.installment_every_days), amount, stage)
                )
    title = spec.title or (
        "Договор с клиентом" if spec.direction == "in" else "Договор с поставщиком"
    )
    events, skipped = [], []
    for i, (day, amount, stage) in enumerate(parts):
        day = next_working_day(day)
        if day < known_at:
            # Already due before the information date: it is either in the statement or overdue.
            skipped.append({"date": str(day), "amount_minor": amount, "stage": stage})
            continue
        if spec.direction == "in":
            category = "contract_prepayment" if stage == "предоплата" else "contract_settlement"
        else:
            category = "supplier"
        events.append(
            _event(
                account_id,
                known_at,
                id=event_id("contract", account_id, spec.contract_id, i),
                component_id=f"contract:{spec.contract_id}"[:160],
                category=category,
                amount_minor=amount,
                direction=spec.direction,
                day=day,
                source="contract",
                label=f"{title} №{spec.contract_id} · {stage}"[:240],
                counterparty_inn=spec.counterparty_inn,
                counterparty_name=spec.counterparty_name,
                document_ref=f"Договор №{spec.contract_id} от {spec.signed_date:%d.%m.%Y}"[:160],
                certainty="agreed",
            )
        )
    return events, skipped


def client_delays(
    transactions: list[dict], party: str | None, as_of: date
) -> tuple[list[int], list[int]]:
    """Days from a document date to its payment: (this client, the whole account), history <= as_of."""
    own, everyone = [], []
    for tx in transactions:
        if tx["direction"] != "in" or tx["operation_date"] > str(as_of):
            continue
        parsed = parse_purpose(tx.get("payment_purpose"))
        doc = parsed.document if parsed else None
        if doc is None:
            continue
        lag = (date.fromisoformat(tx["operation_date"]) - doc.doc_date).days
        if 0 <= lag <= 365:
            everyone.append(lag)
            if party and party_key(tx) == party:
                own.append(lag)
    return own, everyone


def invoice_event(spec: InvoiceSpec, account_id: str, known_at: date, transactions: list[dict]):
    """Expected payment date = due date, moved by the client's usual delay learnt from history."""
    due = spec.due_date or spec.issue_date + timedelta(days=spec.payment_terms_days)
    expected, delay = due, {"level": "due_date", "n": 0, "median_days": None}
    if spec.direction == "in" and spec.use_client_delay:
        party = spec.counterparty_inn or spec.counterparty_name
        own, everyone = client_delays(transactions, party, known_at)
        sample, level = (
            (own, "client")
            if len(own) >= 3
            else (everyone, "account")
            if len(everyone) >= 5
            else ([], None)
        )
        if level:
            typical = int(statistics.median(sample))
            expected = max(due, spec.issue_date + timedelta(days=typical))
            delay = {"level": level, "n": len(sample), "median_days": typical}
    expected = next_working_day(expected)
    overdue = expected <= known_at
    if overdue:
        expected = next_working_day(known_at + timedelta(days=1))
    party = spec.counterparty_inn or spec.counterparty_name or "unknown"
    event = PlannedEvent(
        id=event_id("invoice", account_id, spec.direction, spec.number, spec.issue_date, party),
        account_id=account_id,
        component_id=f"invoice:{party}"[:160],
        category="customer_payment" if spec.direction == "in" else "supplier",
        amount_minor=spec.amount_minor - spec.paid_minor,
        direction=spec.direction,
        due_date=due,
        expected_date=expected,
        known_at=known_at,
        source="invoice",
        status="confirmed",
        label=f"Счёт №{spec.number}"
        + (f" · {spec.counterparty_name}" if spec.counterparty_name else ""),
        counterparty_inn=spec.counterparty_inn,
        counterparty_name=spec.counterparty_name,
        document_ref=f"Счёт №{spec.number} от {spec.issue_date:%d.%m.%Y}",
        certainty="documented",
    )
    return event, {
        **delay,
        "due_date": str(due),
        "expected_date": str(expected),
        "overdue": overdue,
    }


INVOICE_COLUMNS = {"number", "issue_date", "amount", "direction"}


def parse_invoice_csv(payload: bytes) -> list[dict]:
    """ЭДО / 1С export: number;issue_date;due_date;payment_terms_days;amount;direction;inn;name."""
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = payload.decode("cp1251")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not INVOICE_COLUMNS.issubset(reader.fieldnames or []):
        raise ValueError("CSV счетов: нужны столбцы " + ", ".join(sorted(INVOICE_COLUMNS)))
    items = []
    for line, row in enumerate(reader, 2):
        direction = {"in": "in", "out": "out", "выставлен": "in", "получен": "out"}.get(
            (row.get("direction") or "").strip().lower()
        )
        if direction is None:
            raise ValueError(f"Строка {line}: direction — in/out или выставлен/получен")
        item = {
            "number": row["number"].strip(),
            "direction": direction,
            "issue_date": row["issue_date"].strip(),
            "amount_minor": to_minor(row["amount"].strip().replace(",", ".")),
            "counterparty_inn": (row.get("counterparty_inn") or "").strip() or None,
            "counterparty_name": (row.get("counterparty_name") or "").strip() or None,
        }
        if (row.get("due_date") or "").strip():
            item["due_date"] = row["due_date"].strip()
        if (row.get("payment_terms_days") or "").strip():
            item["payment_terms_days"] = int(row["payment_terms_days"])
        items.append(item)
    if not items:
        raise ValueError("В файле нет счетов")
    return items


# ------------------------------------------------------------------ bank, marketplace, cash registers

PAYROLL_LABELS = {
    "advance": "аванс",
    "salary": "зарплата",
    "bonus": "премия",
    "vacation": "отпускные",
}


def _rub(minor: int) -> str:
    return f"{minor // 100:,}".replace(",", " ") + f",{minor % 100:02d} ₽"


def bank_events(feed: BankFeed, account_id: str, known_at: date) -> list[PlannedEvent]:
    events = []
    for loan in feed.loans:
        for p in loan.schedule:
            if p.date < known_at:
                continue
            events.append(
                _event(
                    account_id,
                    known_at,
                    id=event_id("bank-loan", account_id, loan.contract, p.date),
                    component_id=f"loan:{loan.contract}"[:160],
                    category="debt_service",
                    amount_minor=p.principal_minor + p.interest_minor,
                    direction="out",
                    day=next_working_day(p.date),
                    source="bank",
                    label=f"Кредит {loan.contract}: тело {_rub(p.principal_minor)}, проценты {_rub(p.interest_minor)}"[
                        :240
                    ],
                    counterparty_name=loan.lender,
                    document_ref=f"Кредитный договор {loan.contract}"[:160],
                    certainty="documented",
                )
            )
    for p in feed.payroll:
        if p.date < known_at:
            continue
        events.append(
            _event(
                account_id,
                known_at,
                id=event_id("bank-payroll", account_id, p.date, p.kind),
                component_id="payroll",
                category="salary",
                amount_minor=p.amount_minor,
                direction="out",
                day=p.date,
                source="bank",
                label=f"Зарплатный проект: {PAYROLL_LABELS[p.kind]}",
                certainty="documented",
            )
        )
    for order in feed.payment_orders:
        if order.date < known_at:
            continue
        category = categorize(order.purpose, "out", order.counterparty_name or "")
        events.append(
            _event(
                account_id,
                known_at,
                id=event_id("bank-order", account_id, order.number, order.date),
                component_id=f"order:{order.number}"[:160],
                category="operating_expense" if category == "unknown" else category,
                amount_minor=order.amount_minor,
                direction="out",
                day=order.date,
                source="bank",
                label=f"Платёжное поручение №{order.number}: {order.purpose}"[:240],
                counterparty_inn=order.counterparty_inn,
                counterparty_name=order.counterparty_name,
                document_ref=f"П/п №{order.number} от {order.date:%d.%m.%Y}",
                certainty="documented",
            )
        )
    return events


def marketplace_events(
    feed: MarketplaceFeed, account_id: str, known_at: date
) -> list[PlannedEvent]:
    return [
        _event(
            account_id,
            known_at,
            id=event_id("marketplace", account_id, feed.marketplace, p.date, p.report_ref),
            component_id=f"marketplace:{feed.marketplace}"[:160],
            category="marketplace_payout",
            amount_minor=p.amount_minor,
            direction="in",
            day=next_working_day(p.date),
            source="marketplace",
            label=f"{feed.marketplace}: выплата"
            + (f" по отчёту {p.report_ref}" if p.report_ref else ""),
            counterparty_name=feed.marketplace,
            document_ref=p.report_ref or None,
            certainty="documented",
        )
        for p in feed.payouts
        if p.date >= known_at
    ]


def ofd_events(feed: OfdFeed, account_id: str, known_at: date) -> list[PlannedEvent]:
    """Card and SBP receipts arrive net of the acquiring fee; cash only after collection."""
    fee = Decimal(str(feed.acquiring_fee_pct)) / 100
    events = []
    for day in feed.days:
        gross = day.card_minor + day.sbp_minor
        if not gross:
            continue
        settle = add_working_days(next_working_day(day.date), feed.settlement_working_days)
        if settle <= known_at:
            continue
        net = int((Decimal(gross) * (1 - fee)).quantize(Decimal(1), ROUND_HALF_UP))
        events.append(
            _event(
                account_id,
                known_at,
                id=event_id("ofd", account_id, feed.register_id, day.date),
                component_id=f"ofd:{feed.register_id}"[:160],
                category="acquiring",
                amount_minor=net,
                direction="in",
                day=settle,
                source="ofd",
                label=f"Эквайринг по чекам за {day.date:%d.%m} ({feed.register_id}), комиссия {feed.acquiring_fee_pct}%",
                certainty="documented",
            )
        )
    return events


# ------------------------------------------------------------------ 1С «Клиент-банк»


def _1c_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%d.%m.%Y").date()


def _1c_party(doc: dict, side: str) -> tuple[str, str]:
    name = (doc.get(f"{side}1") or doc.get(side) or "").strip()
    # The long field often starts with «ИНН 7700000000 ООО ...».
    name = re.sub(r"^ИНН\s*\d{10,12}\s*", "", name)
    inn = (doc.get(f"{side}ИНН") or "").strip()
    return (inn if re.fullmatch(r"\d{10}|\d{12}", inn) else ""), name[:240]


def parse_client_bank_exchange(payload: bytes, account_id: str, opening_minor: int | None = None):
    """1CClientBankExchange (export «Обмен с банком» from 1С) -> CSV of the statement format.

    The format has no end-of-day balance, so it is restored from the opening balance of the
    section «СекцияРасчСчет» (or the opening_balance parameter) plus the daily movements.
    """
    text = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            candidate = payload.decode(encoding)
        except UnicodeDecodeError:
            continue
        if candidate.lstrip().startswith("1CClientBankExchange"):
            text = candidate
            break
    if text is None:
        raise ValueError("Ожидается файл 1CClientBankExchange (выгрузка «Обмен с банком» из 1С)")
    accounts, sections, documents = set(), [], []
    current, kind = None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("СекцияДокумент="):
            current, kind = {"Вид": line.split("=", 1)[1]}, "doc"
            continue
        if line == "СекцияРасчСчет":
            current, kind = {}, "acc"
            continue
        if line in ("КонецДокумента", "КонецРасчСчет"):
            (documents if kind == "doc" else sections).append(current)
            current, kind = None, None
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if current is not None:
            current[key] = value
        elif key == "РасчСчет":
            accounts.add(value.strip())
    accounts |= {s["РасчСчет"].strip() for s in sections if s.get("РасчСчет")}
    if not accounts:
        raise ValueError("В выгрузке нет номера расчётного счёта (РасчСчет)")
    if not documents:
        raise ValueError("В выгрузке нет платёжных документов")
    opening = opening_minor
    if opening is None:
        starts = [s for s in sections if s.get("НачальныйОстаток")]
        if not starts:
            raise ValueError("В выгрузке нет начального остатка: передайте opening_balance")
        first = min(starts, key=lambda s: _1c_date(s.get("ДатаНачала", "01.01.1900")))
        opening = to_minor(first["НачальныйОстаток"].replace(",", "."))
    ops = []
    for doc in documents:
        amount = to_minor(doc.get("Сумма", "0").replace(",", "."))
        payer = (doc.get("ПлательщикСчет") or doc.get("ПлательщикРасчСчет") or "").strip()
        payee = (doc.get("ПолучательСчет") or doc.get("ПолучательРасчСчет") or "").strip()
        if payer in accounts:
            direction, day_text = "Списание", doc.get("ДатаСписано") or doc.get("Дата", "")
            inn, name = _1c_party(doc, "Получатель")
        elif payee in accounts:
            direction, day_text = "Поступление", doc.get("ДатаПоступило") or doc.get("Дата", "")
            inn, name = _1c_party(doc, "Плательщик")
        else:
            continue
        day = _1c_date(day_text)
        purpose = (doc.get("НазначениеПлатежа") or "").strip()
        digest = hashlib.sha256(f"{day}|{amount}|{direction}|{inn}|{purpose}".encode()).hexdigest()[
            :8
        ]
        ops.append(
            {
                "day": day,
                "amount": amount,
                "direction": direction,
                "inn": inn,
                "name": name or "Контрагент",
                "purpose": purpose,
                "operation_id": f"1c-{day:%Y%m%d}-{doc.get('Номер', 'б-н').strip()}-{digest}"[:160],
            }
        )
    if not ops:
        raise ValueError("Ни один документ не относится к счетам выгрузки")
    ops.sort(key=lambda o: (o["day"], o["operation_id"]))
    eod, balance = {}, opening
    for op in ops:
        balance += op["amount"] if op["direction"] == "Поступление" else -op["amount"]
        eod[op["day"]] = balance
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(
        [
            "company_id",
            "operation_id",
            "operation_date",
            "amount",
            "direction",
            "counterparty_inn",
            "counterparty_name",
            "payment_purpose",
            "balance_eod",
        ]
    )
    for op in ops:
        writer.writerow(
            [
                account_id,
                op["operation_id"],
                op["day"].isoformat(),
                f"{op['amount'] / 100:.2f}",
                op["direction"],
                op["inn"],
                op["name"],
                op["purpose"],
                f"{eod[op['day']] / 100:.2f}",
            ]
        )
    closing = [
        to_minor(s["КонечныйОстаток"].replace(",", "."))
        for s in sections
        if s.get("КонечныйОстаток")
    ]
    check = None if not closing else ("ok" if closing[-1] == balance else "mismatch")
    info = {
        "format": "1CClientBankExchange",
        "accounts": sorted(accounts),
        "documents": len(ops),
        "period": [str(ops[0]["day"]), str(ops[-1]["day"])],
        "opening_minor": opening,
        "closing_minor": balance,
        "closing_check": check,
    }
    return out.getvalue().encode("utf-8-sig"), info


# ------------------------------------------------------------------ «клиент обещал заплатить 300 000 рублей 20-го»

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "мае": 5, "май": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}  # fmt: skip
MONTH_RE = r"(январ\w*|феврал\w*|март\w*|апрел\w*|мая|мае|май|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
MULTIPLIERS = [
    (r"млрд|миллиард\w*", 10**9),
    (r"млн|миллион\w*|лям\w*", 10**6),
    (r"тыс\w*|т\.\s?р\.?|тр\b|к\b", 10**3),
]
IN_SUBJECTS = ("клиент", "покупател", "заказчик", "контрагент", "должник", "маркетплейс", "партнер")
IN_VERBS = (
    "заплатит",
    "оплатит",
    "перечислит",
    "переведет",
    "вернет",
    "погасит",
    "придет",
    "придут",
    "поступ",
    "получ",
    "обещал",
    "внесет",
    "закроет долг",
)
OUT_WORDS = (
    "куплю", "купим", "купить", "покупк", "оплачу", "оплатим", "заплачу", "заплатим", "плачу", "платим",
    "закупк", "закупим", "аренд", "налог", "штраф", "ремонт", "зарплат", "отдам", "расход", "приобрет",
    "оборудован", "поставщик", "лизинг", "кредит", "енп", "ндс",
)  # fmt: skip
OUT_CATEGORIES = [
    (("аренд",), "rent"),
    (("налог", "енп", "ндс", "усн", "взнос"), "tax"),
    (("зарплат", "аванс сотрудн", "премия"), "salary"),
    (("кредит", "лизинг", "займ"), "debt_service"),
    (("поставщик", "закупк", "закупим", "товар", "сырь"), "supplier"),
    (("реклам",), "marketing"),
    (("коммунал", "электроэнерг", "вода"), "utilities"),
]


def _month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_month(d: date) -> tuple[int, int]:
    return (d.year + (d.month == 12), d.month % 12 + 1)


def _amount(text: str, spans: list[tuple[int, int]]) -> int | None:
    candidates = []
    for m in re.finditer(r"(?<![\d.])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(\S*)", text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        tail = m.group(3)
        if re.match(r"(-?го|числ|дн|недел|месяц|час|%)", tail) or re.match(MONTH_RE, tail):
            continue
        value = Decimal(re.sub(r"\D", "", m.group(1)) + ("." + m.group(2) if m.group(2) else ""))
        unit = 1
        rest = text[m.end(2) if m.group(2) else m.end(1) :].lstrip()
        for pattern, mult in MULTIPLIERS:
            if re.match(pattern, rest):
                unit = mult
                break
        has_currency = unit > 1 or bool(re.match(r"(руб|₽|р\.|рубл)", rest))
        candidates.append((has_currency, value * unit))
    for word, value in (
        ("полмиллиона", Decimal(500_000)),
        ("полтора миллиона", Decimal(1_500_000)),
        ("миллион", Decimal(1_000_000)),
    ):
        if not candidates and word in text:
            candidates.append((True, value))
    if not candidates:
        return None
    _, rubles = max(candidates, key=lambda c: (c[0], c[1]))
    return int((rubles * 100).quantize(Decimal(1), ROUND_HALF_UP)) if rubles >= 1 else None


def _date(text: str, today: date) -> tuple[date | None, str | None, list[tuple[int, int]]]:
    """(date, precision, spans of the date words so their numbers are not taken as an amount)."""
    m = re.search(r"\b(\d{1,2})\.(\d{2})(?:\.(\d{2,4}))?\b(?!\s*(?:млн|млрд|тыс|руб|₽|%))", text)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(1)) <= 31:
        year = int(m.group(3)) if m.group(3) else today.year
        year += 2000 if year < 100 else 0
        d = _month_day(year, int(m.group(2)), int(m.group(1)))
        if not m.group(3) and d < today:
            d = _month_day(year + 1, d.month, d.day)
        return d, "day", [m.span()]
    if m := re.search(r"\b(\d{1,2})\s*(?:-?го\s+)?" + MONTH_RE, text):
        month = next(v for k, v in MONTHS.items() if m.group(2).startswith(k))
        d = _month_day(today.year, month, int(m.group(1)))
        return (d if d >= today else _month_day(today.year + 1, month, d.day)), "day", [m.span()]
    if m := re.search(r"\b(\d{1,2})\s*(?:-?го|-?ого|числа)\b", text):
        day = int(m.group(1))
        y, mo = (today.year, today.month) if day > today.day else _next_month(today)
        return _month_day(y, mo, day), "day", [m.span()]
    if "послезавтра" in text:
        return today + timedelta(days=2), "day", []
    if "завтра" in text:
        return today + timedelta(days=1), "day", []
    if re.search(r"\bсегодня\b", text):
        return today, "day", []
    if m := re.search(
        r"через\s+(\d+|одну|две|три|неделю|месяц)?\s*(дн\w*|день|недел\w*|месяц\w*)", text
    ):
        count = {"одну": 1, "две": 2, "три": 3, "неделю": 1, "месяц": 1, None: 1}.get(m.group(1))
        count = count if count is not None else int(m.group(1))
        unit = m.group(2) if m.group(1) not in ("неделю", "месяц") else m.group(1)
        days = (
            7 * count
            if unit.startswith("недел")
            else 30 * count
            if unit.startswith("месяц")
            else count
        )
        return today + timedelta(days=days), "day", [m.span()]
    if "следующей неделе" in text:
        return today + timedelta(days=7 - today.weekday()), "week", []
    if "конце месяца" in text:
        return _month_day(today.year, today.month, 31), "day", []
    if m := re.search(r"\bв\s+" + MONTH_RE, text):
        month = next(v for k, v in MONTHS.items() if m.group(1).startswith(k))
        year = today.year if month > today.month else today.year + 1
        return date(year, month, 1), "month", [m.span()]
    return None, None, []


def parse_event_text(text: str, today: date) -> dict:
    """Rule-based draft of a planned event from a Russian phrase; nothing is saved."""
    t = re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()
    notes = []
    day, precision, spans = _date(t, today)
    amount = _amount(t, spans)
    in_score = 2 * any(s in t for s in IN_SUBJECTS) + any(v in t for v in IN_VERBS)
    out_score = 2 * any(w in t for w in OUT_WORDS[10:]) + any(w in t for w in OUT_WORDS[:10])
    direction = "in" if in_score > out_score else "out" if out_score > in_score else None
    category = None
    if direction == "out":
        category = next(
            (c for words, c in OUT_CATEGORIES if any(w in t for w in words)), "operating_expense"
        )
    elif direction == "in":
        category = (
            "marketplace_payout"
            if "маркетплейс" in t
            else "contract_prepayment"
            if "предоплат" in t
            else "customer_payment"
        )
    if day is not None:
        shifted = next_working_day(day)
        if shifted != day:
            notes.append(f"{day:%d.%m} — нерабочий день, деньги ожидаются {shifted:%d.%m}")
        if precision == "month":
            notes.append("Указан только месяц: поставлено 1-е число, уточните день")
        day = shifted
    certainty = "agreed" if "договор" in t else "documented" if "счет" in t else "promised"
    names = re.findall(r"[«\"]([^»\"]{2,120})[»\"]", text)
    draft = {
        "direction": direction,
        "amount_minor": amount,
        "expected_date": day.isoformat() if day else None,
        "category": category,
        "label": text.strip()[:240],
        "certainty": certainty,
        "counterparty_name": names[0] if names else None,
    }
    missing = [k for k in ("direction", "amount_minor", "expected_date") if draft[k] is None]
    if missing:
        notes.append(
            "Не удалось определить: "
            + ", ".join(
                {
                    "direction": "поступление это или списание",
                    "amount_minor": "сумму",
                    "expected_date": "дату",
                }[k]
                for k in missing
            )
        )
    return {"draft": draft, "missing": missing, "complete": not missing, "notes": notes}


# ------------------------------------------------------------------ open registries

RISK_BY_STATUS = {
    "active": "ok",
    "reorganizing": "warning",
    "liquidating": "critical",
    "liquidated": "critical",
    "bankrupt": "critical",
    "unknown": "unknown",
}
STATUS_LABELS = {
    "active": "Действующая организация",
    "reorganizing": "В процессе реорганизации",
    "liquidating": "В процессе ликвидации",
    "liquidated": "Ликвидирована",
    "bankrupt": "Банкротство",
    "unknown": "Нет данных в реестре",
}


def inn_checksum_valid(inn: str) -> bool:
    if not re.fullmatch(r"\d{10}|\d{12}", inn or ""):
        return False
    digits = [int(c) for c in inn]

    def check(coefficients):
        return sum(c * d for c, d in zip(coefficients, digits, strict=False)) % 11 % 10

    if len(digits) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[9]
    return (
        check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[10]
        and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[11]
    )


def is_synthetic_inn(inn: str) -> bool:
    """Real INNs start with a region code 01–99; the hackathon data uses «00…» or bad checksums."""
    return (inn or "").startswith("00") or not inn_checksum_valid(inn)


class CounterpartyRegistry:
    """ЕГРЮЛ/ЕГРИП via DaData when DADATA_API_KEY is set; otherwise an offline answer.

    Synthetic (obviously fake) INNs never reach the network; a demo file can mark them so the
    alert flow can be shown on the hackathon data. Real INNs never get demo statuses.
    """

    def __init__(self, demo_path=None, api_key=None):
        self.api_key = api_key if api_key is not None else os.getenv("DADATA_API_KEY", "")
        path = Path(demo_path or os.getenv("CASHFLOW_REGISTRY_DEMO", "examples/registry_demo.json"))
        self.demo = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def check(self, inn: str) -> dict:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        if is_synthetic_inn(inn):
            demo = self.demo.get(inn)
            status = demo["status"] if demo else "unknown"
            return {
                "inn": inn,
                "status": status,
                "status_label": STATUS_LABELS[status],
                "risk": RISK_BY_STATUS[status],
                "name": demo.get("name") if demo else None,
                "source": "demo_registry" if demo else "offline",
                "note": (demo or {}).get("note")
                or "Вымышленный ИНН обезличенных данных: во внешнем реестре не ищем",
                "checked_at": now,
            }
        if not self.api_key:
            return {
                "inn": inn,
                "status": "unknown",
                "status_label": STATUS_LABELS["unknown"],
                "risk": "unknown",
                "name": None,
                "source": "offline",
                "note": "Реестр не подключён: задайте DADATA_API_KEY для проверки по ЕГРЮЛ/ЕГРИП",
                "checked_at": now,
            }
        try:
            response = httpx.post(
                "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party",
                json={"query": inn, "count": 1},
                headers={"Authorization": f"Token {self.api_key}", "Accept": "application/json"},
                timeout=5,
            )
            response.raise_for_status()
            found = response.json().get("suggestions") or []
        except (httpx.HTTPError, ValueError):
            return {
                "inn": inn,
                "status": "unknown",
                "status_label": STATUS_LABELS["unknown"],
                "risk": "unknown",
                "name": None,
                "source": "dadata",
                "note": "Реестр временно недоступен",
                "checked_at": now,
            }
        if not found:
            status, data = "unknown", {}
        else:
            data = found[0].get("data") or {}
            status = {
                "ACTIVE": "active",
                "LIQUIDATING": "liquidating",
                "LIQUIDATED": "liquidated",
                "BANKRUPT": "bankrupt",
                "REORGANIZING": "reorganizing",
            }.get((data.get("state") or {}).get("status"), "unknown")
        return {
            "inn": inn,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "risk": RISK_BY_STATUS[status],
            "name": ((data.get("name") or {}).get("short_with_opf")) if data else None,
            "source": "dadata",
            "note": None if found else "В ЕГРЮЛ/ЕГРИП не найден",
            "checked_at": now,
        }
