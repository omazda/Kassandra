import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:go_router/go_router.dart';

import '../../api/webhooks.dart';
import '../../app/config.dart';
import '../ai/ocr_dialog.dart';
import 'webhooks_section.dart';
import '../../api/errors.dart';
import '../../api/v11.dart';
import '../../app/providers.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';
import '../../api/models.dart';

class SourcesPage extends ConsumerStatefulWidget {
  const SourcesPage({super.key});

  @override
  ConsumerState<SourcesPage> createState() => _SourcesPageState();
}

class _SourcesPageState extends ConsumerState<SourcesPage> {
  List<String>? _report;
  String? _error;
  var _busy = false;
  List<_ReviewItem> _reviews = const [];

  @override
  Widget build(BuildContext context) {
    final phone = isPhone(context);
    final colors = KassandraColors.of(context);
    return ListView(
      padding: phone
          ? const EdgeInsets.fromLTRB(20, 14, 20, 120)
          : const EdgeInsets.fromLTRB(40, 32, 40, 48),
      children: [
        Text(
          'Источники данных',
          style: phone
              ? golos(22, FontWeight.w600, colors.text)
              : unbounded(30, FontWeight.w600, colors.text),
        ),
        const SizedBox(height: 8),
        Text(
          'Откуда Kassandra берёт данные для прогноза: выписка, документы, банк, маркетплейс и касса. '
          'Подключите webhook — и данные будут приходить сами.',
          style: golos(15, FontWeight.w400, colors.textMuted),
        ),
        const SizedBox(height: 16),
        if (_error != null)
          Container(
            width: double.infinity,
            margin: const EdgeInsets.only(bottom: 16),
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: colors.warningContainer,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: colors.warningOutline),
            ),
            child: Text(
              _error!,
              style: golos(15, FontWeight.w600, colors.text),
            ),
          ),
        // What the forecast uses now, then every source as a card, then automatic collection.
        const SurfaceCard(
          padding: EdgeInsets.all(20),
          child: _SourcesSummary(),
        ),
        const SizedBox(height: 20),
        Text(
          'Подключить источник',
          style: golos(18, FontWeight.w600, colors.text),
        ),
        const SizedBox(height: 4),
        Text(
          'Загрузите файл, введите вручную или подключите webhook — тогда данные будут приходить сами.',
          style: golos(13, FontWeight.w400, colors.textMuted),
        ),
        const SizedBox(height: 12),
        LayoutBuilder(
          builder: (context, constraints) {
            final columns = constraints.maxWidth >= 1000
                ? 3
                : constraints.maxWidth >= 640
                ? 2
                : 1;
            final width = (constraints.maxWidth - 12 * (columns - 1)) / columns;
            return Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                for (final card in _cards(context))
                  SizedBox(width: width, child: card),
              ],
            );
          },
        ),
        const SizedBox(height: 20),
        const WebhooksSection(),
        const SizedBox(height: 16),
        if (_report != null) ...[
          const SizedBox(height: 16),
          SurfaceCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Отчёт об импорте',
                  style: golos(16, FontWeight.w600, colors.text),
                ),
                const SizedBox(height: 8),
                for (final line in _report!)
                  Text(line, style: golos(14, FontWeight.w400, colors.text)),
              ],
            ),
          ),
        ],
        if (_reviews.isNotEmpty) ...[
          const SizedBox(height: 16),
          Text(
            'Нужна проверка',
            style: golos(18, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 8),
          for (final item in _reviews) _ReviewCard(item: item, onPay: _pay),
        ],
      ],
    );
  }

  List<Widget> _cards(BuildContext context) {
    final summary = ref.watch(sourcesSummaryProvider).valueOrNull;
    final hooks = ref.watch(webhooksProvider).valueOrNull ?? const [];
    final statement = summary?['statement'];
    final registered = <String, int>{
      for (final item in (summary?['registered_events'] as List? ?? const []))
        if (item is Map) item['source'].toString(): asInt(item['events']),
    };
    String events(List<String> sources) {
      final count = sources.fold<int>(
        0,
        (sum, s) => sum + (registered[s] ?? 0),
      );
      return count == 0 ? 'пока нет событий' : eventsPhrase(count);
    }

    final ops = statement is Map ? asIntOpt(statement['operations']) : null;
    final last = statement is Map ? asDateOpt(statement['last_date']) : null;
    return [
      _SourceCard(
        icon: Icons.account_balance_outlined,
        title: 'Банковская выписка',
        text: 'Операции по счёту — основа прогноза. CSV в формате выписки или выгрузка 1С «Клиент-банк».',
        status: ops == null
            ? 'выписки нет'
            : '${operationsPhrase(ops)}${last == null ? '' : ' · по ${formatDateDot(last)}'}',
        kinds: const ['statement', '1c'],
        hooks: hooks,
        actions: [
          ('Загрузить CSV', () => _upload(events: false), true),
          ('Выписка 1С', _upload1c, false),
        ],
        busy: _busy,
      ),
      _SourceCard(
        icon: Icons.description_outlined,
        title: 'Счета и договоры',
        text: 'Будущие оплаты по документам: сумма и срок известны заранее, прогноз опирается на них.',
        status: events(const ['invoice', 'contract', 'csv']),
        kinds: const ['invoices', 'contract'],
        hooks: hooks,
        actions: [
          ('Фото или PDF — ИИ', () => showOcrDialog(context, ref), true),
          ('Счёт вручную', () => _formInvoice(context), false),
          ('Договор', () => _formContract(context), false),
          ('Счета CSV', () => _uploadNamed('invoices'), false),
        ],
        busy: _busy,
      ),
      _SourceCard(
        icon: Icons.account_balance_wallet_outlined,
        title: 'Банк',
        text: 'Графики кредитов, зарплатный проект и созданные, но не проведённые платёжки.',
        status: events(const ['bank']),
        kinds: const ['bank'],
        hooks: hooks,
        actions: [('Загрузить JSON', () => _uploadNamed('bank'), false)],
        busy: _busy,
      ),
      _SourceCard(
        icon: Icons.storefront_outlined,
        title: 'Маркетплейс',
        text: 'Выплаты по отчётам о реализации приходят по графику — прогноз знает даты.',
        status: events(const ['marketplace']),
        kinds: const ['marketplace'],
        hooks: hooks,
        actions: [('Загрузить JSON', () => _uploadNamed('marketplace'), false)],
        busy: _busy,
      ),
      _SourceCard(
        icon: Icons.point_of_sale_outlined,
        title: 'Онлайн-касса (ОФД)',
        text: 'Выручка по картам и СБП приходит на счёт через рабочий день за вычетом комиссии.',
        status: events(const ['ofd']),
        kinds: const ['ofd'],
        hooks: hooks,
        actions: [('Загрузить JSON', () => _uploadNamed('ofd'), false)],
        busy: _busy,
      ),
      _SourceCard(
        icon: Icons.edit_calendar_outlined,
        title: 'Ручные события',
        text:
            'Обещания клиентов и разовые платежи, о которых знаете только вы.',
        status: events(const ['manual']),
        kinds: const [],
        hooks: hooks,
        actions: [
          (
            'Добавить событие',
            () => context.go(
              '/events/new${sectionQuery(ref.read(sessionProvider))}',
            ),
            true,
          ),
          ('Импорт CSV', () => _upload(events: true), false),
        ],
        busy: _busy,
      ),
      _SourceCard(
        icon: Icons.fact_check_outlined,
        title: 'Сверка с выпиской',
        text: 'Найти оплаты известных счетов в выписке: оплаченное уходит из прогноза.',
        status: 'по запросу',
        kinds: const [],
        hooks: hooks,
        actions: [('Проверить сверку', _reconcile, true)],
        busy: _busy,
      ),
    ];
  }

  Future<void> _upload({required bool events}) async {
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['csv'],
      withData: true,
    );
    if (picked == null || picked.files.isEmpty) return;
    final file = picked.files.single;
    final bytes = file.bytes;
    if (bytes == null) {
      setState(() => _report = const ['Не удалось прочитать файл']);
      return;
    }
    setState(() => _busy = true);
    try {
      final api = ref.read(apiProvider);
      final session = ref.read(sessionProvider);
      final report = events
          ? await api.importEvents(
              bytes,
              file.name,
              baseRunId: ref
                  .read(forecastBundleProvider)
                  .valueOrNull
                  ?.forecast
                  .runId,
            )
          : await api.importTransactions(bytes, file.name);
      ref.read(sessionProvider.notifier).refresh();
      if (!mounted) return;
      setState(() {
        _busy = false;
        _report = report.lines;
      });
      if (session.accountId != null) ref.invalidate(accountsProvider);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = error.message;
        _report = null;
      });
    }
  }

  Future<void> _upload1c() => _uploadNamed('1c');

  Future<void> _uploadNamed(String kind) async {
    final session = ref.read(sessionProvider);
    if (session.accountId == null) return;
    final extensions = kind == '1c'
        ? const ['txt', 'csv']
        : kind == 'invoices'
        ? const ['csv']
        : const ['json'];
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: extensions,
      withData: true,
    );
    if (picked == null ||
        picked.files.isEmpty ||
        picked.files.single.bytes == null) {
      return;
    }
    final file = picked.files.single;
    setState(() => _busy = true);
    try {
      final path = switch (kind) {
        '1c' => '/api/v1/accounts/${session.accountId}/imports/1c',
        'invoices' => '/api/v1/accounts/${session.accountId}/imports/invoices',
        'bank' => '/api/v1/accounts/${session.accountId}/integrations/bank',
        'marketplace' =>
          '/api/v1/accounts/${session.accountId}/integrations/marketplace',
        _ => '/api/v1/accounts/${session.accountId}/integrations/ofd',
      };
      final api = ref.read(apiProvider);
      final RegisterResult result;
      if (kind == 'bank' || kind == 'marketplace' || kind == 'ofd') {
        final decoded = jsonDecode(utf8.decode(file.bytes!));
        if (decoded is! Map) {
          throw ApiFailure('Нужен JSON-объект с полями фида');
        }
        final body = Map<String, dynamic>.from(decoded);
        // Sample feeds omit known_at. Without it the API uses the last
        // statement day, and May payouts are already in the past.
        if (body['known_at'] == null && session.asOf != null) {
          body['known_at'] = isoDate(session.asOf!);
        }
        result = await api.postJson(path, body);
      } else {
        result = await api.importFile(path, file.bytes!, file.name);
      }
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = result.warning;
        _report = [result.summary, ...result.lines];
      });
      ref.read(sessionProvider.notifier).refresh();
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = error.message;
      });
    } on FormatException {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = 'Файл не разбирается как JSON';
      });
    }
  }

  Future<void> _reconcile() async {
    final session = ref.read(sessionProvider);
    if (session.accountId == null) return;
    setState(() => _busy = true);
    try {
      final data = await ref
          .read(apiProvider)
          .reconcile(session.accountId!, dryRun: true);
      final review = data['needs_review'];
      final linked = data['linked'];
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = null;
        _reviews = _ReviewItem.parse(review);
        _report = [
          'Отмечено оплаченными: ${linked is List ? linked.length : 0}, требуют проверки: ${_reviews.length}',
        ];
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = error.message;
      });
    }
  }

  Future<void> _pay(_ReviewItem item, _ReviewCandidate candidate) async {
    final session = ref.read(sessionProvider);
    if (session.asOf == null) return;
    setState(() => _busy = true);
    try {
      await ref
          .read(apiProvider)
          .markPayment(
            item.eventId,
            candidate.operationId,
            candidate.amountMinor,
            session.asOf!,
          );
      ref.read(sessionProvider.notifier).refresh();
      if (!mounted) return;
      setState(() {
        _busy = false;
        _reviews = [
          for (final review in _reviews)
            if (review.eventId != item.eventId) review,
        ];
        _report = ['Оплата отмечена', ...?_report];
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = error.message;
      });
    }
  }

  Future<void> _formContract(BuildContext context) async {
    final id = TextEditingController();
    final title = TextEditingController();
    final sum = TextEditingController();
    final who = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Договор'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: id,
              decoration: const InputDecoration(labelText: 'Номер'),
            ),
            TextField(
              controller: title,
              decoration: const InputDecoration(labelText: 'Название'),
            ),
            TextField(
              controller: sum,
              decoration: const InputDecoration(labelText: 'Сумма, ₽'),
            ),
            TextField(
              controller: who,
              decoration: const InputDecoration(labelText: 'Контрагент'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Создать'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final session = ref.read(sessionProvider);
    final minor = parseRublesToMinor(sum.text);
    if (session.accountId == null ||
        minor == null ||
        minor <= 0 ||
        id.text.trim().isEmpty) {
      setState(() => _error = 'Укажите номер и сумму договора');
      return;
    }
    try {
      final result = await ref.read(apiProvider).createContract(
        session.accountId!,
        {
          'contract_id': id.text.trim(),
          'title': title.text.trim(),
          'direction': 'in',
          'total_minor': minor,
          'signed_date': isoDate(session.asOf ?? DateTime.now()),
          'prepayment_pct': 30,
          'counterparty_name': who.text.trim(),
          if (session.asOf != null) 'known_at': isoDate(session.asOf!),
          'base_run_id': ref
              .read(forecastBundleProvider)
              .valueOrNull
              ?.forecast
              .runId,
        },
      );
      ref.read(sessionProvider.notifier).refresh();
      setState(() => _report = [result.summary, ...result.lines]);
    } on ApiException catch (error) {
      setState(() => _error = error.message);
    }
  }

  Future<void> _formInvoice(BuildContext context) async {
    final number = TextEditingController();
    final sum = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Счёт'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: number,
              decoration: const InputDecoration(labelText: 'Номер'),
            ),
            TextField(
              controller: sum,
              decoration: const InputDecoration(labelText: 'Сумма, ₽'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Создать'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final session = ref.read(sessionProvider);
    final minor = parseRublesToMinor(sum.text);
    if (session.accountId == null ||
        minor == null ||
        number.text.trim().isEmpty) {
      setState(() => _error = 'Укажите номер и сумму счёта');
      return;
    }
    try {
      final result = await ref.read(apiProvider).createInvoice(
        session.accountId!,
        {
          'number': number.text.trim(),
          'direction': 'in',
          'issue_date': isoDate(session.asOf ?? DateTime.now()),
          'amount_minor': minor,
          if (session.asOf != null) 'known_at': isoDate(session.asOf!),
          'base_run_id': ref
              .read(forecastBundleProvider)
              .valueOrNull
              ?.forecast
              .runId,
        },
      );
      ref.read(sessionProvider.notifier).refresh();
      setState(() => _report = [result.summary, ...result.lines]);
    } on ApiException catch (error) {
      setState(() => _error = error.message);
    }
  }
}

String _registeredLine(Map item) {
  final label = (item['label'] ?? item['source'] ?? 'Источник').toString();
  final events = asInt(item['events']);
  final inbound = asInt(item['amount_in_minor']);
  final outbound = asInt(item['amount_out_minor']);
  final parts = <String>[label, eventsPhrase(events)];
  if (inbound > 0) parts.add('поступления ${formatMoneyShort(inbound)}');
  if (outbound > 0) parts.add('списания ${formatMoneyShort(outbound)}');
  return parts.join(' · ');
}

String _syncLine(Map item) {
  const names = {
    'bank': 'Банк',
    'marketplace': 'Маркетплейс',
    'ofd': 'Онлайн-касса',
    'contract': 'Договор',
    'invoice': 'Счета',
    'invoice_import': 'Счета',
    '1c': '1С',
    'accounting': '1С',
  };
  final provider = (item['provider'] ?? '').toString();
  final title = names[provider] ?? provider;
  final raw = item['last_sync']?.toString();
  final parsed = raw == null ? null : DateTime.tryParse(raw);
  final when = parsed == null
      ? ''
      : ' · ${formatDateDot(parsed.toUtc())}, '
            '${parsed.toUtc().hour.toString().padLeft(2, '0')}:'
            '${parsed.toUtc().minute.toString().padLeft(2, '0')}';
  return '$title$when';
}

/// Watches the whole session: an import refreshes it, and the summary follows.
final sourcesSummaryProvider = FutureProvider<Map<String, dynamic>?>((
  ref,
) async {
  final session = ref.watch(sessionProvider);
  if (session.accountId == null) return null;
  return ref.watch(apiProvider).sources(session.accountId!);
});

class _SourcesSummary extends ConsumerWidget {
  const _SourcesSummary();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final data = ref.watch(sourcesSummaryProvider).valueOrNull;
    return Builder(
      builder: (context) {
        if (data == null) return const SizedBox.shrink();
        final statement = data['statement'];
        final label = statement is Map
            ? (statement['label'] ?? 'Выписка').toString()
            : 'Выписка';
        final ops = statement is Map ? asIntOpt(statement['operations']) : null;
        final from = statement is Map
            ? asDateOpt(statement['first_date'])
            : null;
        final to = statement is Map ? asDateOpt(statement['last_date']) : null;
        final period = from != null && to != null
            ? ' · ${formatDateDot(from)} — ${formatDateDot(to)}'
            : '';
        final registered = data['registered_events'];
        final systems = data['system_sources'];
        final syncs = data['syncs'];
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '$label${ops == null ? '' : ' · ${operationsPhrase(ops)}'}$period',
              style: golos(15, FontWeight.w600, colors.text),
            ),
            if (registered is List && registered.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(
                'События по источникам',
                style: golos(13, FontWeight.w600, colors.textMuted),
              ),
              for (final item in registered)
                if (item is Map)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      _registeredLine(item),
                      style: golos(14, FontWeight.w400, colors.text),
                    ),
                  ),
            ],
            if (systems is List && systems.isNotEmpty) ...[
              const SizedBox(height: 12),
              // Every system source carries the same note: say it once in the heading.
              Text(
                'Прогноз строит сам по истории до даты расчёта',
                style: golos(13, FontWeight.w600, colors.textMuted),
              ),
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  [
                    for (final item in systems)
                      if (item is Map) '${item['label'] ?? item['source']}',
                  ].join(' · '),
                  style: golos(14, FontWeight.w400, colors.text),
                ),
              ),
            ],
            if (syncs is List && syncs.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(
                'Синхронизации',
                style: golos(13, FontWeight.w600, colors.textMuted),
              ),
              for (final item in syncs)
                if (item is Map)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      _syncLine(item),
                      style: golos(14, FontWeight.w400, colors.text),
                    ),
                  ),
            ],
          ],
        );
      },
    );
  }
}

class _ReviewCandidate {
  const _ReviewCandidate({
    required this.operationId,
    required this.dateLabel,
    required this.amountMinor,
    required this.score,
    required this.reasons,
  });

  final String operationId;
  final String dateLabel;
  final int amountMinor;
  final int score;
  final String reasons;
}

class _ReviewItem {
  const _ReviewItem({required this.eventId, required this.candidates});

  final String eventId;
  final List<_ReviewCandidate> candidates;

  static List<_ReviewItem> parse(Object? raw) {
    if (raw is! List) return const [];
    final items = <_ReviewItem>[];
    for (final entry in raw) {
      if (entry is! Map) continue;
      final eventId = entry['event_id']?.toString() ?? '';
      if (eventId.isEmpty) continue;
      final candidates = <_ReviewCandidate>[];
      final rawCandidates = entry['candidates'];
      if (rawCandidates is List) {
        for (final candidate in rawCandidates) {
          if (candidate is! Map) continue;
          final operationId = candidate['operation_id']?.toString() ?? '';
          if (operationId.isEmpty) continue;
          final reasons = <String>[];
          final rawReasons = candidate['reasons'];
          if (rawReasons is List) {
            for (final reason in rawReasons) {
              final text = reason.toString();
              if (RegExp(r'^\d{10,12}$').hasMatch(text)) continue;
              reasons.add(text);
            }
          }
          final date = asDateOpt(candidate['operation_date']);
          candidates.add(
            _ReviewCandidate(
              operationId: operationId,
              dateLabel: date == null ? '' : formatDateDot(date),
              amountMinor: asInt(candidate['amount_minor']),
              score: asInt(candidate['score']),
              reasons: reasons.join(', '),
            ),
          );
        }
      }
      if (candidates.isNotEmpty) {
        items.add(_ReviewItem(eventId: eventId, candidates: candidates));
      }
    }
    return items;
  }
}

class _ReviewCard extends StatelessWidget {
  const _ReviewCard({required this.item, required this.onPay});
  final _ReviewItem item;
  final void Function(_ReviewItem item, _ReviewCandidate candidate) onPay;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: SurfaceCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Выберите операцию, которой оплачено событие',
              style: golos(15, FontWeight.w600, colors.text),
            ),
            for (final candidate in item.candidates) ...[
              const SizedBox(height: 10),
              Text(
                [
                  if (candidate.dateLabel.isNotEmpty) candidate.dateLabel,
                  formatMoneyExact(candidate.amountMinor),
                  'балл ${candidate.score}',
                  if (candidate.reasons.isNotEmpty) candidate.reasons,
                ].join(' · '),
                style: golos(14, FontWeight.w400, colors.text),
              ),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton(
                  onPressed: () => onPay(item, candidate),
                  child: const Text('Это оплата'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// One source: what it gives the forecast, its state now, how to load it and its webhook.
class _SourceCard extends ConsumerWidget {
  const _SourceCard({
    required this.icon,
    required this.title,
    required this.text,
    required this.status,
    required this.kinds,
    required this.hooks,
    required this.actions,
    required this.busy,
  });

  final IconData icon;
  final String title;
  final String text;
  final String status;
  final List<String> kinds;
  final List<WebhookInfo> hooks;
  final List<(String, VoidCallback, bool)> actions;
  final bool busy;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final mine = hooks.where((h) => kinds.contains(h.kind)).toList();
    return SurfaceCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: colors.primaryContainer,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Icon(icon, size: 20, color: colors.primary),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  title,
                  style: golos(16, FontWeight.w700, colors.text),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(text, style: golos(13, FontWeight.w400, colors.textMuted)),
          const SizedBox(height: 8),
          Text(status, style: golos(13, FontWeight.w600, colors.text)),
          if (kinds.isNotEmpty) ...[
            const SizedBox(height: 6),
            if (mine.isNotEmpty)
              Row(
                children: [
                  Icon(Icons.webhook, size: 16, color: colors.positive),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      mine.length == 1
                          ? 'Webhook подключён${mine.first.lastAt == null ? '' : ' · доставок ${mine.first.deliveries}'}'
                          : 'Webhook подключено: ${mine.length}',
                      style: golos(12, FontWeight.w600, colors.positive),
                    ),
                  ),
                ],
              )
            else
              TextButton.icon(
                style: TextButton.styleFrom(
                  padding: EdgeInsets.zero,
                  visualDensity: VisualDensity.compact,
                ),
                onPressed: kUseFixtures
                    ? null
                    : () => showCreateWebhook(context, ref, kind: kinds.first),
                icon: const Icon(Icons.add_link, size: 16),
                label: const Text('Подключить webhook'),
              ),
          ],
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final action in actions)
                action.$3
                    ? FilledButton.tonal(
                        onPressed: busy ? null : action.$2,
                        child: Text(action.$1),
                      )
                    : OutlinedButton(
                        onPressed: busy ? null : action.$2,
                        child: Text(action.$1),
                      ),
            ],
          ),
        ],
      ),
    );
  }
}
