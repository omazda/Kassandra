"""Реестр документов как дополнительный источник прогноза (docs/ml_protocol.md: D6, X3).

В назначениях платежей указаны вид, номер и дата документа (счёт, УПД, акт, накладная) и номер
договора. Из них восстанавливается реестр выставленных документов на дату as_of:

* документ попадает в срез, только если его дата <= as_of и часть оплат приходится после as_of;
* сумма события = сумма будущих оплат документа (допустимо по D6: «сумма и факт существования»);
* дата события = дата документа + задержка, оценённая ТОЛЬКО по оплатам до as_of
  (клиент -> отрасль -> все), перенесена на рабочий день (calendar_ru);
* фактическая дата будущей оплаты в прогноз не попадает;
* предоплата по договору № X -> ожидается «Окончательный расчёт по договору № X»: сумма по
  историческому отношению расчёт/предоплата, лаг по истории.

Смещение выживаемости: в выписке видны только оплаченные документы. Выставленный, но
не оплаченный до конца архива документ в реестр не попадает, поэтому реестр оптимистичен.

Все деньги — целые копейки, даты — datetime.date.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from cashflow_domain.schemas import PlannedEvent
from cashflow_ml.calendar_ru import next_working_day

SOURCE = "documents"
QUANTILES = {"q10": 0.10, "q25": 0.25, "q50": 0.50, "q75": 0.75, "q90": 0.90}

# Категории, платежи по которым реестр «владеет» (нужно X4 против двойного счёта).
# Регулярные ежемесячные счета (аренда, коммунальные, связь, ПО, охрана, бухгалтерия) ведёт
# детектор механизмов X2, поэтому в реестр по умолчанию не входят.
DOC_CATEGORIES = {
    "in": frozenset({"customer_payment"}),
    "out": frozenset({"supplier", "contractor", "logistics"}),
}
CONTRACT_CATEGORIES = {
    "in": frozenset({"contract_prepayment", "contract_settlement"}),
    "out": frozenset({"supplier", "contractor"}),
}

DOC_KINDS = ("act", "upd", "waybill", "invoice", "contract")
_PRIORITY = {"act": 0, "upd": 1, "waybill": 2, "invoice": 3, "contract": 4}
_KIND_LABEL = {
    "act": "акт",
    "upd": "УПД",
    "waybill": "накладная",
    "invoice": "счёт",
    "contract": "договор",
}

_DOC_RE = re.compile(
    r"\b(?P<t>упд|акт\w*|накладн\w*|торг-?12|сч[её]т-?фактур\w*|сч[её]т\w*|договор\w*)"
    r"(?P<desc>(?:\s+[^\W\d][\w\-]*){0,2}?)"
    r"(?:\s*№\s*(?P<n1>(?=[\w/\-]*\d)[\w/\-]+?)|\s+(?P<n2>\d[\w/\-]*?))"
    r"(?:\s*от\s*(?P<d>\d{1,2}\.\d{1,2}\.\d{4})\s*(?:г\.?)?)?"
    r"(?=[\s.,;:)]|$)",
    re.IGNORECASE,
)


def _kind(token: str) -> str:
    t = token.lower()
    if t.startswith("упд"):
        return "upd"
    if t.startswith("акт"):
        return "act"
    if t.startswith("накладн") or t.startswith("торг"):
        return "waybill"
    if t.startswith("договор"):
        return "contract"
    return "invoice"  # счёт, счёт-фактура


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    day, month, year = (int(x) for x in text.split("."))
    try:
        return date(year, month, day)
    except ValueError:
        return None


@dataclass(frozen=True)
class DocRef:
    kind: str  # act | upd | waybill | invoice | contract
    number: str
    doc_date: date | None

    @property
    def text(self) -> str:
        suffix = f" от {self.doc_date:%d.%m.%Y}" if self.doc_date else ""
        return f"{_KIND_LABEL[self.kind]} №{self.number}{suffix}"


@dataclass(frozen=True)
class ParsedPurpose:
    refs: tuple[DocRef, ...]  # все найденные ссылки в порядке появления
    contract_number: str | None
    stage: str  # payment | prepayment | settlement | partial | topup | return

    @property
    def document(self) -> DocRef | None:
        """Платёжный документ: акт > УПД > накладная > счёт, обязательно с датой.

        Договор сам по себе не считается документом реестра (это связь для предоплат).
        """
        dated = [r for r in self.refs if r.kind != "contract" and r.doc_date is not None]
        if not dated:
            return None
        return min(dated, key=lambda r: _PRIORITY[r.kind])

    @property
    def has_undated_document(self) -> bool:
        return self.document is None and any(r.kind != "contract" for r in self.refs)


def _stage(text: str) -> str:
    low = text.lower()
    if low.startswith("возврат"):
        return "return"
    if re.search(r"окончательн\w*\s+расч", low):
        return "settlement"
    if re.search(r"частичн\w*\s+оплат", low):
        return "partial"
    if re.search(r"\bдоплат", low):
        return "topup"
    if re.search(r"предоплат|аванс", low):
        return "prepayment"
    return "payment"


def parse_purpose(text: str | None) -> ParsedPurpose | None:
    """Ссылки на документы и договор в назначении платежа; None, если ссылок нет."""
    text = text or ""
    refs, contract = [], None
    for m in _DOC_RE.finditer(text):
        if re.search(r"(лицев\w*|расч[её]тн\w*|банковск\w*)\s*$", text[: m.start()], re.I):
            continue  # лицевой/расчётный счёт — не документ
        number = (m.group("n1") or m.group("n2")).strip("-/")
        ref = DocRef(_kind(m.group("t")), number, _parse_date(m.group("d")))
        refs.append(ref)
        if ref.kind == "contract" and contract is None:
            contract = number
    if not refs:
        return None
    return ParsedPurpose(tuple(refs), contract, _stage(text))


# ---------------------------------------------------------------- платежи, документы, договоры


@dataclass(frozen=True)
class DocPayment:
    account_id: str
    operation_id: str
    op_date: date
    amount_minor: int
    direction: str
    category: str
    inn: str
    name: str
    parsed: ParsedPurpose


def party_key(tx: dict) -> str:
    inn = (tx.get("counterparty_inn") or "").strip()
    return inn if inn.strip("0") else (tx.get("counterparty_name") or "").strip()


def extract_payments(transactions: list[dict], account_id: str | None = None) -> list[DocPayment]:
    """Операции с разобранными ссылками на документы. Фильтры категорий — позже."""
    out = []
    for tx in transactions:
        parsed = parse_purpose(tx.get("payment_purpose"))
        if parsed is None:
            continue
        out.append(
            DocPayment(
                account_id=account_id or tx.get("account_id", ""),
                operation_id=tx.get("operation_id", ""),
                op_date=date.fromisoformat(str(tx["operation_date"])),
                amount_minor=int(tx["amount_minor"]),
                direction=tx["direction"],
                category=tx["category"],
                inn=party_key(tx),
                name=tx.get("counterparty_name") or "",
                parsed=parsed,
            )
        )
    return out


@dataclass
class Document:
    """Документ с объединёнными частичными оплатами."""

    doc_id: str
    account_id: str
    direction: str
    category: str
    inn: str
    name: str
    ref: DocRef
    contract_number: str | None
    payments: list[tuple[date, int]] = field(default_factory=list)  # (дата, копейки)

    @property
    def doc_date(self) -> date:
        return self.ref.doc_date  # type: ignore[return-value]

    def paid_until(self, as_of: date) -> int:
        return sum(a for d, a in self.payments if d <= as_of)

    def future(self, as_of: date) -> list[tuple[date, int]]:
        return [(d, a) for d, a in self.payments if d > as_of]

    def weighted_date(self, rows: list[tuple[date, int]] | None = None) -> date:
        rows = self.payments if rows is None else rows
        total = sum(a for _, a in rows)
        if total <= 0:
            return rows[0][0]
        base = min(d for d, _ in rows)
        shift = sum((d - base).days * a for d, a in rows) / total
        return base + timedelta(days=int(round(shift)))


@dataclass
class ContractCycle:
    """Предоплата -> окончательный расчёт по одному договору (i-я пара по порядку дат)."""

    cycle_id: str
    account_id: str
    direction: str
    inn: str
    name: str
    number: str
    prepay_category: str
    settle_category: str | None
    prepay: tuple[date, int]
    settle: tuple[date, int] | None


def build_documents(payments: list[DocPayment], categories: dict | None = None) -> list[Document]:
    categories = categories or DOC_CATEGORIES
    docs: dict[tuple, Document] = {}
    for p in payments:
        ref = p.parsed.document
        if (
            ref is None
            or p.parsed.stage == "return"
            or p.category not in categories.get(p.direction, ())
        ):
            continue
        key = (p.account_id, p.direction, p.inn, ref.kind, ref.number, ref.doc_date)
        doc = docs.get(key)
        if doc is None:
            doc_id = f"{p.account_id}|{p.direction}|{p.inn}|{ref.kind}|{ref.number}|{ref.doc_date}"
            doc = docs[key] = Document(
                doc_id,
                p.account_id,
                p.direction,
                p.category,
                p.inn,
                p.name,
                ref,
                p.parsed.contract_number,
            )
        doc.payments.append((p.op_date, p.amount_minor))
    for doc in docs.values():
        doc.payments.sort()
    return sorted(docs.values(), key=lambda d: (d.account_id, d.doc_date, d.doc_id))


def build_contract_cycles(payments: list[DocPayment]) -> list[ContractCycle]:
    groups: dict[tuple, dict[str, list[DocPayment]]] = defaultdict(
        lambda: {"prepayment": [], "settlement": []}
    )
    for p in payments:
        stage, number = p.parsed.stage, p.parsed.contract_number
        if stage not in ("prepayment", "settlement") or not number:
            continue
        if p.category not in CONTRACT_CATEGORIES.get(p.direction, ()):
            continue
        groups[(p.account_id, p.direction, p.inn, number)][stage].append(p)
    cycles = []
    for (account, direction, inn, number), parts in sorted(groups.items()):
        pre = sorted(parts["prepayment"], key=lambda p: (p.op_date, p.operation_id))
        fin = sorted(parts["settlement"], key=lambda p: (p.op_date, p.operation_id))
        for i, p in enumerate(pre):
            f = fin[i] if i < len(fin) else None
            cycles.append(
                ContractCycle(
                    cycle_id=f"{account}|{direction}|{inn}|contract|{number}|{i}",
                    account_id=account,
                    direction=direction,
                    inn=inn,
                    name=p.name,
                    number=number,
                    prepay_category=p.category,
                    settle_category=f.category if f else None,
                    prepay=(p.op_date, p.amount_minor),
                    settle=(f.op_date, f.amount_minor) if f else None,
                )
            )
    return cycles


# ---------------------------------------------------------------- выборки задержек


class _Samples:
    """Наблюдения (опорная дата, дата завершения, значение) для оценки по истории до as_of."""

    def __init__(self):
        self._rows: list[tuple[int, int, float]] = []
        self._arrays = None

    def add(self, ref: date, last: date, value: float):
        self._rows.append((ref.toordinal(), last.toordinal(), value))
        self._arrays = None

    def sorted_values(self, as_of: date, guard_days: int) -> np.ndarray:
        """Значения завершённых до as_of наблюдений, опорная дата <= as_of - guard_days."""
        if self._arrays is None:
            arr = np.asarray(self._rows, dtype=float).reshape(-1, 3)
            self._arrays = (arr[:, 0], arr[:, 1], arr[:, 2])
        ref, last, value = self._arrays
        mask = (last <= as_of.toordinal()) & (ref <= as_of.toordinal() - guard_days)
        return np.sort(value[mask])


@dataclass(frozen=True)
class DelayConfig:
    """Параметры модели задержки; выбираются на калибровочных срезах (см. report.md)."""

    guard_days: int = 0  # исключить документы моложе guard_days (цензурирование справа)
    condition: bool = True  # учитывать, что документ не оплачен к as_of (задержка >= возраст)
    min_client: int = 5
    min_industry: int = 20
    min_global: int = 20
    fallback_days: int = 7  # без выборки: as_of + fallback_days


@dataclass(frozen=True)
class DelayEstimate:
    level: str  # client | industry | global | fallback
    n: int
    quantiles: dict  # q10..q90 в днях от опорной даты (задержка)
    median_days: int


def _conditional(values: np.ndarray, lower: float) -> np.ndarray:
    return values[np.searchsorted(values, lower, side="left") :]


def _quantiles(values: np.ndarray) -> dict:
    return {k: int(round(float(np.quantile(values, q)))) for k, q in QUANTILES.items()}


# ---------------------------------------------------------------- события реестра


@dataclass(frozen=True)
class RegistryEvent:
    """Ожидаемое событие реестра. В PlannedEvent нет контрагента и ссылки на документ."""

    date: date  # ожидаемая дата (рабочий день)
    amount_minor: int
    direction: str
    category: str
    counterparty_inn: str
    counterparty_name: str
    document_ref: str
    known_at: date  # дата документа (для предоплаты — дата предоплаты)
    source: str  # всегда "documents"
    mechanism: str  # invoice | contract_settlement
    component_id: str  # идентификатор документа/договора
    delay_level: str
    delay_n: int
    delay_quantiles: dict  # q10..q90 задержки в днях (для X6)
    window: tuple  # (дата q10, дата q90), рабочие дни
    paid_before_minor: int = 0  # уже оплачено до as_of (частичные оплаты)

    def to_planned_event(self, account_id: str) -> PlannedEvent:
        """Приведение к PlannedEvent. source="csv" — ближайшее допустимое значение схемы."""
        label = f"{self.document_ref} · {self.counterparty_name}"[:240]
        return PlannedEvent(
            id=f"doc:{self.component_id}:{self.date}"[:160],
            account_id=account_id,
            component_id=self.component_id[:160] or "doc",
            category=self.category,
            amount_minor=self.amount_minor,
            direction=self.direction,
            due_date=self.date,
            expected_date=self.date,
            known_at=self.known_at,
            source="csv",
            status="assumption",
            paid_minor=0,
            label=label,
        )


class DocumentUniverse:
    """Все документы и договоры архива + индекс задержек. Метод `registry` строит срез."""

    def __init__(
        self,
        payments: list[DocPayment],
        industries: dict[str, str] | None = None,
        config: DelayConfig | None = None,
    ):
        self.config = config or DelayConfig()
        self.industries = industries or {}
        self.payments = payments
        self.documents = build_documents(payments)
        self.cycles = build_contract_cycles(payments)
        self.docs_by_account: dict[str, list[Document]] = defaultdict(list)
        for d in self.documents:
            self.docs_by_account[d.account_id].append(d)
        self.cycles_by_account: dict[str, list[ContractCycle]] = defaultdict(list)
        for c in self.cycles:
            self.cycles_by_account[c.account_id].append(c)
        self.doc_index = {d.doc_id: d for d in self.documents}
        self.cycle_index = {c.cycle_id: c for c in self.cycles}
        self._delay: dict[tuple, _Samples] = defaultdict(_Samples)
        self._lag: dict[tuple, _Samples] = defaultdict(_Samples)
        self._ratio: dict[tuple, _Samples] = defaultdict(_Samples)
        self._load_samples()
        self._cache: dict = {}
        self._cache_as_of = None

    # -- выборки
    def _levels(self, account: str, direction: str, inn: str):
        industry = self.industries.get(account, "")
        return (
            ("client", (account, direction, inn)),
            ("industry", (direction, industry)),
            ("global", (direction,)),
        )

    def _load_samples(self):
        for d in self.documents:
            delay = max(0, (d.weighted_date() - d.doc_date).days)
            last = d.payments[-1][0]
            for level, key in self._levels(d.account_id, d.direction, d.inn):
                self._delay[(level, *key)].add(d.doc_date, last, delay)
        for c in self.cycles:
            if c.settle is None:
                continue
            lag = max(0, (c.settle[0] - c.prepay[0]).days)
            ratio = c.settle[1] / c.prepay[1] if c.prepay[1] else 0.0
            for level, key in self._levels(c.account_id, c.direction, c.inn):
                self._lag[(level, *key)].add(c.prepay[0], c.settle[0], lag)
                self._ratio[(level, *key)].add(c.prepay[0], c.settle[0], ratio)

    def _sorted(self, store: dict, token: tuple, as_of: date) -> np.ndarray:
        if self._cache_as_of != as_of:
            self._cache, self._cache_as_of = {}, as_of
        key = (id(store), token)
        if key not in self._cache:
            samples = store.get(token)
            self._cache[key] = (
                samples.sorted_values(as_of, self.config.guard_days) if samples else np.empty(0)
            )
        return self._cache[key]

    def _minimums(self) -> dict:
        c = self.config
        return {"client": c.min_client, "industry": c.min_industry, "global": c.min_global}

    def estimate_delay(self, store, account, direction, inn, ref_date, as_of) -> DelayEstimate:
        """Иерархия клиент -> отрасль -> все. Использует только оплаты, завершённые до as_of."""
        lower = float((as_of - ref_date).days + 1) if self.config.condition else 0.0
        minimums = self._minimums()
        for level, key in self._levels(account, direction, inn):
            values = _conditional(self._sorted(store, (level, *key), as_of), lower)
            if values.size >= minimums[level]:
                q = _quantiles(values)
                return DelayEstimate(level, int(values.size), q, q["q50"])
        base = max(lower, float((as_of - ref_date).days + self.config.fallback_days))
        days = int(base)
        return DelayEstimate("fallback", 0, {k: days for k in QUANTILES}, days)

    def estimate_ratio(self, account, direction, inn, as_of) -> tuple[float, str, int]:
        minimums = self._minimums()
        for level, key in self._levels(account, direction, inn):
            values = self._sorted(self._ratio, (level, *key), as_of)
            if values.size >= minimums[level]:
                return float(np.median(values)), level, int(values.size)
        return 1.0, "fallback", 0

    # -- реестр
    def _shift(self, ref: date, days: int, as_of: date) -> date:
        return next_working_day(max(ref + timedelta(days=days), as_of + timedelta(days=1)))

    def registry(self, account_id: str, as_of: date, horizon_days: int | None = None):
        """Ожидаемые события реестра на as_of. Читает платежи документа только для суммы."""
        events = []
        for doc in self.docs_by_account.get(account_id, ()):
            if doc.doc_date > as_of:
                continue
            future = doc.future(as_of)
            if not future:
                continue
            est = self.estimate_delay(
                self._delay, account_id, doc.direction, doc.inn, doc.doc_date, as_of
            )
            events.append(
                self._event(
                    doc.ref.text,
                    doc.category,
                    doc,
                    est,
                    doc.doc_date,
                    as_of,
                    sum(a for _, a in future),
                    "invoice",
                    doc.doc_id,
                    doc.paid_until(as_of),
                )
            )
        for cyc in self.cycles_by_account.get(account_id, ()):
            if cyc.settle is None or cyc.prepay[0] > as_of or cyc.settle[0] <= as_of:
                continue
            est = self.estimate_delay(
                self._lag, account_id, cyc.direction, cyc.inn, cyc.prepay[0], as_of
            )
            ratio, level, n = self.estimate_ratio(account_id, cyc.direction, cyc.inn, as_of)
            amount = int(round(cyc.prepay[1] * ratio))
            if amount <= 0:
                continue
            category = (
                "contract_settlement"
                if cyc.prepay_category == "contract_prepayment"
                else (cyc.settle_category or cyc.prepay_category)
            )
            events.append(
                self._event(
                    f"договор №{cyc.number} (окончательный расчёт)",
                    category,
                    cyc,
                    est,
                    cyc.prepay[0],
                    as_of,
                    amount,
                    "contract_settlement",
                    cyc.cycle_id,
                    0,
                    ratio_info=(level, n),
                )
            )
        if horizon_days is not None:
            limit = as_of + timedelta(days=horizon_days)
            events = [e for e in events if e.date <= limit]
        events.sort(key=lambda e: (e.date, e.component_id))
        return events

    def _event(
        self, ref_text, category, obj, est, ref_date, as_of, amount, mechanism, cid, paid, **_
    ):
        q = est.quantiles
        return RegistryEvent(
            date=self._shift(ref_date, est.median_days, as_of),
            amount_minor=amount,
            direction=obj.direction,
            category=category,
            counterparty_inn=obj.inn,
            counterparty_name=obj.name,
            document_ref=ref_text,
            known_at=ref_date,
            source=SOURCE,
            mechanism=mechanism,
            component_id=cid,
            delay_level=est.level,
            delay_n=est.n,
            delay_quantiles=dict(q),
            window=(self._shift(ref_date, q["q10"], as_of), self._shift(ref_date, q["q90"], as_of)),
            paid_before_minor=paid,
        )

    # -- принадлежность будущих операций реестру (для X4)
    def owned_payments(self, account_id: str, as_of: date) -> list[tuple[date, int, str, str]]:
        """Будущие платежи (> as_of), принадлежащие реестру на as_of.

        (дата, копейки, direction, category). Это операции, которые X4 НЕ должен прогнозировать
        другими источниками: оплаты документов с датой <= as_of и окончательные расчёты по
        договорам с предоплатой <= as_of. Для оценки; в прогноз не попадает.
        """
        out = []
        for doc in self.docs_by_account.get(account_id, ()):
            if doc.doc_date <= as_of:
                out += [(d, a, doc.direction, doc.category) for d, a in doc.future(as_of)]
        for cyc in self.cycles_by_account.get(account_id, ()):
            if cyc.settle and cyc.prepay[0] <= as_of < cyc.settle[0]:
                category = (
                    "contract_settlement"
                    if cyc.prepay_category == "contract_prepayment"
                    else (cyc.settle_category or cyc.prepay_category)
                )
                out.append((cyc.settle[0], cyc.settle[1], cyc.direction, category))
        return out

    def actual(self, event: RegistryEvent) -> list[tuple[date, int]]:
        """Фактические оплаты события. Только для оценки: в прогноз не попадает."""
        if event.mechanism == "invoice":
            return list(self.doc_index[event.component_id].payments)
        cyc = self.cycle_index[event.component_id]
        return [cyc.settle] if cyc.settle else []


def owned_categories() -> dict:
    """Пары (направление -> категории), которыми владеет реестр: документы и договоры."""
    result = {}
    for direction in ("in", "out"):
        result[direction] = sorted(DOC_CATEGORIES[direction] | CONTRACT_CATEGORIES[direction])
    result["in"] = sorted(set(result["in"]) | {"contract_settlement"})
    return result


__all__ = [
    "SOURCE",
    "DelayConfig",
    "DocPayment",
    "DocRef",
    "Document",
    "ContractCycle",
    "DocumentUniverse",
    "ParsedPurpose",
    "RegistryEvent",
    "build_contract_cycles",
    "build_documents",
    "extract_payments",
    "owned_categories",
    "parse_purpose",
]
