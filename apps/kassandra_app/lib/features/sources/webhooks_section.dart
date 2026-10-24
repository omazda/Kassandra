import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/errors.dart';
import '../../api/webhooks.dart';
import '../../app/config.dart';
import '../../app/providers.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';
import 'sources_page.dart' show sourcesSummaryProvider;

/// Webhook kinds the server accepts; labels come from the server when it answers.
const webhookKindLabels = {
  'statement': 'Банковская выписка: новые операции',
  '1c': 'Выписка 1С «Клиент-банк»',
  'bank': 'Банк: кредиты, зарплатный проект, платёжки',
  'marketplace': 'Маркетплейс: выплаты',
  'ofd': 'Онлайн-касса (ОФД): выручка по дням',
  'invoices': 'Счета на оплату',
  'contract': 'Договор с графиком платежей',
};

final webhookKindsProvider = FutureProvider<List<WebhookKind>>(
  (ref) => ref.watch(apiProvider).webhookKinds(),
);

final webhooksProvider = FutureProvider<List<WebhookInfo>>((ref) {
  final account = ref.watch(sessionProvider.select((s) => s.accountId));
  if (account == null) return const [];
  return ref.watch(apiProvider).webhooks(account);
});

final webhookDeliveriesProvider = FutureProvider<List<WebhookDelivery>>((ref) {
  final account = ref.watch(sessionProvider.select((s) => s.accountId));
  if (account == null) return const [];
  return ref.watch(apiProvider).webhookDeliveries(account);
});

/// The address an outside system posts to: the API origin plus the hook path.
String webhookUrl(String path) {
  final base = kApiBaseUrl.startsWith('http')
      ? kApiBaseUrl.replaceAll(RegExp(r'/+$'), '')
      : Uri.base.origin;
  return '$base$path';
}

String _time(DateTime? at) {
  if (at == null) return '—';
  final local = at.toLocal();
  final hh = local.hour.toString().padLeft(2, '0');
  final mm = local.minute.toString().padLeft(2, '0');
  return '${formatDateDot(local)} $hh:$mm';
}

Future<void> _copy(BuildContext context, String text, String what) async {
  await Clipboard.setData(ClipboardData(text: text));
  if (context.mounted) {
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text('$what скопирован')));
  }
}

/// Creates a webhook of [kind] (or lets the user choose) and shows its address and secret once.
Future<void> showCreateWebhook(
  BuildContext context,
  WidgetRef ref, {
  String? kind,
}) async {
  final account = ref.read(sessionProvider).accountId;
  if (account == null) return;
  final chosen = await showDialog<(String, String?)>(
    context: context,
    builder: (_) => _CreateDialog(initial: kind),
  );
  if (chosen == null || !context.mounted) return;
  try {
    final hook = await ref
        .read(apiProvider)
        .createWebhook(account, chosen.$1, chosen.$2);
    ref.invalidate(webhooksProvider);
    if (!context.mounted) return;
    await showDialog<void>(
      context: context,
      builder: (_) => _SecretDialog(hook: hook, secret: hook.secret ?? ''),
    );
  } on ApiException catch (error) {
    ref.read(noticeProvider.notifier).state = error.message;
  }
}

class _CreateDialog extends StatefulWidget {
  const _CreateDialog({this.initial});
  final String? initial;

  @override
  State<_CreateDialog> createState() => _CreateDialogState();
}

class _CreateDialogState extends State<_CreateDialog> {
  late String _kind = widget.initial ?? 'bank';
  final _label = TextEditingController();

  @override
  void dispose() {
    _label.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return AlertDialog(
      title: const Text('Подключить webhook'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Внешняя система будет сама отправлять данные на адрес Kassandra, '
                'а прогноз — обновляться. Выберите, что она присылает:',
                style: golos(14, FontWeight.w400, colors.textMuted),
              ),
              const SizedBox(height: 8),
              RadioGroup<String>(
                groupValue: _kind,
                onChanged: (value) => setState(() => _kind = value ?? _kind),
                child: Column(
                  children: [
                    for (final entry in webhookKindLabels.entries)
                      RadioListTile<String>(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        value: entry.key,
                        title: Text(entry.value),
                      ),
                  ],
                ),
              ),
              TextField(
                controller: _label,
                decoration: const InputDecoration(
                  labelText: 'Название (необязательно)',
                  hintText: 'Например: Сбер Бизнес API',
                ),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Отмена'),
        ),
        FilledButton(
          key: const Key('webhook-create'),
          onPressed: () => Navigator.pop(context, (
            _kind,
            _label.text.trim().isEmpty ? null : _label.text.trim(),
          )),
          child: const Text('Создать'),
        ),
      ],
    );
  }
}

class _SecretDialog extends ConsumerWidget {
  const _SecretDialog({required this.hook, required this.secret});
  final WebhookInfo hook;
  final String secret;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final url = webhookUrl(hook.path);
    final kinds = ref.watch(webhookKindsProvider).valueOrNull ?? const [];
    final kind = kinds.where((k) => k.kind == hook.kind).firstOrNull;
    final file = hook.kind == '1c' ? 'statement.txt' : 'payload.json';
    final type = kind?.contentType ?? 'application/json';
    final curl =
        "SECRET='$secret'\n"
        "SIG=\$(openssl dgst -sha256 -hmac \"\$SECRET\" $file | sed 's/^.* //')\n"
        "curl -X POST '$url' \\\n"
        "  -H 'Content-Type: $type' \\\n"
        "  -H \"X-Kassandra-Signature: sha256=\$SIG\" \\\n"
        "  -H \"X-Delivery-Id: \$(date +%s)\" \\\n"
        "  --data-binary @$file";
    final python =
        'import hashlib, hmac, httpx\n'
        "body = open('$file', 'rb').read()\n"
        "sig = hmac.new(b'$secret', body, hashlib.sha256).hexdigest()\n"
        "httpx.post('$url', content=body, headers={\n"
        "    'Content-Type': '$type', 'X-Kassandra-Signature': 'sha256=' + sig})";
    Widget block(String title, String text) => Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  title,
                  style: golos(13, FontWeight.w700, colors.text),
                ),
              ),
              IconButton(
                tooltip: 'Скопировать',
                icon: const Icon(Icons.copy, size: 18),
                onPressed: () => _copy(context, text, title),
              ),
            ],
          ),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: colors.surfaceMuted,
              borderRadius: BorderRadius.circular(10),
            ),
            child: SelectableText(text, style: mono(12, colors.text)),
          ),
        ],
      ),
    );
    return AlertDialog(
      title: Text('Webhook готов: ${hook.label}'),
      content: SizedBox(
        width: 640,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: colors.warningContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  'Ключ показывается один раз — сохраните его в настройках системы-отправителя. '
                  'Потерян — выпустите новый кнопкой «Новый ключ».',
                  style: golos(13, FontWeight.w600, colors.onWarningContainer),
                ),
              ),
              block('Адрес', url),
              block('Ключ', secret),
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  'Каждый запрос подписывается: заголовок X-Kassandra-Signature = '
                  'sha256=HMAC-SHA256(ключ, тело запроса); X-Delivery-Id защищает от повторной '
                  'доставки. Вместо подписи можно передать заголовок '
                  '«Authorization: Bearer <ключ>».',
                  style: golos(13, FontWeight.w400, colors.textMuted),
                ),
              ),
              block('curl', curl),
              block('Python', python),
              if (kind != null) block('Пример тела ($file)', kind.example),
            ],
          ),
        ),
      ),
      actions: [
        FilledButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Готово'),
        ),
      ],
    );
  }
}

/// «Автоматический сбор»: the account's webhooks, their deliveries and actions. Polls the
/// journal while open, so a delivery from outside shows up and refreshes the forecast.
class WebhooksSection extends ConsumerStatefulWidget {
  const WebhooksSection({super.key});

  @override
  ConsumerState<WebhooksSection> createState() => _WebhooksSectionState();
}

class _WebhooksSectionState extends ConsumerState<WebhooksSection> {
  Timer? _poll;
  int? _lastSeen;
  String? _busy;

  @override
  void initState() {
    super.initState();
    _poll = Timer.periodic(const Duration(seconds: 8), (_) {
      if (!mounted) return;
      ref.invalidate(webhookDeliveriesProvider);
      ref.invalidate(webhooksProvider);
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  void _watchDeliveries(List<WebhookDelivery> list) {
    final newest = list.isEmpty ? 0 : list.first.id;
    final seen = _lastSeen;
    _lastSeen = newest;
    if (seen == null || newest <= seen) return;
    final fresh = list.where((d) => d.id > seen && d.status == 'ok').toList();
    if (fresh.isEmpty) return;
    // New data arrived by itself: recount the forecast and say what came.
    ref.read(sessionProvider.notifier).refresh();
    ref.invalidate(sourcesSummaryProvider);
    ref.read(noticeProvider.notifier).state =
        'Пришли данные по webhook: ${fresh.first.summary}';
  }

  Future<void> _run(String key, Future<String?> Function() action) async {
    setState(() => _busy = key);
    try {
      final message = await action();
      if (message != null) ref.read(noticeProvider.notifier).state = message;
    } on ApiException catch (error) {
      ref.read(noticeProvider.notifier).state = error.message;
    } finally {
      ref.invalidate(webhooksProvider);
      ref.invalidate(webhookDeliveriesProvider);
      if (mounted) setState(() => _busy = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    ref.listen(webhookDeliveriesProvider, (_, next) {
      final list = next.valueOrNull;
      if (list != null) _watchDeliveries(list);
    });
    final hooks = ref.watch(webhooksProvider).valueOrNull ?? const [];
    // Only real deliveries: format checks are not journaled, old test rows are hidden.
    final deliveries = [
      for (final d
          in ref.watch(webhookDeliveriesProvider).valueOrNull ?? const [])
        if (!d.test) d,
    ];
    final phone = isPhone(context);
    return SurfaceCard(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 12,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            alignment: WrapAlignment.spaceBetween,
            children: [
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.webhook, color: colors.primary),
                  const SizedBox(width: 8),
                  Text(
                    'Автоматический сбор — webhook',
                    style: golos(18, FontWeight.w600, colors.text),
                  ),
                ],
              ),
              FilledButton.icon(
                key: const Key('webhook-new'),
                onPressed: kUseFixtures
                    ? null
                    : () => showCreateWebhook(context, ref),
                icon: const Icon(Icons.add_link, size: 18),
                label: const Text('Подключить webhook'),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'Банк, 1С, маркетплейс или касса сами отправляют данные на адрес Kassandra — '
            'без выгрузок вручную. Каждая доставка подписана ключом, повтор с тем же '
            'X-Delivery-Id не применяется дважды, прогноз пересчитывается сам.',
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
          const SizedBox(height: 12),
          if (hooks.isEmpty)
            Text(
              'Пока нет ни одного webhook. Подключите первый — например, банк или онлайн-кассу.',
              style: golos(14, FontWeight.w500, colors.text),
            ),
          for (final hook in hooks)
            _HookTile(
              hook: hook,
              busy: _busy,
              phone: phone,
              onCheck: () => showDialog<void>(
                context: context,
                builder: (_) => _CheckDialog(hook: hook),
              ),
              onRotate: () => _run('rotate-${hook.id}', () async {
                final secret = await ref
                    .read(apiProvider)
                    .rotateWebhook(hook.id);
                if (mounted) {
                  await showDialog<void>(
                    context: this.context,
                    builder: (_) => _SecretDialog(hook: hook, secret: secret),
                  );
                }
                return null;
              }),
              onDelete: () async {
                final ok = await showDialog<bool>(
                  context: context,
                  builder: (context) => AlertDialog(
                    title: Text('Отключить «${hook.label}»?'),
                    content: const Text(
                      'Адрес перестанет принимать данные, журнал доставок удалится. '
                      'Уже полученные данные останутся в прогнозе.',
                    ),
                    actions: [
                      TextButton(
                        onPressed: () => Navigator.pop(context, false),
                        child: const Text('Отмена'),
                      ),
                      FilledButton(
                        onPressed: () => Navigator.pop(context, true),
                        child: const Text('Отключить'),
                      ),
                    ],
                  ),
                );
                if (ok != true) return;
                await _run('delete-${hook.id}', () async {
                  await ref.read(apiProvider).deleteWebhook(hook.id);
                  return 'Webhook отключён';
                });
              },
            ),
          if (deliveries.isNotEmpty) ...[
            const SizedBox(height: 16),
            Text(
              'Журнал доставок',
              style: golos(15, FontWeight.w700, colors.text),
            ),
            const SizedBox(height: 6),
            for (final delivery in deliveries.take(12))
              _DeliveryRow(delivery: delivery, phone: phone),
          ],
        ],
      ),
    );
  }
}

class _HookTile extends StatelessWidget {
  const _HookTile({
    required this.hook,
    required this.busy,
    required this.phone,
    required this.onCheck,
    required this.onRotate,
    required this.onDelete,
  });

  final WebhookInfo hook;
  final String? busy;
  final bool phone;
  final VoidCallback onCheck;
  final VoidCallback onRotate;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final url = webhookUrl(hook.path);
    final last = hook.lastAt == null
        ? 'доставок ещё не было'
        : 'последняя ${_time(hook.lastAt)} — ${hook.lastStatus == 'error' ? hook.lastError ?? 'ошибка' : hook.lastSummary}';
    final working = busy != null && busy!.endsWith(hook.id);
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        border: Border.all(color: colors.divider),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(hook.label, style: golos(15, FontWeight.w700, colors.text)),
              _Status(
                text: hook.active ? 'принимает данные' : 'отключён',
                ok: hook.active,
              ),
              Text(
                '${hook.deliveries} доставок · $last',
                style: golos(12, FontWeight.w400, colors.textMuted),
              ),
            ],
          ),
          if (hook.label != hook.kindLabel)
            Text(
              hook.kindLabel,
              style: golos(12, FontWeight.w500, colors.textMuted),
            ),
          const SizedBox(height: 6),
          Row(
            children: [
              Expanded(
                child: SelectableText(
                  url,
                  maxLines: 1,
                  style: mono(12, colors.primary),
                ),
              ),
              IconButton(
                tooltip: 'Скопировать адрес',
                icon: const Icon(Icons.copy, size: 18),
                onPressed: () => _copy(context, url, 'Адрес'),
              ),
            ],
          ),
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: [
              OutlinedButton(
                onPressed: working ? null : onCheck,
                child: const Text('Проверить'),
              ),
              TextButton(
                onPressed: working ? null : onRotate,
                child: const Text('Новый ключ'),
              ),
              TextButton(
                onPressed: working ? null : onDelete,
                child: Text(
                  'Отключить',
                  style: TextStyle(color: colors.danger),
                ),
              ),
              if (working)
                const Padding(
                  padding: EdgeInsets.all(8),
                  child: SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Status extends StatelessWidget {
  const _Status({required this.text, required this.ok});
  final String text;
  final bool ok;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: ok ? colors.positiveContainer : colors.surfaceMuted,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        text,
        style: golos(
          12,
          FontWeight.w600,
          ok ? colors.onPositiveContainer : colors.textMuted,
        ),
      ),
    );
  }
}

class _DeliveryRow extends StatelessWidget {
  const _DeliveryRow({required this.delivery, required this.phone});
  final WebhookDelivery delivery;
  final bool phone;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final (label, color) = switch (delivery.status) {
      'ok' => ('принято', colors.positive),
      _ => ('ошибка', colors.danger),
    };
    final kind = webhookKindLabels[delivery.kind] ?? delivery.kind;
    final text = delivery.status == 'error'
        ? delivery.error ?? ''
        : delivery.summary;
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 8),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: colors.divider)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: phone ? 92 : 130,
            child: Text(
              _time(delivery.receivedAt),
              style: mono(12, colors.textMuted),
            ),
          ),
          SizedBox(
            width: 86,
            child: Text(label, style: golos(13, FontWeight.w700, color)),
          ),
          Expanded(
            child: Text(
              '${delivery.test ? 'тест · ' : ''}$kind — $text',
              style: golos(13, FontWeight.w400, colors.text),
            ),
          ),
        ],
      ),
    );
  }
}

/// «Проверить»: the webhook's state from real deliveries, and a format check of a body the
/// user pastes. Nothing is invented and nothing is written.
class _CheckDialog extends ConsumerStatefulWidget {
  const _CheckDialog({required this.hook});
  final WebhookInfo hook;

  @override
  ConsumerState<_CheckDialog> createState() => _CheckDialogState();
}

class _CheckDialogState extends ConsumerState<_CheckDialog> {
  final _body = TextEditingController();
  WebhookCheck? _check;
  String? _checkError;
  String? _format;
  bool _formatOk = false;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    ref
        .read(apiProvider)
        .checkWebhook(widget.hook.id)
        .then((check) {
          if (mounted) setState(() => _check = check);
        })
        .catchError((Object error) {
          if (mounted) {
            setState(
              () => _checkError = error is ApiException
                  ? error.message
                  : 'Не удалось проверить',
            );
          }
        });
  }

  @override
  void dispose() {
    _body.dispose();
    super.dispose();
  }

  Future<void> _validate() async {
    setState(() {
      _busy = true;
      _format = null;
    });
    final text = _body.text;
    final type =
        widget.hook.kind == '1c' ||
            !text.trimLeft().startsWith(RegExp(r'[\[{]'))
        ? 'text/plain'
        : 'application/json';
    try {
      final summary = await ref
          .read(apiProvider)
          .validateWebhook(widget.hook.id, text, type);
      if (mounted) {
        setState(() {
          _format = summary;
          _formatOk = true;
        });
      }
    } on ApiException catch (error) {
      if (mounted) {
        setState(() {
          _format = error.message;
          _formatOk = false;
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final check = _check;
    final (icon, color) = switch (check?.status) {
      'working' => (Icons.check_circle_outline, colors.positive),
      'failing' => (Icons.error_outline, colors.danger),
      'disabled' => (Icons.block, colors.textMuted),
      _ => (Icons.hourglass_empty, colors.primary),
    };
    return AlertDialog(
      title: Text('Проверка: ${widget.hook.label}'),
      content: SizedBox(
        width: 600,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (check == null && _checkError == null)
                const LinearProgressIndicator()
              else
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(icon, color: color),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        _checkError ?? check!.summary,
                        style: golos(14, FontWeight.w600, colors.text),
                      ),
                    ),
                  ],
                ),
              const SizedBox(height: 16),
              Text(
                'Проверить формат своих данных',
                style: golos(14, FontWeight.w700, colors.text),
              ),
              const SizedBox(height: 4),
              Text(
                'Вставьте тело запроса, которое отправит ваша система. Kassandra разберёт его '
                'и скажет, что было бы добавлено, — без записи в прогноз и журнал.',
                style: golos(13, FontWeight.w400, colors.textMuted),
              ),
              const SizedBox(height: 8),
              TextField(
                key: const Key('webhook-validate-body'),
                controller: _body,
                minLines: 6,
                maxLines: 12,
                style: mono(12, colors.text),
                decoration: const InputDecoration(
                  hintText: '{ ... } или строки CSV',
                ),
              ),
              if (_format != null) ...[
                const SizedBox(height: 8),
                Text(
                  _format!,
                  style: golos(
                    13,
                    FontWeight.w600,
                    _formatOk ? colors.positive : colors.danger,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Закрыть'),
        ),
        FilledButton(
          key: const Key('webhook-validate'),
          onPressed: _busy ? null : _validate,
          child: Text(_busy ? 'Проверяем…' : 'Проверить формат'),
        ),
      ],
    );
  }
}
