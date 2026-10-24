import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../api/ai.dart';
import '../../api/debug.dart';
import '../../api/errors.dart';
import '../../api/models.dart';
import '../../app/config.dart';
import '../../app/providers.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';
import 'evaluation_section.dart';

final debugDatasetsProvider = FutureProvider<List<DebugDataset>>(
  (ref) => ref.watch(apiProvider).debugDatasets(),
);

final debugDatasetProvider = FutureProvider.family<DebugDataset, String>(
  (ref, id) => ref.watch(apiProvider).debugDataset(id),
);

final debugSelectedProvider = StateProvider<String?>((ref) => null);

String _megabytes(int bytes) {
  final tenths = (bytes / 1024 / 1024 * 10).round();
  return '${tenths ~/ 10},${tenths % 10} МБ';
}

String _count(int value) => groupDigits('$value');

String _errorText(Object error) =>
    error is ApiException ? error.message : 'Не удалось загрузить данные';

class DebugPage extends ConsumerStatefulWidget {
  const DebugPage({super.key});

  @override
  ConsumerState<DebugPage> createState() => _DebugPageState();
}

class _DebugPageState extends ConsumerState<DebugPage> {
  double? _sent;
  bool _parsing = false;
  bool _history = true;
  String? _error;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final phone = isPhone(context);
    final datasets = ref.watch(debugDatasetsProvider);
    final list = datasets.valueOrNull ?? const <DebugDataset>[];
    final selected =
        ref.watch(debugSelectedProvider) ??
        (list.isEmpty ? null : list.last.id);
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1180),
        child: ListView(
          key: const Key('debug-scroll'),
          padding: phone
              ? const EdgeInsets.fromLTRB(16, 14, 16, 120)
              : const EdgeInsets.fromLTRB(32, 28, 32, 48),
          children: [
            Text(
              'Отладка',
              style: unbounded(phone ? 28 : 30, FontWeight.w600, colors.text),
            ),
            const SizedBox(height: 8),
            Text(
              'Загрузите контрольный датасет в формате хакатона: Kassandra проверит строки и качество '
              'данных, построит прогноз для каждой компании, пошагово покажет расчёт и посчитает '
              'метрики на истории — MAE, MSE, матрицу ошибок, precision, recall, F1, Brier.',
              style: golos(15, FontWeight.w400, colors.textMuted),
            ),
            const SizedBox(height: 16),
            _UploadCard(
              sent: _sent,
              parsing: _parsing,
              error: _error,
              full: list.length >= 3,
              history: _history,
              onHistory: (value) => setState(() => _history = value),
              onPick: _pick,
            ),
            const SizedBox(height: 16),
            if (datasets.hasError)
              EmptyHint(text: _errorText(datasets.error!))
            else if (list.isNotEmpty) ...[
              _DatasetTabs(
                datasets: list,
                selected: selected,
                onSelect: (id) =>
                    ref.read(debugSelectedProvider.notifier).state = id,
                onDelete: _delete,
              ),
              const SizedBox(height: 16),
              if (selected != null) _Report(id: selected),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _pick() async {
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['csv', 'zip'],
      withData: true,
    );
    if (picked == null || picked.files.isEmpty) return;
    final file = picked.files.single;
    final bytes = file.bytes;
    if (bytes == null) {
      setState(() => _error = 'Не удалось прочитать файл');
      return;
    }
    setState(() {
      _error = null;
      _sent = 0;
      _parsing = false;
    });
    try {
      final report = await ref
          .read(apiProvider)
          .uploadDebugDataset(
            bytes,
            file.name,
            history: _history,
            onProgress: (sent, total) {
              if (!mounted || total <= 0) return;
              setState(() {
                _sent = sent / total;
                _parsing = sent >= total;
              });
            },
          );
      ref.invalidate(debugDatasetsProvider);
      // New accounts must reach the shell, or «Открыть прогноз» falls back to the first account.
      ref.invalidate(accountsProvider);
      ref.read(debugSelectedProvider.notifier).state = report.id;
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) {
        setState(() {
          _sent = null;
          _parsing = false;
        });
      }
    }
  }

  Future<void> _delete(DebugDataset dataset) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Удалить набор ${dataset.id}?'),
        content: Text(
          'Удалятся ${_count(dataset.companies)} компаний набора «${dataset.filename}» '
          'с операциями и прогнозами. Демо-счета не затрагиваются.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Удалить'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await ref.read(apiProvider).deleteDebugDataset(dataset.id);
      ref.invalidate(evaluationStateProvider(dataset.id));
      if (ref.read(debugSelectedProvider) == dataset.id) {
        ref.read(debugSelectedProvider.notifier).state = null;
      }
      ref.invalidate(debugDatasetsProvider);
      ref.invalidate(accountsProvider);
    } on ApiException catch (error) {
      ref.read(noticeProvider.notifier).state = error.message;
    }
  }
}

class _UploadCard extends StatelessWidget {
  const _UploadCard({
    required this.sent,
    required this.parsing,
    required this.error,
    required this.full,
    required this.history,
    required this.onHistory,
    required this.onPick,
  });

  final double? sent;
  final bool parsing;
  final String? error;
  final bool full;
  final bool history;
  final ValueChanged<bool> onHistory;
  final VoidCallback onPick;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final busy = sent != null;
    return SurfaceCard(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Загрузить датасет',
            style: golos(18, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 6),
          Text(
            'all_operations.csv, одна выписка statements/C001.csv или ZIP-архив с companies.csv — до 100 МБ. '
            'Набор получает свой префикс (dbg1-C001…) и не смешивается с демо-счетами; хранится до трёх наборов.',
            style: golos(14, FontWeight.w400, colors.textMuted),
          ),
          const SizedBox(height: 14),
          Wrap(
            spacing: 12,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              FilledButton.icon(
                key: const Key('debug-upload'),
                onPressed: busy || full ? null : onPick,
                icon: const Icon(Icons.upload_file, size: 18),
                label: const Text('Выбрать файл'),
              ),
              if (full)
                Text(
                  'Удалите один из наборов ниже, чтобы загрузить новый',
                  style: golos(13, FontWeight.w500, colors.textMuted),
                ),
            ],
          ),
          const SizedBox(height: 4),
          CheckboxListTile(
            key: const Key('debug-history'),
            value: history,
            onChanged: busy ? null : (value) => onHistory(value ?? true),
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            dense: true,
            title: Text(
              'Дополнить историей основного датасета',
              style: golos(14, FontWeight.w500, colors.text),
            ),
            subtitle: Text(
              'Если в файле только последние недели тех же компаний (C001…), модели нужна история '
              'до них: она берётся из основного набора, а метрики считаются только по датам файла.',
              style: golos(12, FontWeight.w400, colors.textMuted),
            ),
          ),
          if (busy) ...[
            const SizedBox(height: 14),
            LinearProgressIndicator(value: parsing ? null : sent),
            const SizedBox(height: 6),
            Text(
              parsing
                  ? 'Файл отправлен. Проверяем строки и импортируем — около 30 секунд на 222 тыс. операций'
                  : 'Отправляем файл · ${formatPercent(sent!)}',
              style: golos(13, FontWeight.w400, colors.textMuted),
            ),
          ],
          if (error != null) ...[
            const SizedBox(height: 12),
            Text(error!, style: golos(14, FontWeight.w600, colors.danger)),
          ],
        ],
      ),
    );
  }
}

class _DatasetTabs extends StatelessWidget {
  const _DatasetTabs({
    required this.datasets,
    required this.selected,
    required this.onSelect,
    required this.onDelete,
  });

  final List<DebugDataset> datasets;
  final String? selected;
  final ValueChanged<String> onSelect;
  final ValueChanged<DebugDataset> onDelete;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final dataset in datasets)
          InputChip(
            key: Key('debug-set-${dataset.id}'),
            selected: dataset.id == selected,
            label: Text(
              '${dataset.id} · ${dataset.filename} · ${_count(dataset.companies)} комп.',
            ),
            onPressed: () => onSelect(dataset.id),
            onDeleted: () => onDelete(dataset),
            deleteButtonTooltipMessage: 'Удалить набор',
          ),
      ],
    );
  }
}

class _Report extends ConsumerWidget {
  const _Report({required this.id});
  final String id;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final report = ref.watch(debugDatasetProvider(id));
    return report.when(
      loading: () => const Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: CircularProgressIndicator()),
      ),
      error: (error, _) => EmptyHint(text: _errorText(error)),
      data: (data) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Summary(data: data),
          if (data.rejected > 0) ...[
            const SizedBox(height: 16),
            _Rejected(data: data),
          ],
          const SizedBox(height: 16),
          _QualityCard(data: data),
          const SizedBox(height: 16),
          EvaluationSection(datasetId: data.id),
          const SizedBox(height: 16),
          _Companies(data: data),
        ],
      ),
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.data});
  final DebugDataset data;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final tiles = [
      ('Строк в файле', _count(data.rows), null),
      ('Принято', _count(data.accepted), null),
      (
        'Отклонено',
        _count(data.rejected),
        data.rejected > 0 ? colors.danger : null,
      ),
      ('Компаний', _count(data.companies), null),
      (
        'Период',
        '${formatDateDot(data.firstDate)} — ${formatDateDot(data.lastDate)}',
        null,
      ),
      ('Без категории', formatShare(data.unknownShare), null),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (isPhone(context))
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              for (final tile in tiles)
                SizedBox(
                  width: tile.$1 == 'Период' ? 260 : 160,
                  child: _Tile(tile: tile),
                ),
            ],
          )
        else
          Row(
            children: [
              for (final tile in tiles) ...[
                if (tile != tiles.first) const SizedBox(width: 12),
                Expanded(
                  flex: tile.$1 == 'Период' ? 2 : 1,
                  child: _Tile(tile: tile),
                ),
              ],
            ],
          ),
        const SizedBox(height: 10),
        Text(
          '${data.source} · ${_megabytes(data.sizeBytes)} · новых операций ${_count(data.inserted)}'
          '${data.duplicates > 0 ? ' · повторы пропущены: ${_count(data.duplicates)}' : ''}',
          style: golos(13, FontWeight.w400, colors.textMuted),
        ),
        if (data.historyCompanies > 0)
          Text(
            'История до начала файла взята из основного датасета: ${_count(data.historyCompanies)} '
            'компаний, ${_count(data.historyOperations)} операций',
            style: golos(13, FontWeight.w500, colors.text),
          ),
        for (final note in data.notes)
          Text(note, style: golos(13, FontWeight.w400, colors.textMuted)),
      ],
    );
  }
}

class _Tile extends StatelessWidget {
  const _Tile({required this.tile});
  final (String, String, Color?) tile;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return SurfaceCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(tile.$1, style: golos(13, FontWeight.w500, colors.textMuted)),
          const SizedBox(height: 6),
          Text(
            tile.$2,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: golos(18, FontWeight.w700, tile.$3 ?? colors.text),
          ),
        ],
      ),
    );
  }
}

class _Rejected extends StatelessWidget {
  const _Rejected({required this.data});
  final DebugDataset data;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final rest = data.rejected - data.errors.length;
    return SurfaceCard(
      padding: const EdgeInsets.all(20),
      borderColor: colors.danger.withValues(alpha: 0.4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Отклонённые строки',
            style: golos(18, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 4),
          Text(
            'Эти строки не импортированы, остальные приняты. Номер — строка файла с заголовком.',
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
          const SizedBox(height: 8),
          for (final error in data.errors)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 96,
                    child: Text(
                      'строка ${_count(error.line)}',
                      style: mono(13, colors.textMuted),
                    ),
                  ),
                  Expanded(
                    child: Text(
                      error.reason,
                      style: golos(14, FontWeight.w400, colors.text),
                    ),
                  ),
                ],
              ),
            ),
          if (rest > 0)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                'и ещё ${_count(rest)}',
                style: golos(13, FontWeight.w500, colors.textMuted),
              ),
            ),
        ],
      ),
    );
  }
}

class _QualityCard extends StatelessWidget {
  const _QualityCard({required this.data});
  final DebugDataset data;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final q = data.quality;
    final lines = [
      (
        'Остаток не сходится с операциями',
        '${_count(q.inconsistentTransitions)} переходов, из них больше копейки — ${_count(q.materialTransitions)}',
      ),
      (
        'Дни под маской качества',
        '${_count(q.invalidDays)} — в метриках и обучении не участвуют',
      ),
      ('Подозрительные дубли', '${_count(q.suspiciousDuplicateGroups)} групп'),
      ('Пустое назначение платежа', _count(q.emptyPurpose)),
      ('Без ИНН контрагента', _count(q.emptyInn)),
      (
        'Без категории',
        '${_count(data.unknownOperations)} операций · ${formatShare(data.unknownShare)} оборота',
      ),
    ];
    final phone = isPhone(context);
    final quality = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Качество данных', style: golos(18, FontWeight.w600, colors.text)),
        const SizedBox(height: 8),
        for (final line in lines)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text.rich(
              TextSpan(
                children: [
                  TextSpan(
                    text: '${line.$1}: ',
                    style: golos(14, FontWeight.w500, colors.textMuted),
                  ),
                  TextSpan(
                    text: line.$2,
                    style: golos(14, FontWeight.w600, colors.text),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
    final categories = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Крупнейшие категории',
          style: golos(18, FontWeight.w600, colors.text),
        ),
        const SizedBox(height: 8),
        for (final category in data.topCategories)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    category.label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: golos(14, FontWeight.w500, colors.text),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  formatMoneyShort(category.amountMinor),
                  style: golos(14, FontWeight.w600, colors.text),
                ),
                SizedBox(
                  width: 96,
                  child: Text(
                    '${_count(category.operations)} оп.',
                    textAlign: TextAlign.right,
                    style: golos(12, FontWeight.w400, colors.textMuted),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
    return SurfaceCard(
      padding: const EdgeInsets.all(20),
      child: phone
          ? Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [quality, const SizedBox(height: 20), categories],
            )
          : Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(child: quality),
                const SizedBox(width: 32),
                Expanded(child: categories),
              ],
            ),
    );
  }
}

class _Companies extends ConsumerStatefulWidget {
  const _Companies({required this.data});
  final DebugDataset data;

  @override
  ConsumerState<_Companies> createState() => _CompaniesState();
}

class _CompaniesState extends ConsumerState<_Companies> {
  static const _page = 30;
  int _shown = _page;
  bool _gapsFirst = true;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final phone = isPhone(context);
    final result = ref.watch(evaluationStateProvider(widget.data.id))?.result;
    final rows = {
      for (final row in result?.rows ?? const <EvaluationRow>[])
        row.accountId: row,
    };
    final accounts = [...widget.data.accounts];
    if (_gapsFirst && rows.isNotEmpty) {
      // A gap ahead first, then accounts already in a deficit, then the rest.
      int rank(DebugAccount a) {
        final row = rows[a.accountId];
        if (row == null || row.closed) return 3;
        if (row.gapDate == null) return 2;
        return a.lastBalanceMinor < 0 ? 1 : 0;
      }

      accounts.sort((a, b) => rank(a).compareTo(rank(b)));
    }
    return SurfaceCard(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: 12,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            alignment: WrapAlignment.spaceBetween,
            children: [
              Text(
                'Компании · ${_count(accounts.length)}',
                style: golos(18, FontWeight.w600, colors.text),
              ),
              if (rows.isNotEmpty)
                FilterChip(
                  label: const Text('Сначала с разрывом'),
                  selected: _gapsFirst,
                  onSelected: (value) => setState(() => _gapsFirst = value),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            rows.isEmpty
                ? 'Запустите «Рассчитать прогноз и метрики» выше: для каждой компании появятся '
                      'прогноз, ошибка на истории, графики и журнал расчёта.'
                : '«График» — прогноз на 30 дней, проверка на истории против факта и пошаговый '
                      'журнал расчёта. «Без изменений» — остаток, который просто не меняется.',
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
          if (result != null && !kUseFixtures) ...[
            const SizedBox(height: 12),
            _ModelReview(datasetId: widget.data.id),
          ],
          const SizedBox(height: 12),
          if (!phone) const _HeaderRow(),
          for (final account in accounts.take(_shown))
            _CompanyRow(
              account: account,
              row: rows[account.accountId],
              phone: phone,
              onChart: rows[account.accountId] == null
                  ? null
                  : () => showEvaluationCompany(
                      context,
                      datasetId: widget.data.id,
                      accountId: account.accountId,
                      title:
                          '${account.name.isEmpty ? account.companyId : account.name} · ${account.accountId}',
                      onOpen: () => _open(account),
                    ),
              onOpen: () => _open(account),
            ),
          if (accounts.length > _shown)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Align(
                alignment: Alignment.centerLeft,
                child: OutlinedButton(
                  onPressed: () => setState(() => _shown += _page),
                  child: Text(
                    'Показать ещё · ${_count(accounts.length - _shown)}',
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Future<void> _open(DebugAccount account) async {
    final list = await ref.read(accountsProvider.future);
    final found = list.where((a) => a.id == account.accountId);
    if (found.isEmpty) {
      ref.read(noticeProvider.notifier).state =
          'Счёт ещё не появился, обновите страницу';
      return;
    }
    ref
        .read(sessionProvider.notifier)
        .applyAccount(found.first, asOf: account.lastDate);
    if (!mounted) return;
    context.go(
      '/forecast?account=${account.accountId}&as_of=${isoDate(account.lastDate)}',
    );
  }
}

class _HeaderRow extends StatelessWidget {
  const _HeaderRow();

  @override
  Widget build(BuildContext context) {
    final style = golos(
      12,
      FontWeight.w600,
      KassandraColors.of(context).textMuted,
    );
    return Container(
      padding: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        border: Border(
          bottom: BorderSide(color: KassandraColors.of(context).divider),
        ),
      ),
      child: Row(
        children: [
          // Same widths and gaps as _CompanyRow, so the columns line up.
          Expanded(flex: 3, child: Text('Компания', style: style)),
          const SizedBox(width: 12),
          Expanded(
            flex: 3,
            child: Text('Выписка: операций · остаток', style: style),
          ),
          const SizedBox(width: 12),
          Expanded(flex: 3, child: Text('Прогноз на 30 дней', style: style)),
          const SizedBox(width: 12),
          Expanded(flex: 4, child: Text('Проверка на истории', style: style)),
          const SizedBox(width: 222),
        ],
      ),
    );
  }
}

class _CompanyRow extends StatelessWidget {
  const _CompanyRow({
    required this.account,
    required this.row,
    required this.phone,
    required this.onChart,
    required this.onOpen,
  });

  final DebugAccount account;
  final EvaluationRow? row;
  final bool phone;
  final VoidCallback? onChart;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final muted = golos(13, FontWeight.w400, colors.textMuted);
    final strong = golos(14, FontWeight.w600, colors.text);
    final company = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          account.name.isEmpty ? account.companyId : account.name,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: strong,
        ),
        Text(account.accountId, style: mono(12, colors.textMuted)),
      ],
    );
    final flags = [
      if (account.inconsistentTransitions > 0)
        'несверок ${account.inconsistentTransitions}',
      if (account.suspiciousDuplicates > 0)
        'дублей ${account.suspiciousDuplicates}',
    ];
    final statement = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '${_count(account.operations)} оп. · ${formatMoneyShort(account.lastBalanceMinor)}',
          style: golos(14, FontWeight.w500, colors.text),
        ),
        Text(
          '${formatDateDot(account.firstDate)} — ${formatDateDot(account.lastDate)}'
          '${flags.isEmpty ? '' : ' · ${flags.join(', ')}'}',
          style: muted,
        ),
      ],
    );
    final r = row;
    // The chain ensemble → hgb → weekday: say which model ran when it is not the main one.
    final fallback = switch (r?.model) {
      'hgb' => 'модель hgb: ансамбль доступен с 28.02.2026',
      'weekday' => 'модель по дням недели: счёт закрыт до 31.12.2025',
      _ => null,
    };
    final forecast = r == null
        ? Text('—', style: muted)
        : r.closed
        ? Text('Счёт закрыт ${formatDateDot(r.asOf)}', style: muted)
        : Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (r.gapDate == null)
                Text(
                  'Разрыва нет',
                  style: golos(14, FontWeight.w600, colors.positive),
                )
              else ...[
                Text(
                  'Разрыв ${formatDayMonth(r.gapDate!)}',
                  style: golos(14, FontWeight.w600, colors.danger),
                ),
                Text(
                  'не хватит ${formatMoneyShort(r.needMinor, ceil: true)}',
                  style: muted,
                ),
              ],
              if (r.probability != null)
                Text(
                  'вероятность ${formatPercent(r.probability!)}',
                  style: muted,
                ),
              if (fallback != null) Text(fallback, style: muted),
            ],
          );
    final history = r == null
        ? const SizedBox.shrink()
        : r.maeMinor == null
        ? Text('Короткая история: проверки нет', style: muted)
        : Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'MAE ${formatMoneyShort(r.maeMinor!)} · без изменений ${formatMoneyShort(r.constantMaeMinor ?? 0)}',
                style: golos(14, FontWeight.w500, colors.text),
              ),
              Text(
                '${r.windows} окон'
                '${r.skill == null ? '' : ' · skill ${r.skill!.toStringAsFixed(2).replaceAll('.', ',')}'}',
                style: golos(
                  13,
                  FontWeight.w400,
                  (r.skill ?? 0) > 0 ? colors.positive : colors.textMuted,
                ),
              ),
            ],
          );
    final actions = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        OutlinedButton(onPressed: onChart, child: const Text('График')),
        TextButton(onPressed: onOpen, child: const Text('Открыть')),
      ],
    );
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: colors.divider)),
      ),
      child: phone
          ? Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                company,
                const SizedBox(height: 4),
                statement,
                const SizedBox(height: 6),
                forecast,
                const SizedBox(height: 4),
                history,
                const SizedBox(height: 6),
                actions,
              ],
            )
          : Row(
              children: [
                Expanded(flex: 3, child: company),
                const SizedBox(width: 12),
                Expanded(flex: 3, child: statement),
                const SizedBox(width: 12),
                Expanded(flex: 3, child: forecast),
                const SizedBox(width: 12),
                Expanded(flex: 4, child: history),
                const SizedBox(width: 12),
                SizedBox(width: 210, child: actions),
              ],
            ),
    );
  }
}

/// The LLM reads the check of the set and says how good the AI forecast is here and
/// what would improve it («проверка результатов ИИ и улучшение»).
class _ModelReview extends ConsumerStatefulWidget {
  const _ModelReview({required this.datasetId});
  final String datasetId;

  @override
  ConsumerState<_ModelReview> createState() => _ModelReviewState();
}

class _ModelReviewState extends ConsumerState<_ModelReview> {
  AiModelReview? _review;
  String? _error;
  bool _busy = false;

  Future<void> _run() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final review = await ref
          .read(apiProvider)
          .aiModelReview(widget.datasetId);
      if (mounted) setState(() => _review = review);
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final review = _review;
    Widget list(String title, List<String> items) => items.isEmpty
        ? const SizedBox.shrink()
        : Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: golos(14, FontWeight.w700, colors.text)),
                for (final item in items)
                  Padding(
                    padding: const EdgeInsets.only(top: 3),
                    child: Text(
                      '• $item',
                      style: golos(13, FontWeight.w400, colors.text),
                    ),
                  ),
              ],
            ),
          );
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        border: Border.all(color: colors.primary.withValues(alpha: 0.3)),
        borderRadius: BorderRadius.circular(KRadius.tile),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.auto_awesome, color: colors.primary, size: 20),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'Разбор модели — $kAiName',
                  style: golos(16, FontWeight.w600, colors.text),
                ),
              ),
              if (!_busy)
                FilledButton.tonal(
                  key: const Key('debug-ai-review'),
                  onPressed: _run,
                  child: Text(review == null ? 'Разобрать' : 'Ещё раз'),
                ),
            ],
          ),
          if (_busy) ...[
            const SizedBox(height: 10),
            const LinearProgressIndicator(),
            const SizedBox(height: 6),
            Text(
              '$kAiName изучает метрики и компании и думает, что улучшить…',
              style: golos(13, FontWeight.w400, colors.textMuted),
            ),
          ],
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                _error!,
                style: golos(14, FontWeight.w600, colors.danger),
              ),
            ),
          if (review != null) ...[
            const SizedBox(height: 10),
            Text(
              review.headline,
              style: golos(15, FontWeight.w600, colors.text),
            ),
            list('Сильные стороны', review.strengths),
            list('Слабые места', review.weaknesses),
            list('Закономерности', review.patterns),
            if (review.improvements.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(
                'Что улучшить',
                style: golos(14, FontWeight.w700, colors.text),
              ),
              for (final item in review.improvements)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text.rich(
                    TextSpan(
                      children: [
                        TextSpan(
                          text: '${item.title}. ',
                          style: golos(13, FontWeight.w700, colors.text),
                        ),
                        TextSpan(
                          text:
                              '${item.detail} Эффект: ${item.expectedEffect} Проверка: ${item.howToValidate}',
                          style: golos(13, FontWeight.w400, colors.textMuted),
                        ),
                      ],
                    ),
                  ),
                ),
            ],
            list('Риски', review.risks),
            if (review.reasoning.isNotEmpty)
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: Text(
                  'Как рассуждала модель',
                  style: golos(13, FontWeight.w600, colors.primary),
                ),
                children: [
                  SelectableText(
                    review.reasoning,
                    style: mono(11, colors.textMuted),
                  ),
                ],
              ),
          ],
        ],
      ),
    );
  }
}
