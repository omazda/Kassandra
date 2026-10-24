# Kassandra — клиент на Flutter

Один код для веба, Android, iOS, Windows и macOS. Интерфейс на русском. Бэкенд, ML и данные этот пакет не меняет.

Версия Flutter, на которой собран проект: **3.47.8** (Dart 3.13.7). Файл `.fvmrc` фиксирует ту же версию. На сервере репозитория SDK нет: поставьте stable 3.47.8 локально или через [FVM](https://fvm.app).

```bash
cd apps/kassandra_app
flutter pub get
```

## Запуск на фикстуре

По умолчанию `KASSANDRA_DATA=fixtures`. Приложение читает `assets/fixtures/c003_2026-04-26.json` (срез ООО «Агро-191») и не ходит в сеть.

```bash
flutter run -d chrome
# телефонный размер в браузере: сузьте окно ниже 600 px
```

Демо-ссылка: `/forecast?account=C003&as_of=2026-04-26`. Веб использует path URL (`usePathUrlStrategy`), без `#`. Nginx стенда уже отдаёт `index.html` на любой путь.

На этой фикстуре:

- остаток 2 099 029 ₽, разрыв 21 мая, нехватка в баннере «393 тыс. ₽» (округление API вверх);
- вероятность 77% с пометкой «экспериментальная оценка»;
- сценарий «выручка −20%» и «внеплановый расход 300 000 ₽ 8 мая» даёт разрыв 15 мая и нехватку 1,34 млн ₽;
- превью события «+300 000 ₽ 20 мая» сдвигает разрыв на 25 мая и нехватку 92 тыс. ₽.

Другой даты в фикстуре нет: выбор 26.04.2026 — единственный срез.

## Запуск против локального API

Из корня репозитория, как в основном README:

```bash
uv run cashflow bootstrap --dataset dataset/
API_PORT=8100 uv run python scripts/dev.py
```

Клиент:

```bash
cd apps/kassandra_app
flutter run -d chrome \
  --dart-define=KASSANDRA_DATA=http \
  --dart-define=API_BASE_URL=http://127.0.0.1:8100
```

В режиме `CASHFLOW_AUTH_MODE=demo` демо-счета открываются без токена. Иначе на экране «Вход» вставьте токен. В сборку токен не зашивается. На вебе он живёт только в памяти вкладки. На Android, iOS, Windows и macOS — в `flutter_secure_storage`, а если связка ключей недоступна, до конца сессии в памяти.

Веб-сборка ходит на тот же origin, что и API: CORS у API нет.

```bash
flutter build web --release \
  --dart-define=KASSANDRA_DATA=http \
  --dart-define=API_BASE_URL=/
```

Пример nginx — `deploy/nginx.conf` в корне репозитория.

## Сборки

| Платформа | Команда |
|---|---|
| Веб | `flutter build web --release --dart-define=API_BASE_URL=/ --dart-define=KASSANDRA_DATA=http` |
| Android | `flutter build apk --release` (для демо достаточно debug-подписи) |
| Windows | `flutter build windows --release` — только на Windows |
| macOS | `flutter build macos --release` — только на Mac |
| iOS | `flutter build ios --release --no-codesign` — только на Mac |

Каталоги `android/`, `ios/`, `web/`, `windows/`, `macos/`, `linux/` создаёт Flutter. `flutter analyze` и тесты не требуют Mac.

Иконка: `flutter_launcher_icons` в `pubspec.yaml`, исходник `assets/logo/app-icon-1024.png`, фон adaptive icon `#0F2B2E`.

```bash
dart run flutter_launcher_icons
```

## Проверки

```bash
flutter analyze
flutter test
```

На машине сборки (Linux, Flutter 3.47.8): `flutter analyze` без замечаний, `flutter test` — 51 тест, включая путь фикстуры «прогноз → разрыв → два сценария → 15 мая → превью события 25 мая» и golden-кадры календаря и операций. Веб-сборка:

```bash
flutter build web --release \
  --dart-define=KASSANDRA_DATA=http \
  --dart-define=API_BASE_URL=/
```

Сборки под Android, iOS, Windows и macOS делаются на соответствующей платформе; стенд использует веб-сборку.

`integration_test/demo_flow_test.dart` повторяет путь фикстуры на устройстве или в браузере (`flutter test integration_test`). Тот же путь без устройства гоняет `test/demo_flow_test.dart`.

## Принятые решения

- Модели API написаны вручную (`fromJson`), без `freezed` и `build_runner`. Имена полей совпадают с OpenAPI. Так сборка не зависит от кодогенерации.
- Деньги считаются в `int` (копейки). В рубли переводятся только при выводе. Точная сумма — HALF_UP до рубля (`−392 029 ₽`). Короткая нехватка до тысяч округляется вверх, как `fmt_money(..., up=True)` в API (`393 тыс. ₽`). Миллионы на карточке сценария — HALF_UP (`1,34 млн ₽`), это число из `liquidity_need_minor`.
- Тексты `why`, `actions`, `limitation`, `assumptions` показываются как пришли. Чипы («ЕНП», «392 тыс.») не подставляются, если их нет в ответе.
- Отрасль компании не выводится: в API её нет. В карточке компании — имя счёта и число дней истории из `confidence.history_days`.
- `GET /daily` режется по `date <= as_of` и на графике истории, и в ряде «90 дней / год». Будущее архива на график не попадает.
- В фикстуре `quality.last_date` — 26.04.2026, дата демо-среза, а не `latest_operation_date` (28.09.2026). Иначе прогноз открылся бы не на том дне.
- Сохранение события и прочие сценарии в режиме фикстур считаются на клиенте тем же линейным правилом, что и превью. Комбинация «выручка −20% + 300 000 ₽ 8 мая» возвращает записанный ответ API. После сохранения уходит `POST /events`, затем `POST /forecasts/{run_id}/refresh`. Тост берётся из `changes`: «Разрыв закрыт», «Разрыв сдвинулся на N дней раньше», «Потребность выросла на …».
- Новое событие всегда уходит со `status: confirmed`. Уверенность — поле `certainty`: `promised`, `agreed`, `documented`. Строка «Опишите словами» вызывает `POST /events/parse`.
- Загрузка своей выписки и 1С в демо-режиме отвечает 403. Клиент показывает текст `detail`, а не сырой JSON.
- Подписи источников и категорий берутся из `GET /dictionaries`. Неизвестный код — «Другой источник».
- Golden-тесты — кадры Flutter на 1440 и 390, светлая тема: прогноз, разрыв, новое событие, «Что если», календарь платежей и операции. Календарь сверяется с `demo_sources_calendar` (10 платежей, +830 000 ₽, −1 942 820,27 ₽, 4 обязательных, разрыв 18 мая).
- Календарь читает `GET /forecasts/{run_id}/calendar`. Правка, удаление и «не учитывать» уходят с `known_at` = дата расчёта сеанса, затем `POST /forecasts/{run_id}/refresh`. Смена категории операции — `PATCH` и новый `POST /forecasts` на ту же дату, не `refresh`. Список операций всегда с `date_to = as_of`.
- Поле «Что это» у внепланового расхода остаётся только в интерфейсе.
- Сводка источников — `GET /accounts/{id}/sources`. Отчёт импорта показывает «Обновлено N, без изменений M» и не печатает ИНН. Экран сверки (`reconcile?dry_run=true`) даёт выбрать оплату: `POST /events/{id}/payments`.
- `debugPrint` только в debug-сборке и без тел ответов.

## Экраны

P0: прогноз, детали разрыва, «что если», новое событие, выбор счёта и даты. Адреса: `/forecast`, `/gap`, `/scenarios`, `/events/new`.

P1: `/calendar`, `/operations`, `/plan-fact`, `/sources`.

P2: выбор модели (`ensemble` по умолчанию) и локальный список id сохранённых сценариев на экране «Ещё». Push-уведомлений нет.
