import base64
import os
import secrets
import uuid
from pathlib import Path

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import httpx
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from dotenv import load_dotenv
from flask import Response, request

from apps.dashboard.calendar import (
    api_error_message,
    build_event_request,
    calendar_view,
    form_dates,
    inclusion,
    matching_base,
    save_message,
)
from cashflow_domain.money import to_minor
from cashflow_domain.schemas import ASSUMPTION_SOURCES, CATEGORY_LABELS

load_dotenv()
COMBO_OPTIONS = [
    {"label": " снижение выручки", "value": "revenue_drop"},
    {"label": " рост постоянных расходов", "value": "fixed_cost_increase"},
    {"label": " крупный клиент платит позже", "value": "client_delay"},
    {"label": " дополнительный расход", "value": "extra_expense"},
]
SCENARIO_FIELDS = {
    "delay": {"delay-fields"},
    "client_delay": {"client-fields"},
    "revenue_drop": {"drop-fields"},
    "fixed_cost_increase": {"fixed-fields"},
    "extra_expense": {"extra-fields"},
    "combined": {"combo-fields", "client-fields", "drop-fields", "fixed-fields", "extra-fields"},
}
FIELD_BLOCKS = [
    "delay-fields",
    "client-fields",
    "drop-fields",
    "fixed-fields",
    "extra-fields",
    "combo-fields",
]
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
TOKEN = os.getenv("DASH_TOKEN", "")


def api(method, path, **kwargs):
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    try:
        response = httpx.request(method, API_URL + path, headers=headers, timeout=120, **kwargs)
        if response.status_code >= 400:
            detail = response.json().get("detail", "Не удалось выполнить действие")
            raise ValueError(api_error_message(detail))
        return response.json()
    except httpx.HTTPError as exc:
        raise ValueError("API недоступен. Запустите сервер по README.") from exc


def rub(minor):
    return f"{minor / 100:,.0f}".replace(",", " ") + " ₽"


def field(label, component):
    return html.Div([html.Label(label, htmlFor=component.id), component], className="field")


def metric(label, value, hint, accent=False):
    return html.Div(
        [
            html.Div(label, className="metric-label"),
            html.Div(value, className="metric-value"),
            html.Div(hint, className="metric-hint"),
        ],
        className="metric accent" if accent else "metric",
    )


def grid(grid_id, columns):
    return dag.AgGrid(
        id=grid_id,
        columnDefs=columns,
        rowData=[],
        defaultColDef={
            "resizable": True,
            "sortable": True,
            "filter": True,
            "flex": 1,
            "minWidth": 130,
        },
        dashGridOptions={"pagination": True, "paginationPageSize": 15, "animateRows": False},
        rowClassRules={
            "event-assumption": "['regular', 'tax_calendar', 'documents'].includes(data.kind)",
            "event-confirmed": "!['regular', 'tax_calendar', 'documents'].includes(data.kind)",
        }
        if grid_id == "events-grid"
        else {},
        className="ag-theme-quartz",
        style={"height": "430px", "width": "100%"},
    )


app = Dash(
    __name__,
    assets_folder=str(Path(__file__).parent / "assets"),
    title="Kassandra · Денежный поток",
    update_title="Расчёт…",
)
server = app.server


@server.before_request
def protect_dashboard():
    password = os.getenv("DASH_PASSWORD", "")
    if password:
        auth = request.authorization
        if (
            not auth
            or auth.username != "cashflow"
            or not secrets.compare_digest(auth.password or "", password)
        ):
            return Response("Требуется вход", 401, {"WWW-Authenticate": 'Basic realm="Kassandra"'})
    return None


def layout():
    try:
        accounts = api("GET", "/api/v1/accounts")
        startup = ""
    except ValueError as exc:
        accounts, startup = [], str(exc)
    options = [{"label": f"{a['id']} · {a['name']}", "value": a["id"]} for a in accounts]
    default = (
        "C002"
        if any(a["id"] == "C002" for a in accounts)
        else accounts[0]["id"]
        if accounts
        else None
    )
    return html.Div(
        [
            dcc.Store(id="base"),
            dcc.Store(id="scenario"),
            dcc.Store(id="revision", data=0),
            dcc.Store(id="event-session", data=str(uuid.uuid4())),
            dcc.Store(id="event-context"),
            html.Aside(
                [
                    html.Div(
                        [
                            html.Span("↗", className="brand-mark"),
                            html.Div([html.Strong("CashFlow"), html.Span("PRO", className="pro")]),
                        ],
                        className="brand",
                    ),
                    html.Div("ФИНАНСОВЫЙ ГОРИЗОНТ", className="side-label"),
                    html.Div("Ваши деньги.\nНа 30 дней вперёд.", className="side-title"),
                    html.Div(
                        [html.Span("◉"), " Историческое воспроизведение"], className="side-mode"
                    ),
                    html.Div(
                        [
                            html.Div("01", className="step-number"),
                            html.Div(
                                [
                                    html.Strong("Выберите счёт"),
                                    html.P("150 демонстрационных компаний"),
                                ]
                            ),
                        ],
                        className="side-step",
                    ),
                    html.Div(
                        [
                            html.Div("02", className="step-number"),
                            html.Div(
                                [html.Strong("Посмотрите прогноз"), html.P("Факт, ожидания и риск")]
                            ),
                        ],
                        className="side-step",
                    ),
                    html.Div(
                        [
                            html.Div("03", className="step-number"),
                            html.Div(
                                [
                                    html.Strong("Проверьте сценарий"),
                                    html.P("Подготовьтесь к изменениям"),
                                ]
                            ),
                        ],
                        className="side-step",
                    ),
                    html.Div(
                        [
                            html.Div("СИНТЕТИЧЕСКИЕ ВЫПИСКИ", className="side-label"),
                            html.P("01.10.2024 — 30.09.2026"),
                            html.Small(
                                "Один банковский счёт. Данные не отражают всю ликвидность бизнеса."
                            ),
                        ],
                        className="side-bottom",
                    ),
                ],
                className="sidebar",
            ),
            html.Main(
                [
                    html.Header(
                        [
                            html.Div(
                                [
                                    html.Div("ОБЗОР / ДЕНЕЖНЫЙ ПОТОК", className="eyebrow"),
                                    html.H1("Будущее под контролем"),
                                    html.P(
                                        "Планируйте платежи и проверяйте, хватит ли денег на важное.",
                                        className="subtitle",
                                    ),
                                ]
                            ),
                            html.Span("●  Локальный прототип", className="status-badge"),
                        ],
                        className="page-header",
                    ),
                    html.Div(
                        [
                            field(
                                "Компания и счёт",
                                dcc.Dropdown(
                                    id="account", options=options, value=default, clearable=False
                                ),
                            ),
                            field(
                                "Дата расчёта · конец дня",
                                dbc.Input(id="asof", type="date", value="2026-08-31"),
                            ),
                            field(
                                "Метод прогноза",
                                dcc.Dropdown(
                                    id="model",
                                    options=[
                                        {"label": "HistGradientBoosting · ML", "value": "hgb"},
                                        {
                                            "label": "Структурная · механизмы и налоги, события",
                                            "value": "structural",
                                        },
                                        {"label": "По дням недели · базовый", "value": "weekday"},
                                        {"label": "Средний поток · 60 дней", "value": "mean"},
                                        {
                                            "label": "Последний остаток · ориентир",
                                            "value": "constant",
                                        },
                                    ],
                                    value="hgb",
                                    clearable=False,
                                ),
                            ),
                            html.Button(
                                "Рассчитать прогноз ↗",
                                id="forecast-button",
                                n_clicks=0,
                                className="primary-button",
                            ),
                        ],
                        className="toolbar",
                    ),
                    html.Details(
                        [
                            html.Summary("Допущения и порог ликвидности"),
                            html.Div(
                                [
                                    dbc.Checklist(
                                        id="calendar",
                                        options=[
                                            {
                                                "label": "Выделять регулярные компоненты из истории",
                                                "value": "on",
                                            }
                                        ],
                                        value=["on"],
                                        switch=True,
                                    ),
                                    field(
                                        "Резерв, ₽",
                                        dbc.Input(
                                            id="reserve", type="number", value=0, min=0, step=0.01
                                        ),
                                    ),
                                    field(
                                        "Подтверждённый доступный лимит, ₽",
                                        dbc.Input(
                                            id="credit", type="number", value=0, min=0, step=0.01
                                        ),
                                    ),
                                ],
                                className="assumption-controls",
                            ),
                        ],
                        className="assumptions",
                    ),
                    html.Div(startup, id="forecast-message", role="status", className="message"),
                    dcc.Loading(
                        html.Div(id="metrics", className="metrics"), type="circle", color="#16877b"
                    ),
                    dcc.Tabs(
                        id="view",
                        value="forecast",
                        children=[
                            dcc.Tab(label="Прогноз и сценарии", value="forecast"),
                            dcc.Tab(label="Календарь событий", value="events"),
                            dcc.Tab(label="Операции и качество", value="data"),
                            dcc.Tab(label="План-факт", value="plan"),
                            dcc.Tab(label="Технический режим", value="research"),
                        ],
                        className="tabs",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H2("Траектория остатка"),
                                                    html.P(
                                                        "Факт → прогноз на 30 календарных дней",
                                                        className="muted",
                                                    ),
                                                ]
                                            ),
                                            html.Span("RUB / ₽", className="currency-tag"),
                                        ],
                                        className="panel-heading",
                                    ),
                                    dcc.Graph(
                                        id="trajectory",
                                        responsive=True,
                                        config={"displayModeBar": False},
                                        style={"height": "370px"},
                                    ),
                                    html.Div(id="versions", className="version-caption"),
                                ],
                                className="panel graph-panel",
                            ),
                            html.Div(
                                [
                                    html.Div("ЧТО ЕСЛИ", className="eyebrow"),
                                    html.H2("Проверка на прочность"),
                                    html.P(
                                        "Измените одно предположение и сравните результат с сохранённой базой.",
                                        className="muted",
                                    ),
                                    field(
                                        "Изменение",
                                        dcc.Dropdown(
                                            id="scenario-kind",
                                            options=[
                                                {"label": "Задержка поступления", "value": "delay"},
                                                {
                                                    "label": "Крупный клиент заплатит позже",
                                                    "value": "client_delay",
                                                },
                                                {
                                                    "label": "Снижение выручки",
                                                    "value": "revenue_drop",
                                                },
                                                {
                                                    "label": "Рост постоянных расходов",
                                                    "value": "fixed_cost_increase",
                                                },
                                                {
                                                    "label": "Дополнительный расход",
                                                    "value": "extra_expense",
                                                },
                                                {
                                                    "label": "Несколько изменений сразу",
                                                    "value": "combined",
                                                },
                                            ],
                                            value="delay",
                                            clearable=False,
                                        ),
                                    ),
                                    html.Div(
                                        [
                                            field(
                                                "Поступление из календаря",
                                                dcc.Dropdown(
                                                    id="scenario-event",
                                                    options=[],
                                                    placeholder="Выберите событие",
                                                ),
                                            ),
                                            field(
                                                "Задержка, календарных дней",
                                                dbc.Input(
                                                    id="delay",
                                                    type="number",
                                                    value=14,
                                                    min=1,
                                                    max=365,
                                                ),
                                            ),
                                        ],
                                        id="delay-fields",
                                    ),
                                    html.Div(
                                        [
                                            field(
                                                "Клиент (топ по поступлениям за 90 дней)",
                                                dcc.Dropdown(
                                                    id="scenario-client",
                                                    options=[],
                                                    placeholder="Выберите клиента",
                                                ),
                                            ),
                                            field(
                                                "Клиент заплатит позже на, дней (1–60)",
                                                dbc.Input(
                                                    id="client-delay",
                                                    type="number",
                                                    value=14,
                                                    min=1,
                                                    max=60,
                                                ),
                                            ),
                                        ],
                                        id="client-fields",
                                        style={"display": "none"},
                                    ),
                                    html.Div(
                                        [
                                            field(
                                                "Рост постоянных расходов, %",
                                                dbc.Input(
                                                    id="fixed-pct",
                                                    type="number",
                                                    value=10,
                                                    min=0,
                                                    max=300,
                                                ),
                                            ),
                                            field(
                                                "С какой даты (пусто: со следующего дня)",
                                                dbc.Input(id="fixed-start", type="date"),
                                            ),
                                        ],
                                        id="fixed-fields",
                                        style={"display": "none"},
                                    ),
                                    html.Div(
                                        field(
                                            "Что изменить одновременно",
                                            dcc.Checklist(
                                                id="combo-parts",
                                                options=COMBO_OPTIONS,
                                                value=["revenue_drop", "fixed_cost_increase"],
                                            ),
                                        ),
                                        id="combo-fields",
                                        style={"display": "none"},
                                    ),
                                    html.Div(
                                        field(
                                            "Снижение операционной выручки, %",
                                            dbc.Input(
                                                id="drop", type="number", value=20, min=0, max=100
                                            ),
                                        ),
                                        id="drop-fields",
                                        style={"display": "none"},
                                    ),
                                    html.Div(
                                        [
                                            field(
                                                "Дополнительный расход, ₽",
                                                dbc.Input(
                                                    id="extra",
                                                    type="number",
                                                    value=100000,
                                                    min=0.01,
                                                    step=0.01,
                                                ),
                                            ),
                                            field(
                                                "Дата расхода",
                                                dbc.Input(
                                                    id="extra-date", type="date", value="2026-09-15"
                                                ),
                                            ),
                                        ],
                                        id="extra-fields",
                                        style={"display": "none"},
                                    ),
                                    html.Button(
                                        "Сравнить с базой",
                                        id="scenario-button",
                                        n_clicks=0,
                                        className="secondary-button",
                                    ),
                                    html.Div(
                                        id="scenario-message",
                                        role="status",
                                        className="scenario-result",
                                    ),
                                ],
                                className="panel scenario-panel",
                            ),
                            html.Div(id="explanation", className="panel explain-panel"),
                            html.Div(id="warnings", className="panel warning-panel"),
                        ],
                        id="forecast-section",
                        className="forecast-layout",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H2("Календарь будущих движений"),
                                    html.P(
                                        "Все сохранённые события видны в реестре. Отдельная отметка показывает, включено ли событие в прогноз на выбранную дату.",
                                        className="muted",
                                    ),
                                    html.Div(
                                        id="calendar-message", role="status", className="message"
                                    ),
                                    grid(
                                        "events-grid",
                                        [
                                            {
                                                "field": k,
                                                "headerName": v,
                                                "tooltipField": k,
                                                **(
                                                    {
                                                        "minWidth": 220,
                                                        "wrapText": True,
                                                        "autoHeight": True,
                                                    }
                                                    if k == "inclusion"
                                                    else {}
                                                ),
                                                **(
                                                    {
                                                        "minWidth": 160,
                                                        "wrapText": True,
                                                        "autoHeight": True,
                                                    }
                                                    if k in {"label", "source"}
                                                    else {}
                                                ),
                                            }
                                            for k, v in [
                                                ("date", "Ожидаемая дата"),
                                                ("known_at", "Известно на дату"),
                                                ("label", "Событие"),
                                                ("amount", "Осталось, ₽"),
                                                ("direction", "Направление"),
                                                ("source", "Источник / статус"),
                                                ("inclusion", "Участие в прогнозе"),
                                                ("id", "ID"),
                                            ]
                                        ],
                                    ),
                                ],
                                className="panel",
                            ),
                            html.Div(
                                [
                                    html.H2("Добавить подтверждённое событие"),
                                    html.P(
                                        "При совпадении выберите ожидание для замещения. Дополнительное движение добавляется только по вашему явному выбору.",
                                        className="muted",
                                    ),
                                    html.Div(
                                        [
                                            field(
                                                "Название",
                                                dbc.Input(
                                                    id="event-label", value="Оплата от клиента"
                                                ),
                                            ),
                                            field(
                                                "Сумма, ₽",
                                                dbc.Input(
                                                    id="event-amount",
                                                    type="number",
                                                    value=300000,
                                                    min=0.01,
                                                    step=0.01,
                                                ),
                                            ),
                                            field(
                                                "Направление",
                                                dcc.Dropdown(
                                                    id="event-direction",
                                                    options=[
                                                        {"label": "Поступление", "value": "in"},
                                                        {"label": "Списание", "value": "out"},
                                                    ],
                                                    value="in",
                                                    clearable=False,
                                                ),
                                            ),
                                            field(
                                                "Категория",
                                                dcc.Dropdown(
                                                    id="event-category",
                                                    options=[
                                                        {"label": v, "value": k}
                                                        for k, v in CATEGORY_LABELS.items()
                                                    ],
                                                    value="revenue",
                                                    clearable=False,
                                                ),
                                            ),
                                            field(
                                                "Срок оплаты",
                                                dbc.Input(
                                                    id="event-due", type="date", value="2026-09-15"
                                                ),
                                            ),
                                            field(
                                                "Ожидаемая дата",
                                                dbc.Input(
                                                    id="event-date", type="date", value="2026-09-15"
                                                ),
                                            ),
                                            field(
                                                "Известно на дату",
                                                dbc.Input(
                                                    id="event-known",
                                                    type="date",
                                                    value="2026-08-31",
                                                ),
                                            ),
                                            field(
                                                "Решение о сопоставлении",
                                                dcc.Dropdown(
                                                    id="event-replacement",
                                                    options=[
                                                        {
                                                            "label": "Новое дополнительное событие",
                                                            "value": "additional",
                                                        }
                                                    ],
                                                    value="additional",
                                                    clearable=False,
                                                ),
                                            ),
                                        ],
                                        className="form-grid",
                                    ),
                                    html.Div(
                                        id="event-preview", role="status", className="message"
                                    ),
                                    html.Div(
                                        id="event-message",
                                        role="status",
                                        className="message event-feedback",
                                    ),
                                    html.Button(
                                        "Подтвердить и добавить событие",
                                        id="event-button",
                                        n_clicks=0,
                                        className="primary-button",
                                    ),
                                    html.Button(
                                        "Новое событие",
                                        id="new-event-button",
                                        n_clicks=0,
                                        className="secondary-button new-event-button",
                                    ),
                                    dcc.Upload(
                                        html.Button(
                                            "Импорт CSV-реестра", className="secondary-button"
                                        ),
                                        id="event-upload",
                                        multiple=False,
                                    ),
                                ],
                                className="panel",
                            ),
                            html.Div(
                                [
                                    html.H2("Оплата и новая ожидаемая дата"),
                                    html.P(
                                        "Оплата связывается с уже загруженной операцией. Остаток события уменьшается без повторного зачисления денег.",
                                        className="muted",
                                    ),
                                    html.Div(
                                        [
                                            field(
                                                "Подтверждённое событие",
                                                dcc.Dropdown(id="payment-event", options=[]),
                                            ),
                                            field(
                                                "ID фактической операции",
                                                dbc.Input(
                                                    id="payment-operation",
                                                    placeholder="ID из таблицы операций",
                                                ),
                                            ),
                                            field(
                                                "Оплаченная часть, ₽",
                                                dbc.Input(
                                                    id="payment-amount",
                                                    type="number",
                                                    value=100000,
                                                    min=0.01,
                                                ),
                                            ),
                                            field(
                                                "Дата знания об оплате",
                                                dbc.Input(
                                                    id="payment-known",
                                                    type="date",
                                                    value="2026-09-15",
                                                ),
                                            ),
                                            field(
                                                "Новая ожидаемая дата",
                                                dbc.Input(
                                                    id="reschedule-date",
                                                    type="date",
                                                    value="2026-09-25",
                                                ),
                                            ),
                                        ],
                                        className="form-grid",
                                    ),
                                    html.Button(
                                        "Связать частичную оплату",
                                        id="payment-button",
                                        className="secondary-button",
                                        n_clicks=0,
                                    ),
                                    html.Button(
                                        "Сохранить новую дату",
                                        id="reschedule-button",
                                        className="secondary-button",
                                        n_clicks=0,
                                    ),
                                    html.Div(
                                        id="payment-message", role="status", className="message"
                                    ),
                                ],
                                className="panel",
                            ),
                        ],
                        id="events-section",
                        className="section-stack",
                        style={"display": "none"},
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H2("Импорт банковской выписки"),
                                    html.P(
                                        "CSV · UTF-8 с BOM · разделитель ; · ИНН сохраняется строкой. Повторный импорт безопасен.",
                                        className="muted",
                                    ),
                                    dcc.Upload(
                                        html.Div(
                                            [
                                                html.Strong("Выберите CSV выписки"),
                                                html.Span(" или перетащите файл сюда"),
                                            ]
                                        ),
                                        id="tx-upload",
                                        multiple=False,
                                        className="upload-box",
                                    ),
                                    html.Div(
                                        id="import-message", role="status", className="message"
                                    ),
                                    html.Div(id="quality"),
                                ],
                                className="panel",
                            ),
                            html.Div(
                                [
                                    html.H2("Фактические операции"),
                                    html.Div(
                                        [
                                            html.Button(
                                                "← Предыдущие",
                                                id="tx-prev",
                                                className="text-button",
                                                n_clicks=0,
                                            ),
                                            html.Span(id="tx-page-label"),
                                            html.Button(
                                                "Следующие →",
                                                id="tx-next",
                                                className="text-button",
                                                n_clicks=0,
                                            ),
                                        ],
                                        className="pagination-controls",
                                    ),
                                    dcc.Store(id="tx-page", data=1),
                                    grid(
                                        "tx-grid",
                                        [
                                            {"field": k, "headerName": v}
                                            for k, v in [
                                                ("operation_date", "Дата"),
                                                ("operation_id", "ID"),
                                                ("amount", "Сумма, ₽"),
                                                ("direction_label", "Направление"),
                                                ("category_label", "Категория"),
                                                ("payment_purpose", "Назначение"),
                                            ]
                                        ],
                                    ),
                                    html.Div(
                                        [
                                            field(
                                                "ID операции для правки",
                                                dbc.Input(
                                                    id="edit-operation",
                                                    placeholder="Скопируйте ID из таблицы",
                                                ),
                                            ),
                                            field(
                                                "Новая категория",
                                                dcc.Dropdown(
                                                    id="edit-category",
                                                    options=[
                                                        {"label": v, "value": k}
                                                        for k, v in CATEGORY_LABELS.items()
                                                    ],
                                                    value="unknown",
                                                ),
                                            ),
                                            html.Button(
                                                "Сохранить категорию",
                                                id="category-button",
                                                n_clicks=0,
                                                className="secondary-button",
                                            ),
                                        ],
                                        className="form-grid",
                                    ),
                                    html.Div(
                                        id="category-message", role="status", className="message"
                                    ),
                                ],
                                className="panel",
                            ),
                        ],
                        id="data-section",
                        className="section-stack",
                        style={"display": "none"},
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H2("Неизменяемые версии плана"),
                                    field(
                                        "Сохранённый прогноз",
                                        dcc.Dropdown(
                                            id="saved-run",
                                            options=[],
                                            placeholder="Выберите сохранённую версию",
                                        ),
                                    ),
                                    dcc.Graph(
                                        id="plan-graph",
                                        responsive=True,
                                        config={"displayModeBar": False},
                                    ),
                                    html.Div(id="plan-info", className="muted"),
                                    grid(
                                        "plan-grid",
                                        [
                                            {"field": k, "headerName": v}
                                            for k, v in [
                                                ("date", "Дата"),
                                                ("planned", "План, ₽"),
                                                ("actual", "Факт, ₽"),
                                                ("delta", "Отклонение, ₽"),
                                                ("coverage", "Покрытие"),
                                            ]
                                        ],
                                    ),
                                ],
                                className="panel",
                            )
                        ],
                        id="plan-section",
                        style={"display": "none"},
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H2("Проверенные результаты"),
                                    html.P(
                                        "Сравнение на общих окнах без календаря. Полная матрица 2×2 — следующая задача E03.",
                                        className="muted",
                                    ),
                                    html.Div(id="research"),
                                ],
                                className="panel",
                            )
                        ],
                        id="research-section",
                        style={"display": "none"},
                    ),
                    html.Footer(
                        "Kassandra · технический режим · Прогноз — оценка по доступной истории, регулярность — предположение."
                    ),
                ],
                className="main-content",
            ),
        ],
        className="app-shell",
    )


app.layout = layout


@app.callback(
    Output("event-known", "value"),
    Output("event-due", "value"),
    Output("event-date", "value"),
    Output("payment-known", "value"),
    Output("reschedule-date", "value"),
    Output("extra-date", "value"),
    Output("event-context", "data"),
    Input("account", "value"),
    Input("asof", "value"),
    Input("new-event-button", "n_clicks"),
    State("event-context", "data"),
)
def sync_context_dates(account, asof, _, previous):
    context = {"account_id": account, "as_of": asof}
    if context == previous and ctx.triggered_id != "new-event-button":
        return (no_update,) * 7
    try:
        known, expected = form_dates(asof)
    except ValueError:
        return (no_update,) * 7
    return known, expected, expected, known, expected, expected, context


@app.callback(
    Output("event-replacement", "value"),
    Input("account", "value"),
    Input("asof", "value"),
    Input("new-event-button", "n_clicks"),
)
def reset_matching(account, asof, _):
    return "additional"


@app.callback(
    Output("event-direction", "value"),
    Output("event-category", "value"),
    Output("event-amount", "value"),
    Output("event-direction", "disabled"),
    Output("event-category", "disabled"),
    Output("event-due", "value", allow_duplicate=True),
    Output("event-date", "value", allow_duplicate=True),
    Output("event-known", "value", allow_duplicate=True),
    Input("event-replacement", "value"),
    State("base", "data"),
    State("account", "value"),
    State("asof", "value"),
    prevent_initial_call=True,
)
def sync_replacement(replacement, base, account, asof):
    if replacement == "additional":
        return "in", "revenue", no_update, False, False, no_update, no_update, no_update
    if matching_base(base, account, asof):
        target = next(
            (
                e
                for e in base["events"]
                if e["id"] == replacement and e["source"] in ASSUMPTION_SOURCES
            ),
            None,
        )
        if target:
            return (
                target["direction"],
                target["category"],
                target["amount_minor"] / 100,
                True,
                True,
                target["due_date"],
                target["expected_date"],
                asof,
            )
    return no_update, no_update, no_update, False, False, no_update, no_update, no_update


@app.callback(
    Output("event-session", "data"),
    Output("event-label", "value"),
    Output("event-amount", "value", allow_duplicate=True),
    Input("new-event-button", "n_clicks"),
    prevent_initial_call=True,
)
def new_event(_):
    return str(uuid.uuid4()), "", None


@app.callback(
    Output("event-preview", "children"),
    Input("account", "value"),
    Input("asof", "value"),
    Input("base", "data"),
    Input("event-label", "value"),
    Input("event-amount", "value"),
    Input("event-direction", "value"),
    Input("event-category", "value"),
    Input("event-due", "value"),
    Input("event-date", "value"),
    Input("event-known", "value"),
    Input("event-replacement", "value"),
    State("event-session", "data"),
)
def event_preview(
    account,
    asof,
    base,
    label,
    amount,
    direction,
    category,
    due,
    expected,
    known,
    replacement,
    session,
):
    try:
        event = build_event_request(
            account_id=account,
            as_of=asof,
            base=base,
            session_id=session,
            label=label,
            amount=amount,
            direction=direction,
            category=category,
            due=due,
            expected=expected,
            known=known,
            replacement=replacement,
        )["event"]
        state = inclusion(event, asof, False)
        if state == "Ожидает расчёта прогноза":
            return f"Счёт {account} · срез {asof}. Событие на {expected} будет учтено после сохранения и расчёта."
        return f"Счёт {account} · срез {asof}. Событие сохранится в реестре, но не войдёт в этот прогноз: {state.lower()}."
    except ValueError as exc:
        return str(exc)


@app.callback(
    Output("events-grid", "rowData"),
    Output("payment-event", "options"),
    Output("calendar-message", "children"),
    Input("account", "value"),
    Input("asof", "value"),
    Input("base", "data"),
    Input("revision", "data"),
)
def render_calendar(account, asof, base, revision):
    if not account:
        return [], [], "Выберите счёт. Реестр работает и без готового прогноза."
    try:
        form_dates(asof)
        effective = api("GET", f"/api/v1/accounts/{account}/events", params={"as_of": asof})
        registered = api("GET", f"/api/v1/accounts/{account}/events")
        rows, payments = calendar_view(base, effective, registered, account, asof)
        excluded = sum(row["inclusion"] != "В прогнозе" for row in rows)
        return (
            rows,
            payments,
            f"Срез {asof} · сохранённых событий: {len(registered)} · строк вне текущего прогноза: {excluded}. Причина указана у каждой строки.",
        )
    except ValueError as exc:
        return [], [], str(exc)


@app.callback(
    [
        Output(f"{name}-section", "style")
        for name in ["forecast", "events", "data", "plan", "research"]
    ],
    Input("view", "value"),
)
def switch_tab(view):
    return [
        {} if view == name else {"display": "none"}
        for name in ["forecast", "events", "data", "plan", "research"]
    ]


@app.callback(
    Output("base", "data"),
    Output("forecast-message", "children"),
    Input("forecast-button", "n_clicks"),
    Input("account", "value"),
    Input("revision", "data"),
    Input("asof", "value"),
    State("model", "value"),
    State("calendar", "value"),
    State("reserve", "value"),
    State("credit", "value"),
)
def forecast(_, account, revision, asof, model, calendar, reserve, credit):
    if not account:
        return None, "Импортируйте демонстрационные данные командой cashflow bootstrap."
    try:
        result = api(
            "POST",
            "/api/v1/forecasts",
            json={
                "account_id": account,
                "as_of": asof,
                "model": model,
                "calendar": "on" in (calendar or []),
                "reserve_minor": to_minor(str(reserve or 0)),
                "credit_limit_minor": to_minor(str(credit or 0)),
            },
        )
        return (
            result,
            f"Срез {asof} · последняя операция в выписке: {result['latest_operation_date']} · сохранена новая версия плана",
        )
    except ValueError as exc:
        return None, str(exc)


def mark_gap(fig, base, points, risk, name, color):
    """Marker on the first threshold breach with its date and the amount that is missing."""
    gap = risk["first_gap_date"]
    if not gap:
        return
    point = next(p for p in points if p["date"] == gap)
    need = risk["liquidity_need_minor"]
    fig.add_trace(
        go.Scatter(
            x=[gap],
            y=[point["closing_balance_minor"] / 100],
            mode="markers+text",
            name=name,
            marker={"color": color, "size": 11, "symbol": "x"},
            text=[f"{gap[8:]}.{gap[5:7]} · не хватит {rub(need)}"],
            textposition="top center",
            hovertemplate="%{x}: не хватит " + rub(need) + " до порога<extra></extra>",
        )
    )


def figure(base, comparison=None):
    fig = go.Figure()
    if base:
        history, points = base["history"], base["points"]
        fig.add_trace(
            go.Scatter(
                x=[d["date"] for d in history],
                y=[d["balance_minor"] / 100 for d in history],
                name="Факт",
                line={"color": "#839b99", "width": 2.5},
            )
        )
        if points[0].get("p10_minor") is not None:
            x = [p["date"] for p in points]
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=[p["p90_minor"] / 100 for p in points],
                    line={"width": 0},
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=[p["p10_minor"] / 100 for p in points],
                    line={"width": 0},
                    fill="tonexty",
                    fillcolor="rgba(22,135,123,.10)",
                    name="P10–P90 · экспериментально",
                    hoverinfo="skip",
                )
            )
        x = [base["as_of"], *[p["date"] for p in points]]
        fig.add_trace(
            go.Scatter(
                x=x,
                y=[
                    base["opening_balance_minor"] / 100,
                    *[p["closing_balance_minor"] / 100 for p in points],
                ],
                name="Базовый прогноз",
                line={"color": "#16877b", "width": 3},
            )
        )
        if comparison:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=[
                        base["opening_balance_minor"] / 100,
                        *[p["closing_balance_minor"] / 100 for p in comparison["points"]],
                    ],
                    name="Негативный сценарий",
                    line={"color": "#c46464", "width": 3, "dash": "dot"},
                )
            )
        mark_gap(fig, base, base["points"], base["risk"], "Разрыв (база)", "#16877b")
        if comparison:
            mark_gap(
                fig, base, comparison["points"], comparison["risk"], "Разрыв (сценарий)", "#c46464"
            )
        fig.add_hline(
            y=base["risk"]["threshold_minor"] / 100,
            line_color="#c46464",
            line_dash="dot",
            annotation_text="Порог ликвидности",
            annotation_position="bottom right",
        )
        fig.add_vline(x=base["as_of"], line_color="#d0dbd7", line_dash="dash")
    fig.update_layout(
        template="plotly_white",
        margin={"l": 55, "r": 20, "t": 18, "b": 40},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Arial, sans-serif", "color": "#657a77", "size": 11},
        legend={"orientation": "h", "y": 1.16, "x": 0},
        hovermode="x unified",
        xaxis={"showgrid": False},
        yaxis={"gridcolor": "#edf1ed", "zeroline": False, "tickformat": ",.3s"},
    )
    return fig


@app.callback(
    Output("metrics", "children"),
    Output("trajectory", "figure"),
    Output("warnings", "children"),
    Output("versions", "children"),
    Output("scenario-event", "options"),
    Output("event-replacement", "options"),
    Input("base", "data"),
    Input("scenario", "data"),
)
def render(base, comparison):
    if not base:
        return (
            [],
            figure(None),
            [html.H2("Нет расчёта"), html.P("Выберите доступный счёт и дату внутри покрытия.")],
            "",
            [],
            [{"label": "Новое дополнительное событие", "value": "additional"}],
        )
    if comparison and comparison["base_run_id"] != base["run_id"]:
        comparison = None
    risk = base["risk"]
    probability = (
        "Нет оценки" if risk["probability_30d"] is None else f"{100 * risk['probability_30d']:.1f}%"
    )
    cards = [
        metric("Остаток на дату среза", rub(base["opening_balance_minor"]), base["as_of"]),
        metric(
            "Минимум за 30 дней",
            rub(risk["minimum_balance_minor"]),
            f"Порог {rub(risk['threshold_minor'])}",
            True,
        ),
        metric(
            "Потребность в ликвидности",
            rub(risk["liquidity_need_minor"]),
            "Текущий дефицит"
            if risk["current_gap_minor"]
            else "Новый риск"
            if risk["new_risk"]
            else "По центральной траектории",
        ),
        metric(
            "Вероятность пересечения порога",
            probability,
            "Экспериментально · не калибровано"
            if risk["probability_30d"] is not None
            else "Недостаточно пригодных ошибок",
        ),
    ]
    warnings = [
        html.Div("ОЖИДАНИЯ И ОГРАНИЧЕНИЯ", className="eyebrow"),
        html.H2(
            "Текущий дефицит"
            if risk["current_gap_minor"]
            else f"Риск с {risk['first_gap_date']}"
            if risk["new_risk"]
            else "Центральный прогноз выше порога"
        ),
        html.P(risk["limitation"], className="muted"),
        html.Div(
            f"Уровень доверия: {conf['label']} · история {conf['history_days']} дн."
            + (f" · {conf['limitation']}" if conf.get("limitation") else ""),
            className="muted",
        )
        if (conf := base.get("confidence"))
        else None,
        html.Ul([html.Li(flag) for flag in base["quality_flags"]]),
    ]
    versions = f"as_of {base['as_of']} · модель {base['model_version']} · trained_to {base['trained_to'] or 'не требуется'} · данные v{base['data_version']} · события v{base['event_version']}"
    incoming = [
        {
            "label": f"{e['expected_date']} · {e['label']} · {rub(e['amount_minor'] - e['paid_minor'])}",
            "value": e["id"],
        }
        for e in base["events"]
        if e["direction"] == "in"
        and e["expected_date"] > base["as_of"]
        and e["status"] not in {"paid", "cancelled", "review"}
    ]
    replace = [{"label": "Новое дополнительное событие", "value": "additional"}] + [
        {
            "label": f"Заменить: {e['expected_date']} · {e['label']} · {rub(e['amount_minor'])}",
            "value": e["id"],
        }
        for e in base["events"]
        if e["source"] in ASSUMPTION_SOURCES
    ]
    return (
        cards,
        figure(base, comparison),
        warnings,
        versions,
        incoming,
        replace,
    )


@app.callback(*[Output(block, "style") for block in FIELD_BLOCKS], Input("scenario-kind", "value"))
def scenario_fields(kind):
    shown = SCENARIO_FIELDS.get(kind, set())
    return [{} if block in shown else {"display": "none"} for block in FIELD_BLOCKS]


@app.callback(
    Output("scenario-client", "options"),
    Input("base", "data"),
)
def client_options(base):
    if not base:
        return []
    try:
        rows = api(
            "GET",
            f"/api/v1/accounts/{base['account_id']}/top-clients",
            params={"as_of": base["as_of"], "limit": 10, "run_id": base["run_id"]},
        )
    except ValueError:
        return []
    return [
        {
            "label": f"{r['counterparty_name'] or r['counterparty_inn']} · {rub(r['amount_minor'])} "
            f"({100 * r['share']:.0f}%) · ближайших поступлений: {r.get('upcoming_events', 0)}",
            "value": r["counterparty_inn"] or r["counterparty_name"],
        }
        for r in rows
    ]


def scenario_part(
    kind, event_id, delay, client, client_delay, drop, fixed_pct, fixed_start, extra, extra_date
):
    if kind == "delay":
        return {"kind": kind, "event_id": event_id, "delay_days": int(delay or 14)}
    if kind == "client_delay":
        return {"kind": kind, "counterparty": client, "delay_days": int(client_delay or 14)}
    if kind == "revenue_drop":
        return {"kind": kind, "revenue_drop_pct": drop or 0}
    if kind == "fixed_cost_increase":
        part = {"kind": kind, "fixed_cost_pct": fixed_pct or 0}
        if fixed_start:
            part["start_date"] = fixed_start
        return part
    return {"kind": kind, "amount_minor": to_minor(str(extra or 0)), "expense_date": extra_date}


@app.callback(
    Output("scenario", "data"),
    Output("scenario-message", "children"),
    Input("scenario-button", "n_clicks"),
    Input("base", "data"),
    State("scenario-kind", "value"),
    State("scenario-event", "value"),
    State("delay", "value"),
    State("scenario-client", "value"),
    State("client-delay", "value"),
    State("drop", "value"),
    State("fixed-pct", "value"),
    State("fixed-start", "value"),
    State("extra", "value"),
    State("extra-date", "value"),
    State("combo-parts", "value"),
)
def calculate_scenario(
    clicks,
    base,
    kind,
    event_id,
    delay,
    client,
    client_delay,
    drop,
    fixed_pct,
    fixed_start,
    extra,
    extra_date,
    combo,
):
    if not clicks or not base or ctx.triggered_id == "base":
        return None, ""
    try:
        args = (
            event_id,
            delay,
            client,
            client_delay,
            drop,
            fixed_pct,
            fixed_start,
            extra,
            extra_date,
        )
        if kind == "combined":
            spec = {
                "base_run_id": base["run_id"],
                "kind": kind,
                "parts": [scenario_part(k, *args) for k in (combo or [])],
            }
        else:
            spec = {"base_run_id": base["run_id"], **scenario_part(kind, *args)}
        result = api("POST", "/api/v1/scenarios/evaluate", json=spec)
        return result, [
            html.Strong(
                f"Остаток на день 30: {rub(result['points'][-1]['closing_balance_minor'])}"
            ),
            html.P(f"Изменение к базе: {rub(result['delta_end_minor'])}"),
            html.P(
                f"Потребность: {rub(result['risk']['liquidity_need_minor'])} · дата пересечения: {result['risk']['first_gap_date'] or 'нет'}"
            ),
            html.Small(result["assumptions"][-1]),
        ]
    except (ValueError, TypeError) as exc:
        return None, str(exc)


@app.callback(
    Output("explanation", "children"),
    Output("explanation", "style"),
    Input("base", "data"),
    Input("scenario", "data"),
)
def explanation(base, comparison):
    if not base:
        return [], {"display": "none"}
    if comparison and comparison["base_run_id"] != base["run_id"]:
        comparison = None
    try:
        path = (
            f"/api/v1/scenarios/{comparison['scenario_id']}/explanation"
            if comparison
            else f"/api/v1/forecasts/{base['run_id']}/explanation"
        )
        ex = api("GET", path)
    except ValueError as exc:
        return [html.P(str(exc), className="muted")], {}
    title = "Негативный сценарий" if comparison else "Базовый прогноз"
    if not ex["has_gap"]:
        return [
            html.Div(f"РАЗРЫВ ЛИКВИДНОСТИ · {title.upper()}", className="eyebrow"),
            html.H2("Разрыва нет"),
            html.P(ex["text"], className="muted"),
        ], {}
    return [
        html.Div(f"РАЗРЫВ ЛИКВИДНОСТИ · {title.upper()}", className="eyebrow"),
        html.H2(f"{ex['when']}: не хватит {ex['shortfall']}"),
        html.H3("Почему", className="explain-subtitle"),
        html.Ul([html.Li(item) for item in ex["why"]]),
        html.H3("Что сделать", className="explain-subtitle"),
        html.Ol([html.Li(action["text"]) for action in ex["actions"]]),
        html.Small(
            "Текст собран по шаблону из чисел расчёта, без языковой модели. "
            "Рекомендации — варианты для обсуждения, а не гарантия результата."
        ),
    ], {}


@app.callback(
    Output("event-message", "children"),
    Output("revision", "data", allow_duplicate=True),
    Input("event-button", "n_clicks"),
    Input("event-upload", "contents"),
    Input("new-event-button", "n_clicks"),
    State("base", "data"),
    State("account", "value"),
    State("asof", "value"),
    State("event-session", "data"),
    State("revision", "data"),
    State("event-label", "value"),
    State("event-amount", "value"),
    State("event-direction", "value"),
    State("event-category", "value"),
    State("event-due", "value"),
    State("event-date", "value"),
    State("event-known", "value"),
    State("event-replacement", "value"),
    prevent_initial_call=True,
)
def create_event(
    clicks,
    contents,
    new_clicks,
    base,
    account,
    asof,
    session,
    revision,
    label,
    amount,
    direction,
    category,
    due,
    expected,
    known,
    replacement,
):
    if ctx.triggered_id == "new-event-button":
        return "", no_update
    try:
        if ctx.triggered_id == "event-upload" and contents:
            payload = base64.b64decode(contents.split(",", 1)[1])
            result = api(
                "POST",
                "/api/v1/imports/events",
                params={"base_run_id": base["run_id"]}
                if matching_base(base, account, asof)
                else {},
                files={"file": ("events.csv", payload, "text/csv")},
            )
            return html.Div(
                f"Импортировано событий: {len(result)}", className="notice-success"
            ), revision + 1
        body = build_event_request(
            account_id=account,
            as_of=asof,
            base=base,
            session_id=session,
            label=label,
            amount=amount,
            direction=direction,
            category=category,
            due=due,
            expected=expected,
            known=known,
            replacement=replacement,
        )
        event = api("POST", "/api/v1/events", json=body)
        return html.Div(save_message(event, asof), className="notice-success"), revision + 1
    except (ValueError, TypeError) as exc:
        return html.Div(str(exc), className="notice-error"), no_update


@app.callback(
    Output("payment-message", "children"),
    Output("revision", "data", allow_duplicate=True),
    Input("payment-button", "n_clicks"),
    Input("reschedule-button", "n_clicks"),
    State("payment-event", "value"),
    State("payment-operation", "value"),
    State("payment-amount", "value"),
    State("payment-known", "value"),
    State("reschedule-date", "value"),
    State("revision", "data"),
    prevent_initial_call=True,
)
def payment(_, __, event_id, operation, amount, known, expected, revision):
    if not event_id:
        return "Выберите подтверждённое событие", no_update
    try:
        if ctx.triggered_id == "payment-button":
            api(
                "POST",
                f"/api/v1/events/{event_id}/payments",
                json={
                    "operation_id": operation,
                    "amount_minor": to_minor(str(amount)),
                    "known_at": known,
                },
            )
            return (
                "Оплата связана. Учитывается на срезах после даты знания и фактической операции.",
                revision + 1,
            )
        api(
            "PATCH",
            f"/api/v1/events/{event_id}",
            json={"expected_date": expected, "known_at": known},
        )
        return "Новая дата сохранена отдельной версией события.", revision + 1
    except (ValueError, TypeError) as exc:
        return str(exc), no_update


@app.callback(
    Output("import-message", "children"),
    Output("account", "options"),
    Output("account", "value"),
    Output("revision", "data", allow_duplicate=True),
    Input("tx-upload", "contents"),
    State("revision", "data"),
    prevent_initial_call=True,
)
def import_csv(contents, revision):
    if not contents:
        return no_update, no_update, no_update, no_update
    try:
        payload = base64.b64decode(contents.split(",", 1)[1])
        result = api(
            "POST",
            "/api/v1/imports/transactions",
            files={"file": ("statement.csv", payload, "text/csv")},
        )
        accounts = api("GET", "/api/v1/accounts")
        return (
            f"Добавлено: {result['inserted']}. Уже были: {result['duplicates']}.",
            [{"label": f"{a['id']} · {a['name']}", "value": a["id"]} for a in accounts],
            result["accounts"][0],
            revision + 1,
        )
    except ValueError as exc:
        return str(exc), no_update, no_update, no_update


@app.callback(
    Output("tx-page", "data"),
    Input("tx-prev", "n_clicks"),
    Input("tx-next", "n_clicks"),
    Input("account", "value"),
    State("tx-page", "data"),
)
def page(_, __, account, current):
    return (
        max(1, current - 1)
        if ctx.triggered_id == "tx-prev"
        else current + 1
        if ctx.triggered_id == "tx-next"
        else 1
    )


@app.callback(
    Output("tx-grid", "rowData"),
    Output("tx-page-label", "children"),
    Output("quality", "children"),
    Input("account", "value"),
    Input("tx-page", "data"),
    Input("revision", "data"),
)
def operations(account, page_number, revision):
    if not account:
        return [], "", ""
    try:
        result = api(
            "GET",
            f"/api/v1/accounts/{account}/transactions",
            params={"page": page_number, "page_size": 50},
        )
        quality = api("GET", f"/api/v1/accounts/{account}/quality")
        rows = [
            {
                **t,
                "amount": t["amount_minor"] / 100,
                "direction_label": "Поступление" if t["direction"] == "in" else "Списание",
                "category_label": CATEGORY_LABELS[t["category"]],
            }
            for t in result["items"]
        ]
        return (
            rows,
            f"Страница {page_number} · всего операций {result['total']}",
            html.Div(
                [
                    html.P(
                        f"Покрытие: {quality['first_date']} — {quality['last_date']} · календарных дней: {quality['calendar_days']}"
                    ),
                    html.P(
                        f"Несверки остатков: {len(quality['inconsistent_transitions'])} · подозрительные группы дублей: {len(quality['suspicious_duplicates'])} · пустые назначения: {quality['empty_purpose']}"
                    ),
                    html.Small(
                        f"Дней без операций и без подтверждения полноты: {quality['unconfirmed_no_operation_days']}. Подозрительные операции сохранены; EOD не суммируется."
                    ),
                ],
                className="quality-note",
            ),
        )
    except ValueError as exc:
        return [], "", str(exc)


@app.callback(
    Output("category-message", "children"),
    Output("revision", "data", allow_duplicate=True),
    Input("category-button", "n_clicks"),
    State("account", "value"),
    State("edit-operation", "value"),
    State("edit-category", "value"),
    State("asof", "value"),
    State("revision", "data"),
    prevent_initial_call=True,
)
def edit_category(_, account, operation, category, known_at, revision):
    try:
        api(
            "PATCH",
            f"/api/v1/transactions/{account}/{operation}/category",
            json={"category": category, "known_at": known_at},
        )
        return "Категория сохранена; версия данных обновлена.", revision + 1
    except ValueError as exc:
        return str(exc), no_update


@app.callback(Output("saved-run", "options"), Input("base", "data"))
def saved_runs(base):
    if not base:
        return []
    rows = api("GET", f"/api/v1/accounts/{base['account_id']}/forecasts")
    return [
        {
            "label": f"{r['as_of']} · {r['model_version']} · данные v{r['data_version']} · {r['run_id'][:8]}",
            "value": r["run_id"],
        }
        for r in rows
    ]


@app.callback(
    Output("plan-graph", "figure"),
    Output("plan-grid", "rowData"),
    Output("plan-info", "children"),
    Input("saved-run", "value"),
    Input("base", "data"),
)
def plan_fact(run_id, base):
    if not run_id and not base:
        return go.Figure(), [], "Сначала сохраните прогноз"
    try:
        result = api("GET", "/api/v1/plan-fact", params={"run_id": run_id or base["run_id"]})
        rows = result["rows"]
        fig = go.Figure()
        for key, name, color in [
            ("planned_minor", "Сохранённый план", "#16877b"),
            ("actual_minor", "Наблюдаемый факт", "#d78b3e"),
        ]:
            fig.add_trace(
                go.Scatter(
                    x=[r["date"] for r in rows],
                    y=[r[key] / 100 if r[key] is not None else None for r in rows],
                    name=name,
                    line={"color": color},
                )
            )
        fig.update_layout(
            template="plotly_white", margin={"t": 20, "b": 20}, legend={"orientation": "h"}
        )
        data = [
            {
                "date": r["date"],
                "planned": r["planned_minor"] / 100,
                "actual": r["actual_minor"] / 100 if r["actual_minor"] is not None else None,
                "delta": r["delta_minor"] / 100 if r["delta_minor"] is not None else None,
                "coverage": r["coverage"],
            }
            for r in rows
        ]
        return (
            fig,
            data,
            f"План {result['run_id']} неизменяем. Факт: версия данных {result['data_version']}. Неоднозначных сопоставлений для проверки: {len(result['matching_review'])}.",
        )
    except ValueError as exc:
        return go.Figure(), [], str(exc)


@app.callback(Output("research", "children"), Input("view", "value"))
def research(view):
    if view != "research":
        return no_update
    try:
        result = api("GET", "/api/v1/experiments")
        metrics = result["metrics"]
        if not metrics:
            return html.P("Оценка ещё не выполнена. Запустите cashflow evaluate.")
        table = []
        for split, methods in metrics["summary"].items():
            for method, m in methods.items():
                table.append(
                    html.Tr(
                        [
                            html.Td(split),
                            html.Td(method),
                            html.Td(m["windows"]),
                            html.Td(rub(int(m["trajectory_mae_rub"] * 100))),
                            html.Td(rub(int(m["day30_mae_rub"] * 100))),
                            html.Td(rub(int(m["minimum_mae_rub"] * 100))),
                        ]
                    )
                )
        return [
            html.Table(
                [
                    html.Thead(
                        html.Tr(
                            [
                                html.Th(t)
                                for t in [
                                    "Часть",
                                    "Метод",
                                    "Окна",
                                    "MAE траектории",
                                    "MAE дня 30",
                                    "MAE минимума",
                                ]
                            ]
                        )
                    ),
                    html.Tbody(table),
                ],
                className="research-table",
            ),
            html.Ul([html.Li(t) for t in metrics["limitations"]]),
            html.Div(
                [html.P(f"{r['id']} · {r['status']} · {r['note']}") for r in result["registry"]],
                className="experiment-registry",
            ),
        ]
    except ValueError as exc:
        return str(exc)


if __name__ == "__main__":
    app.run(
        host=os.getenv("DASH_HOST", "127.0.0.1"),
        port=int(os.getenv("DASH_PORT", "8050")),
        debug=False,
    )
