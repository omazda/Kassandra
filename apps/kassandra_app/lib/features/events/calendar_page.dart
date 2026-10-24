import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../api/errors.dart';
import '../../api/models.dart';
import '../../api/v11.dart';
import '../../api/views.dart';
import '../../app/providers.dart';
import '../../design/icons.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';
import 'calendar_edit.dart';
import 'calendar_logic.dart';

/// Forecast rows the calendar must show even when the event registry is empty:
/// history repeats (`regular`), taxes, and anything already stored in the registry.
List<PlannedEvent> calendarEvents({
  required List<PlannedEvent> registry,
  required List<PlannedEvent> forecast,
}) {
  const fromForecast = {'regular', 'tax', 'tax_calendar'};
  final merged = <String, PlannedEvent>{
    for (final event in forecast)
      if (fromForecast.contains(event.source)) event.id: event,
    for (final event in registry) event.id: event,
  };
  final rows = merged.values.toList()
    ..sort((a, b) => a.expectedDate.compareTo(b.expectedDate));
  return rows;
}

String taxEventLine(Map event) {
  final date = asDateOpt(event['expected_date'] ?? event['date']);
  final raw = (event['label'] ?? 'Налог').toString();
  final label = raw == 'Единый налоговый платёж' ? 'ЕНП' : raw;
  final amount = formatMoneyExact(asInt(event['amount_minor']));
  final when = date == null ? '' : '${formatDateShort(date)} · ';
  return '$when$label · $amount';
}

class CalendarFilters {
  const CalendarFilters({
    this.presetDays = 30,
    this.customFrom,
    this.customTo,
    this.direction,
    this.obligatory = false,
    this.sources = const {},
    this.category,
    this.amountMinMinor,
    this.amountMaxMinor,
    this.includeCancelled = false,
    this.query = '',
  });

  final int? presetDays;
  final DateTime? customFrom;
  final DateTime? customTo;
  final String? direction;
  final bool obligatory;
  final Set<String> sources;
  final String? category;
  final int? amountMinMinor;
  final int? amountMaxMinor;
  final bool includeCancelled;
  final String query;

  int get activeCount {
    var count = 0;
    if (presetDays != 30) count++;
    if (direction != null) count++;
    if (obligatory) count++;
    if (sources.isNotEmpty) count++;
    if (category != null && category!.isNotEmpty) count++;
    if (amountMinMinor != null || amountMaxMinor != null) count++;
    if (includeCancelled) count++;
    if (query.trim().isNotEmpty) count++;
    return count;
  }

  CalendarQuery toQuery(DateTime asOf) {
    final DateTime from;
    final DateTime to;
    if (presetDays != null) {
      from = asOf;
      to = asOf.add(Duration(days: presetDays!));
    } else {
      from = customFrom ?? asOf;
      to = customTo ?? asOf.add(const Duration(days: 30));
    }
    return CalendarQuery(
      dateFrom: from,
      dateTo: to,
      direction: direction,
      sources: sources.toList(),
      category: category,
      obligatory: obligatory,
      includeCancelled: includeCancelled,
      query: query,
      amountMin: amountMinMinor,
      amountMax: amountMaxMinor,
    );
  }
}

class CalendarFiltersNotifier extends Notifier<CalendarFilters> {
  @override
  CalendarFilters build() => const CalendarFilters();

  void replace(CalendarFilters next) => state = next;

  void reset() => state = const CalendarFilters();
}

final calendarFiltersProvider =
    NotifierProvider<CalendarFiltersNotifier, CalendarFilters>(
      CalendarFiltersNotifier.new,
    );

final paymentCalendarProvider = FutureProvider<CalendarView>((ref) async {
  final session = ref.watch(sessionProvider);
  final filters = ref.watch(calendarFiltersProvider);
  final asOf = session.asOf;
  if (asOf == null) throw ApiFailure('Выберите дату расчёта');
  final bundle = await ref.watch(forecastBundleProvider.future);
  return ref
      .watch(apiProvider)
      .paymentCalendar(bundle.forecast.runId, filters.toQuery(asOf));
});

/// Tax calendar for the footer hint. A provider, not a FutureBuilder: the hint used to
/// re-request the server on every rebuild of the calendar.
final taxHintProvider = FutureProvider<Map<String, dynamic>>((ref) async {
  final session = ref.watch(sessionProvider);
  if (session.accountId == null || session.asOf == null) return const {};
  // The forecast of this account and date: the previous run id after «Сменить счёт» gets a 422.
  final bundle = await ref.watch(forecastBundleProvider.future);
  return ref
      .watch(apiProvider)
      .taxCalendar(session.accountId!, session.asOf!, bundle.forecast.runId);
});

final recurringProvider = FutureProvider<List<RecurringItem>>((ref) async {
  final session = ref.watch(sessionProvider);
  if (session.accountId == null || session.asOf == null) return const [];
  return ref.watch(apiProvider).recurring(session.accountId!, session.asOf!);
});

class CalendarPage extends ConsumerStatefulWidget {
  const CalendarPage({super.key});

  @override
  ConsumerState<CalendarPage> createState() => _CalendarPageState();
}

class _CalendarPageState extends ConsumerState<CalendarPage> {
  final _search = TextEditingController();
  Timer? _searchTimer;
  String? _selectedId;
  String? _pendingOpen;

  @override
  void dispose() {
    _searchTimer?.cancel();
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final phone = isPhone(context);
    final wide = isDesktop(context);
    final async = ref.watch(paymentCalendarProvider);
    final view = async.valueOrNull;
    if (view == null) {
      if (async.hasError) {
        return ErrorPane(
          error: async.error!,
          onRetry: () => ref.invalidate(paymentCalendarProvider),
        );
      }
      return const Center(child: CircularProgressIndicator());
    }
    final forecast = ref.watch(forecastBundleProvider).valueOrNull?.forecast;
    final filters = ref.watch(calendarFiltersProvider);
    final risks =
        ref.watch(counterpartyRisksProvider).valueOrNull ??
        const <CounterpartyRisk>[];
    final recurring =
        ref.watch(recurringProvider).valueOrNull ?? const <RecurringItem>[];
    final dictionaries = ref.watch(dictionariesProvider).valueOrNull;
    final selected = _find(view, _selectedId);
    if (_pendingOpen != null && _find(view, _pendingOpen) != null) {
      final id = _pendingOpen!;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted || _pendingOpen != id) return;
        setState(() {
          _selectedId = id;
          _pendingOpen = null;
        });
        if (!wide) _openSheet(_find(view, id)!);
      });
    }
    final body = ListView(
      key: const Key('page-scroll'),
      padding: phone
          ? const EdgeInsets.fromLTRB(16, 12, 16, 128)
          : const EdgeInsets.fromLTRB(32, 28, 32, 40),
      children: [
        _Header(
          phone: phone,
          forecast: forecast,
          dateTo: view.dateTo,
          sources: view.facets.sources,
        ),
        const SizedBox(height: 12),
        if (forecast != null) _GapBanner(forecast: forecast, phone: phone),
        const SizedBox(height: 12),
        if (phone)
          _PhoneFilters(filters: filters, facets: view.facets)
        else
          _DesktopFilters(
            filters: filters,
            facets: view.facets,
            search: _search,
            onQuery: _onQuery,
          ),
        const SizedBox(height: 12),
        if (phone)
          _PhoneTotals(totals: view.totals)
        else
          _DesktopTotals(view: view, forecast: forecast),
        const SizedBox(height: 8),
        for (final group in _groups(view.items))
          _DayCard(
            date: group.date,
            balance: forecast == null
                ? null
                : closingOn(forecast.points, group.date),
            gap: forecast?.risk.firstGapDate,
            phone: phone,
            children: [
              for (final item in group.items)
                _PaymentRow(
                  item: item,
                  selected: item.id == selected?.id,
                  phone: phone,
                  embedded: true,
                  rhythm: _rhythm(item, recurring),
                  risky: _risky(item, risks),
                  onOpen: () => _open(item, wide),
                  onEdit: () => _open(item, wide),
                  onDelete: () => _delete(item),
                  onSkip: () => _skip(item),
                ),
            ],
          ),
        const SizedBox(height: 8),
        const _TaxFooter(),
      ],
    );
    return Stack(
      children: [
        if (!wide || selected == null)
          body
        else
          Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(child: body),
              SizedBox(
                width: 360,
                child: Material(
                  color: colors.surface,
                  child: PaymentEditor(
                    key: ValueKey(selected.id),
                    item: selected,
                    points: forecast?.points ?? const [],
                    knownAt: forecast?.asOf ?? view.asOf,
                    categories: _categories(dictionaries),
                    onSave: (changed) => _save(selected, changed),
                    onDelete: () => _delete(selected),
                    onSkip: () => _skip(selected),
                  ),
                ),
              ),
            ],
          ),
      ],
    );
  }

  void _onQuery(String value) {
    _searchTimer?.cancel();
    _searchTimer = Timer(const Duration(milliseconds: 300), () {
      final current = ref.read(calendarFiltersProvider);
      ref
          .read(calendarFiltersProvider.notifier)
          .replace(
            CalendarFilters(
              presetDays: current.presetDays,
              customFrom: current.customFrom,
              customTo: current.customTo,
              direction: current.direction,
              obligatory: current.obligatory,
              sources: current.sources,
              category: current.category,
              amountMinMinor: current.amountMinMinor,
              amountMaxMinor: current.amountMaxMinor,
              includeCancelled: current.includeCancelled,
              query: value,
            ),
          );
    });
  }

  List<CodeLabel> _categories(Dictionaries? dictionaries) {
    if (dictionaries != null && dictionaries.categories.isNotEmpty) {
      return dictionaries.categories;
    }
    return [
      for (final entry in categoryLabels.entries)
        CodeLabel(code: entry.key, label: entry.value),
    ];
  }

  String? _rhythm(CalendarItem item, List<RecurringItem> items) {
    if (!item.assumption) return null;
    for (final component in items) {
      if (component.componentId == item.componentId) {
        return rhythmCaption(component, item.date);
      }
    }
    return null;
  }

  bool _risky(CalendarItem item, List<CounterpartyRisk> risks) {
    final inn = item.counterpartyInn;
    if (inn == null || inn.isEmpty) return false;
    return risks.any(
      (risk) => risk.inn == inn && showsCounterpartyRisk(risk.risk),
    );
  }

  CalendarItem? _find(CalendarView view, String? id) {
    if (id == null) return null;
    for (final item in view.items) {
      if (item.id == id) return item;
    }
    return null;
  }

  void _open(CalendarItem item, bool wide) {
    if (item.cancelled || !item.hasAction('edit')) return;
    if (wide) {
      setState(() => _selectedId = item.id);
      return;
    }
    _openSheet(item);
  }

  Future<void> _openSheet(CalendarItem item) async {
    final forecast = ref.read(forecastBundleProvider).valueOrNull?.forecast;
    ref.read(phoneAddVisibleProvider.notifier).state = false;
    try {
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (context) => FractionallySizedBox(
          heightFactor: 0.92,
          child: PaymentEditor(
            item: item,
            points: forecast?.points ?? const [],
            knownAt: forecast?.asOf ?? item.date,
            categories: _categories(ref.read(dictionariesProvider).valueOrNull),
            compact: true,
            onSave: (changed) => _save(item, changed),
            onDelete: () => _delete(item),
            onSkip: () => _skip(item),
          ),
        ),
      );
    } finally {
      ref.read(phoneAddVisibleProvider.notifier).state = true;
    }
  }

  Future<void> _save(CalendarItem item, Map<String, dynamic> changed) async {
    final session = ref.read(sessionProvider);
    final knownAt = session.asOf;
    final runId = ref.read(forecastBundleProvider).valueOrNull?.forecast.runId;
    if (knownAt == null || runId == null) return;
    final body = {...changed, 'known_at': isoDate(knownAt)};
    try {
      if (item.assumption) {
        await ref.read(apiProvider).editAssumption(runId, item.id, {
          'action': 'edit',
          ...body,
        });
      } else {
        await ref.read(apiProvider).patchEvent(item.id, body);
      }
      ref.read(sessionProvider.notifier).refresh();
    } on ConflictException catch (error) {
      ref.read(noticeProvider.notifier).state = error.message;
      if (error.eventId != null) {
        ref.read(sessionProvider.notifier).refresh();
        setState(() => _pendingOpen = error.eventId);
      }
    } on ApiException catch (error) {
      ref.read(noticeProvider.notifier).state = error.message;
    }
  }

  Future<void> _delete(CalendarItem item) async {
    if (!item.hasAction('delete')) return;
    final knownAt = ref.read(sessionProvider).asOf;
    if (knownAt == null) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Удалить платёж?'),
        content: const Text('Он уйдёт из прогноза'),
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
      await ref.read(apiProvider).deleteEvent(item.id, knownAt);
      if (mounted) setState(() => _selectedId = null);
      if (!item.assumption) {
        ref
            .read(removedPaymentHintProvider.notifier)
            .state = RemovedPaymentHint(
          amountMinor: item.amountMinor,
          categoryLabel: item.categoryLabel,
        );
      }
      ref.read(sessionProvider.notifier).refresh();
    } on ApiException catch (error) {
      ref.read(noticeProvider.notifier).state = error.message;
    }
  }

  Future<void> _skip(CalendarItem item) async {
    final knownAt = ref.read(sessionProvider).asOf;
    final runId = ref.read(forecastBundleProvider).valueOrNull?.forecast.runId;
    if (knownAt == null || runId == null) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Не учитывать этот платёж?'),
        content: const Text('Он уйдёт из прогноза'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Не учитывать'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await ref.read(apiProvider).editAssumption(runId, item.id, {
        'action': 'skip',
        'known_at': isoDate(knownAt),
      });
      if (mounted) setState(() => _selectedId = null);
      ref.read(sessionProvider.notifier).refresh();
    } on ConflictException catch (error) {
      ref.read(noticeProvider.notifier).state = error.message;
      if (error.eventId != null) {
        ref.read(sessionProvider.notifier).refresh();
        setState(() => _pendingOpen = error.eventId);
      }
    } on ApiException catch (error) {
      ref.read(noticeProvider.notifier).state = error.message;
    }
  }
}

class _DayGroup {
  const _DayGroup(this.date, this.items);
  final DateTime date;
  final List<CalendarItem> items;
}

List<_DayGroup> _groups(List<CalendarItem> items) {
  final groups = <_DayGroup>[];
  for (final item in items) {
    if (groups.isEmpty || !sameDate(groups.last.date, item.date)) {
      groups.add(_DayGroup(item.date, [item]));
    } else {
      groups.last.items.add(item);
    }
  }
  return groups;
}

class _Header extends ConsumerWidget {
  const _Header({
    required this.phone,
    required this.forecast,
    required this.dateTo,
    required this.sources,
  });

  final bool phone;
  final ForecastResult? forecast;
  final DateTime dateTo;
  final List<FacetCount> sources;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final asOf = forecast?.asOf ?? ref.watch(sessionProvider).asOf;
    final title = phone ? 'Календарь' : 'Календарь платежей';
    final subtitle = asOf == null
        ? ''
        : 'Прогноз на ${formatDateFull(asOf)} · платежи до ${formatDateFull(dateTo)} · ${calendarSourcesLine([for (final source in sources) source.label])}';
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: phone
                    ? unbounded(28, FontWeight.w600, colors.text)
                    : unbounded(30, FontWeight.w600, colors.text),
              ),
              if (!phone) ...[
                const SizedBox(height: 4),
                Text(
                  subtitle,
                  style: golos(14, FontWeight.w400, colors.textMuted),
                ),
              ],
            ],
          ),
        ),
        if (!phone && asOf != null) ...[
          _DateChip(date: asOf),
          const SizedBox(width: 8),
          FilledButton.icon(
            key: const Key('add-payment'),
            onPressed: () => context.go(
              '/events/new${sectionQuery(ref.read(sessionProvider))}',
            ),
            icon: const Icon(Icons.add, size: 18),
            label: const Text('Добавить платёж'),
          ),
        ],
      ],
    );
  }
}

class _DateChip extends ConsumerWidget {
  const _DateChip({required this.date});
  final DateTime date;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    return OutlinedButton(
      onPressed: () async {
        final picked = await showDatePicker(
          context: context,
          initialDate: date,
          firstDate: DateTime.utc(2020),
          lastDate: DateTime.utc(2030),
        );
        if (picked == null) return;
        ref.read(sessionProvider.notifier).setAsOf(picked);
      },
      child: Text(
        formatDateDot(date),
        style: golos(14, FontWeight.w600, colors.text),
      ),
    );
  }
}

class _GapBanner extends StatelessWidget {
  const _GapBanner({required this.forecast, required this.phone});
  final ForecastResult forecast;
  final bool phone;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final risk = forecast.risk;
    final gap = risk.firstGapDate;
    if (gap == null) {
      return const SizedBox.shrink();
    }
    final need = formatMoneyCeil(risk.liquidityNeedMinor);
    final text = phone
        ? '${formatDayMonth(gap)} разрыв · не хватит $need'
        : '${formatDayMonth(gap)} остаток уйдёт в минус — до ${formatDayMonth(forecast.horizonEnd)} не хватит $need. Вероятность ${risk.probability30d == null ? '—' : formatPercent(risk.probability30d!)}, экспериментальная оценка.';
    return Material(
      color: colors.warningContainer,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: colors.warningOutline),
      ),
      child: InkWell(
        key: const Key('calendar-gap'),
        borderRadius: BorderRadius.circular(16),
        onTap: () => context.go('/gap${sectionQuery(_session(context))}'),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          child: Row(
            children: [
              Icon(
                Icons.warning_amber_rounded,
                color: colors.onWarningContainer,
                size: 18,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  text,
                  style: golos(14, FontWeight.w500, colors.text),
                ),
              ),
              Text(
                'Что сделать',
                style: golos(14, FontWeight.w600, colors.primary),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

SessionState _session(BuildContext context) {
  return ProviderScope.containerOf(context).read(sessionProvider);
}

class _DesktopFilters extends ConsumerWidget {
  const _DesktopFilters({
    required this.filters,
    required this.facets,
    required this.search,
    required this.onQuery,
  });

  final CalendarFilters filters;
  final CalendarFacets facets;
  final TextEditingController search;
  final ValueChanged<String> onQuery;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.surface,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                _PeriodControl(filters: filters),
                _DirectionControl(filters: filters),
                SizedBox(
                  width: 280,
                  child: TextField(
                    controller: search,
                    onChanged: onQuery,
                    decoration: const InputDecoration(
                      isDense: true,
                      hintText: 'Контрагент, номер счёта, ИНН',
                      prefixIcon: Icon(Icons.search, size: 18),
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                FilterChip(
                  label: Text('Только обязательные · ${facets.obligatory}'),
                  selected: filters.obligatory,
                  onSelected: (value) =>
                      _patch(ref, obligatory: value, setObligatory: true),
                  selectedColor: colors.warningContainer,
                ),
                Text(
                  'Источник:',
                  style: golos(13, FontWeight.w500, colors.textMuted),
                ),
                for (final source in facets.sources)
                  FilterChip(
                    label: Text('${source.label} · ${source.count}'),
                    selected: filters.sources.contains(source.code),
                    onSelected: (value) {
                      final next = {...filters.sources};
                      if (value) {
                        next.add(source.code);
                      } else {
                        next.remove(source.code);
                      }
                      _patch(ref, sources: next);
                    },
                  ),
                _CategoryMenu(filters: filters, facets: facets),
                _AmountMenu(filters: filters),
                if (filters.activeCount > 0)
                  TextButton(
                    onPressed: () {
                      search.clear();
                      ref.read(calendarFiltersProvider.notifier).reset();
                    },
                    child: const Text('Сбросить'),
                  ),
              ],
            ),
            const SizedBox(height: 4),
            Row(
              children: [
                Checkbox(
                  value: filters.includeCancelled,
                  onChanged: (value) => _patch(
                    ref,
                    includeCancelled: value ?? false,
                    setCancelled: true,
                  ),
                ),
                Text(
                  'Показать удалённые',
                  style: golos(14, FontWeight.w400, colors.text),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _PhoneFilters extends ConsumerWidget {
  const _PhoneFilters({required this.filters, required this.facets});
  final CalendarFilters filters;
  final CalendarFacets facets;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final count = filters.activeCount;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Spacer(),
            IconButton(
              tooltip: 'Поиск',
              onPressed: () async {
                final text = await _ask(context, filters.query);
                if (text == null) return;
                _patch(ref, query: text, setQuery: true);
              },
              icon: Icon(Icons.search, color: colors.text),
            ),
            Badge(
              isLabelVisible: count > 0,
              label: Text('$count'),
              child: IconButton(
                key: const Key('calendar-filters'),
                tooltip: 'Фильтры',
                onPressed: () => _sheet(context, ref),
                icon: Icon(Icons.filter_alt_outlined, color: colors.text),
              ),
            ),
          ],
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            children: [
              ActionChip(
                label: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(_periodChipLabel(filters)),
                    const Icon(Icons.expand_more, size: 18),
                  ],
                ),
                onPressed: () => _sheet(context, ref),
              ),
              const SizedBox(width: 8),
              FilterChip(
                label: const Text('Списания'),
                selected: filters.direction == 'out',
                onSelected: (value) => _patch(
                  ref,
                  direction: value ? 'out' : null,
                  setDirection: true,
                ),
              ),
              const SizedBox(width: 8),
              FilterChip(
                label: const Text('Поступления'),
                selected: filters.direction == 'in',
                onSelected: (value) => _patch(
                  ref,
                  direction: value ? 'in' : null,
                  setDirection: true,
                ),
              ),
              const SizedBox(width: 8),
              FilterChip(
                label: Text('Обязательные · ${facets.obligatory}'),
                selected: filters.obligatory,
                onSelected: (value) =>
                    _patch(ref, obligatory: value, setObligatory: true),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Future<void> _sheet(BuildContext context, WidgetRef ref) async {
    ref.read(phoneAddVisibleProvider.notifier).state = false;
    try {
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (context) => Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
          child: Consumer(
            builder: (context, ref, _) {
              final filters = ref.watch(calendarFiltersProvider);
              return Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Фильтры',
                    style: unbounded(
                      22,
                      FontWeight.w600,
                      KassandraColors.of(context).text,
                    ),
                  ),
                  const SizedBox(height: 12),
                  const Text('Период'),
                  const SizedBox(height: 8),
                  _PeriodChips(filters: filters),
                  const SizedBox(height: 12),
                  FilterChip(
                    label: Text('Только обязательные · ${facets.obligatory}'),
                    selected: filters.obligatory,
                    onSelected: (value) =>
                        _patch(ref, obligatory: value, setObligatory: true),
                  ),
                  const SizedBox(height: 8),
                  _DirectionChips(filters: filters),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    children: [
                      _CategoryMenu(filters: filters, facets: facets),
                      _AmountMenu(filters: filters),
                    ],
                  ),
                  Wrap(
                    spacing: 8,
                    children: [
                      for (final source in facets.sources)
                        FilterChip(
                          label: Text(source.label),
                          selected: filters.sources.contains(source.code),
                          onSelected: (value) {
                            final next = {...filters.sources};
                            if (value) {
                              next.add(source.code);
                            } else {
                              next.remove(source.code);
                            }
                            _patch(ref, sources: next);
                          },
                        ),
                    ],
                  ),
                  Row(
                    children: [
                      Checkbox(
                        value: filters.includeCancelled,
                        onChanged: (value) => _patch(
                          ref,
                          includeCancelled: value ?? false,
                          setCancelled: true,
                        ),
                      ),
                      const Text('Показать удалённые'),
                    ],
                  ),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton(
                      onPressed: () =>
                          ref.read(calendarFiltersProvider.notifier).reset(),
                      child: const Text('Сбросить'),
                    ),
                  ),
                ],
              );
            },
          ),
        ),
      );
    } finally {
      ref.read(phoneAddVisibleProvider.notifier).state = true;
    }
  }
}

String _periodChipLabel(CalendarFilters filters) {
  if (filters.presetDays == null) return 'Период…';
  return '${filters.presetDays} дней';
}

class _PeriodChips extends ConsumerWidget {
  const _PeriodChips({required this.filters});
  final CalendarFilters filters;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          for (final days in const [7, 14, 30]) ...[
            ChoiceChip(
              label: Text('$days дней'),
              selected: filters.presetDays == days,
              onSelected: (_) =>
                  _patch(ref, presetDays: days, clearCustom: true),
            ),
            const SizedBox(width: 8),
          ],
          ChoiceChip(
            label: const Text('Период…'),
            selected: filters.presetDays == null,
            onSelected: (_) async {
              final asOf =
                  ref.read(sessionProvider).asOf ?? DateTime.utc(2026, 4, 26);
              final range = await showDateRangePicker(
                context: context,
                firstDate: asOf,
                lastDate: asOf.add(const Duration(days: 370)),
                initialDateRange: DateTimeRange(
                  start: filters.customFrom ?? asOf,
                  end: filters.customTo ?? asOf.add(const Duration(days: 30)),
                ),
              );
              if (range == null) return;
              _patch(
                ref,
                clearPreset: true,
                customFrom: dateOnly(range.start),
                customTo: dateOnly(range.end),
              );
            },
          ),
        ],
      ),
    );
  }
}

class _DirectionChips extends ConsumerWidget {
  const _DirectionChips({required this.filters});
  final CalendarFilters filters;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          ChoiceChip(
            label: const Text('Все'),
            selected: filters.direction == null,
            onSelected: (_) => _patch(ref, setDirection: true),
          ),
          const SizedBox(width: 8),
          ChoiceChip(
            label: const Text('Поступления'),
            selected: filters.direction == 'in',
            onSelected: (_) => _patch(ref, direction: 'in', setDirection: true),
          ),
          const SizedBox(width: 8),
          ChoiceChip(
            label: const Text('Списания'),
            selected: filters.direction == 'out',
            onSelected: (_) =>
                _patch(ref, direction: 'out', setDirection: true),
          ),
        ],
      ),
    );
  }
}

Future<String?> _ask(BuildContext context, String initial) async {
  final controller = TextEditingController(text: initial);
  final text = await showDialog<String>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Поиск'),
      content: TextField(
        controller: controller,
        decoration: const InputDecoration(
          hintText: 'Контрагент, номер счёта, ИНН',
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Отмена'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, controller.text),
          child: const Text('Найти'),
        ),
      ],
    ),
  );
  controller.dispose();
  return text;
}

class _PeriodControl extends ConsumerWidget {
  const _PeriodControl({required this.filters});
  final CalendarFilters filters;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return SegmentedButton<int>(
      segments: const [
        ButtonSegment(value: 7, label: Text('7 дней')),
        ButtonSegment(value: 14, label: Text('14 дней')),
        ButtonSegment(value: 30, label: Text('30 дней')),
        ButtonSegment(value: 0, label: Text('Период…')),
      ],
      selected: {filters.presetDays ?? 0},
      onSelectionChanged: (value) async {
        final picked = value.first;
        if (picked != 0) {
          _patch(ref, presetDays: picked, clearCustom: true);
          return;
        }
        final asOf =
            ref.read(sessionProvider).asOf ?? DateTime.utc(2026, 4, 26);
        final range = await showDateRangePicker(
          context: context,
          firstDate: asOf,
          lastDate: asOf.add(const Duration(days: 370)),
          initialDateRange: DateTimeRange(
            start: filters.customFrom ?? asOf,
            end: filters.customTo ?? asOf.add(const Duration(days: 30)),
          ),
        );
        if (range == null) return;
        _patch(
          ref,
          clearPreset: true,
          customFrom: dateOnly(range.start),
          customTo: dateOnly(range.end),
        );
      },
    );
  }
}

class _DirectionControl extends ConsumerWidget {
  const _DirectionControl({required this.filters});
  final CalendarFilters filters;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final current = filters.direction ?? 'all';
    return SegmentedButton<String>(
      segments: const [
        ButtonSegment(value: 'all', label: Text('Все')),
        ButtonSegment(value: 'in', label: Text('Поступления')),
        ButtonSegment(value: 'out', label: Text('Списания')),
      ],
      selected: {current},
      onSelectionChanged: (value) {
        final next = value.first == 'all' ? null : value.first;
        _patch(ref, direction: next, setDirection: true);
      },
    );
  }
}

class _CategoryMenu extends ConsumerWidget {
  const _CategoryMenu({required this.filters, required this.facets});
  final CalendarFilters filters;
  final CalendarFacets facets;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final label = filters.category == null
        ? 'Категория'
        : facets.categories
                  .where((item) => item.code == filters.category)
                  .map((item) => item.label)
                  .firstOrNull ??
              'Категория';
    return PopupMenuButton<String>(
      onSelected: (value) => _patch(
        ref,
        category: value.isEmpty ? null : value,
        setCategory: true,
      ),
      itemBuilder: (context) => [
        const PopupMenuItem(value: '', child: Text('Все категории')),
        for (final item in facets.categories)
          PopupMenuItem(
            value: item.code,
            child: Text('${item.label} · ${item.count}'),
          ),
      ],
      child: _MenuChip(label),
    );
  }
}

class _AmountMenu extends ConsumerStatefulWidget {
  const _AmountMenu({required this.filters});
  final CalendarFilters filters;

  @override
  ConsumerState<_AmountMenu> createState() => _AmountMenuState();
}

class _AmountMenuState extends ConsumerState<_AmountMenu> {
  final _from = TextEditingController();
  final _to = TextEditingController();

  @override
  void dispose() {
    _from.dispose();
    _to.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final active =
        widget.filters.amountMinMinor != null ||
        widget.filters.amountMaxMinor != null;
    return PopupMenuButton<void>(
      onCanceled: () {},
      itemBuilder: (context) => [
        PopupMenuItem(
          enabled: false,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Сумма, ₽'),
              const SizedBox(height: 8),
              TextField(
                controller: _from,
                decoration: const InputDecoration(
                  labelText: 'от',
                  isDense: true,
                ),
              ),
              TextField(
                controller: _to,
                decoration: const InputDecoration(
                  labelText: 'до',
                  isDense: true,
                ),
              ),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton(
                  onPressed: () {
                    final min = parseRublesToMinor(_from.text);
                    final max = parseRublesToMinor(_to.text);
                    _patch(
                      ref,
                      amountMinMinor: min,
                      amountMaxMinor: max,
                      setAmount: true,
                    );
                    Navigator.pop(context);
                  },
                  child: const Text('Применить'),
                ),
              ),
            ],
          ),
        ),
      ],
      child: _MenuChip(active ? 'Сумма · фильтр' : 'Сумма'),
    );
  }
}

class _MenuChip extends StatelessWidget {
  const _MenuChip(this.label);
  final String label;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: colors.divider),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Text(label, style: golos(13, FontWeight.w500, colors.text)),
      ),
    );
  }
}

void _patch(
  WidgetRef ref, {
  int? presetDays,
  bool clearPreset = false,
  bool clearCustom = false,
  DateTime? customFrom,
  DateTime? customTo,
  String? direction,
  bool setDirection = false,
  bool? obligatory,
  bool setObligatory = false,
  Set<String>? sources,
  String? category,
  bool setCategory = false,
  int? amountMinMinor,
  int? amountMaxMinor,
  bool setAmount = false,
  bool? includeCancelled,
  bool setCancelled = false,
  String? query,
  bool setQuery = false,
}) {
  final current = ref.read(calendarFiltersProvider);
  ref
      .read(calendarFiltersProvider.notifier)
      .replace(
        CalendarFilters(
          presetDays: clearPreset ? null : (presetDays ?? current.presetDays),
          customFrom: clearCustom ? null : (customFrom ?? current.customFrom),
          customTo: clearCustom ? null : (customTo ?? current.customTo),
          direction: setDirection ? direction : current.direction,
          obligatory: setObligatory
              ? (obligatory ?? false)
              : current.obligatory,
          sources: sources ?? current.sources,
          category: setCategory ? category : current.category,
          amountMinMinor: setAmount ? amountMinMinor : current.amountMinMinor,
          amountMaxMinor: setAmount ? amountMaxMinor : current.amountMaxMinor,
          includeCancelled: setCancelled
              ? (includeCancelled ?? false)
              : current.includeCancelled,
          query: setQuery ? (query ?? '') : current.query,
        ),
      );
}

class _DesktopTotals extends StatelessWidget {
  const _DesktopTotals({required this.view, required this.forecast});
  final CalendarView view;
  final ForecastResult? forecast;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final residual = forecast == null ? null : residualSums(forecast!.points);
    final inflowCount = activePayments(view.items, 'in');
    final outflowCount = activePayments(view.items, 'out');
    return Row(
      children: [
        Expanded(
          child: _TotalCard(
            title: 'Поступления · ${paymentsPhrase(inflowCount)}',
            value: formatPayment(view.totals.inflowMinor),
            valueColor: colors.positive,
            valueKey: const Key('calendar-in'),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _TotalCard(
            title: 'Списания · ${paymentsPhrase(outflowCount)}',
            value: formatPayment(-view.totals.outflowMinor),
            valueColor: colors.text,
            valueKey: const Key('calendar-out'),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _TotalCard(
            title: 'Кроме этих платежей модель ждёт',
            value: residual == null
                ? '—'
                : '+${formatMoneyShort(residual.inflow)} и ${formatMoneyShort(-residual.outflow)}',
            caption: 'по среднему потоку, без привязки к дням',
          ),
        ),
      ],
    );
  }
}

class _PhoneTotals extends StatelessWidget {
  const _PhoneTotals({required this.totals});
  final FlowTotals totals;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.surface,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        child: Row(
          children: [
            Expanded(
              child: Text(
                '${formatPayment(totals.inflowMinor)} · ${formatPayment(-totals.outflowMinor)}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: golos(14, FontWeight.w600, colors.text),
              ),
            ),
            const SizedBox(width: 8),
            Text(
              paymentsPhrase(totals.count),
              key: const Key('calendar-count'),
              style: golos(13, FontWeight.w400, colors.textMuted),
            ),
          ],
        ),
      ),
    );
  }
}

class _TotalCard extends StatelessWidget {
  const _TotalCard({
    required this.title,
    required this.value,
    this.caption,
    this.valueColor,
    this.valueKey,
  });

  final String title;
  final String value;
  final String? caption;
  final Color? valueColor;
  final Key? valueKey;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.surface,
        borderRadius: BorderRadius.circular(18),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: golos(13, FontWeight.w400, colors.textMuted)),
            const SizedBox(height: 8),
            Text(
              value,
              key: valueKey,
              style: golos(20, FontWeight.w700, valueColor ?? colors.text),
            ),
            if (caption != null) ...[
              const SizedBox(height: 4),
              Text(
                caption!,
                style: golos(12, FontWeight.w400, colors.textMuted),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _DayCard extends StatelessWidget {
  const _DayCard({
    required this.date,
    required this.balance,
    required this.gap,
    required this.phone,
    required this.children,
  });

  final DateTime date;
  final int? balance;
  final DateTime? gap;
  final bool phone;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final broken = gap != null && sameDate(gap!, date);
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: colors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: colors.divider),
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(16),
          child: IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (broken) Container(width: 4, color: colors.danger),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      _DayHeader(
                        date: date,
                        balance: balance,
                        gap: gap,
                        phone: phone,
                      ),
                      for (var i = 0; i < children.length; i++) ...[
                        if (i > 0) Divider(height: 1, color: colors.divider),
                        children[i],
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _DayHeader extends StatelessWidget {
  const _DayHeader({
    required this.date,
    required this.balance,
    required this.gap,
    required this.phone,
  });
  final DateTime date;
  final int? balance;
  final DateTime? gap;
  final bool phone;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final broken = gap != null && sameDate(gap!, date);
    final negative = balance != null && balance! < 0;
    final label = formatWeekdayDate(date) + (broken ? ' · разрыв' : '');
    final evening = balance == null
        ? null
        : '${phone ? 'вечером' : 'вечером на счёте'} ${formatMoneyTrunc(balance!)}';
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 14, 4, 6),
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: golos(
                14,
                FontWeight.w600,
                broken ? colors.danger : colors.text,
              ),
            ),
          ),
          if (evening != null)
            Text(
              evening,
              style: golos(
                13,
                FontWeight.w500,
                negative ? colors.danger : colors.textMuted,
              ),
            ),
        ],
      ),
    );
  }
}

class _PaymentRow extends StatelessWidget {
  const _PaymentRow({
    required this.item,
    required this.selected,
    required this.phone,
    this.embedded = false,
    required this.rhythm,
    required this.risky,
    required this.onOpen,
    required this.onEdit,
    required this.onDelete,
    required this.onSkip,
  });

  final CalendarItem item;
  final bool selected;
  final bool phone;
  final bool embedded;
  final String? rhythm;
  final bool risky;
  final VoidCallback onOpen;
  final VoidCallback onEdit;
  final VoidCallback onDelete;
  final VoidCallback onSkip;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final muted = item.cancelled || item.assumption;
    final row = Material(
      color: selected ? colors.primaryContainer : colors.surface,
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: item.cancelled ? null : onOpen,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          child: Row(
            children: [
              _DirectionIcon(item: item),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      item.shortTitle,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: golos(
                        15,
                        FontWeight.w600,
                        item.cancelled ? colors.textMuted : colors.text,
                      ),
                    ),
                    if (item.metaLine.isNotEmpty) ...[
                      const SizedBox(height: 2),
                      Text(
                        item.metaLine,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: golos(13, FontWeight.w400, colors.textMuted),
                      ),
                    ],
                    if (rhythm != null)
                      Text(
                        rhythm!,
                        style: golos(12, FontWeight.w400, colors.textMuted),
                      ),
                    const SizedBox(height: 4),
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: [
                        if (item.sourceShort.isNotEmpty)
                          _Badge(item.sourceShort, dashed: item.assumption),
                        if (item.showsCertainty && !phone)
                          Text(
                            item.certaintyLabel!.toLowerCase(),
                            style: golos(12, FontWeight.w400, colors.textMuted),
                          ),
                        if (item.obligatory)
                          _Badge(
                            'Обязательный',
                            color: colors.warningContainer,
                            textColor: colors.onWarningContainer,
                          ),
                        if (risky)
                          _Badge(
                            'Контрагент в реестре с риском',
                            color: const Color(0xFFF8E4E2),
                            textColor: colors.danger,
                          ),
                        if (item.cancelled) const _Badge('Удалён'),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Text(
                '${item.assumption ? '≈ ' : ''}${formatPayment(item.signedMinor)}',
                style:
                    golos(
                      15,
                      FontWeight.w700,
                      item.cancelled
                          ? colors.textMuted
                          : item.direction == 'in'
                          ? colors.positive
                          : muted
                          ? colors.textMuted
                          : colors.text,
                    ).copyWith(
                      decoration: item.cancelled
                          ? TextDecoration.lineThrough
                          : null,
                    ),
              ),
              if (!phone && !item.cancelled) ...[
                const SizedBox(width: 4),
                if (item.hasAction('edit'))
                  IconButton(
                    tooltip: 'Изменить',
                    onPressed: onEdit,
                    icon: KIcon(
                      KIcon.pencil,
                      size: 18,
                      color: colors.textMuted,
                    ),
                  ),
                if (item.hasAction('delete'))
                  IconButton(
                    tooltip: 'Удалить',
                    onPressed: onDelete,
                    icon: KIcon(KIcon.trash, size: 18, color: colors.textMuted),
                  ),
                if (item.hasAction('skip'))
                  IconButton(
                    tooltip: 'Не учитывать этот платёж',
                    onPressed: onSkip,
                    icon: KIcon(
                      KIcon.eyeOff,
                      size: 18,
                      color: colors.textMuted,
                    ),
                  ),
              ],
            ],
          ),
        ),
      ),
    );
    final body = phone && !item.cancelled
        ? _SwipeRow(
            item: item,
            onEdit: onEdit,
            onDelete: onDelete,
            onSkip: onSkip,
            child: row,
          )
        : row;
    if (embedded) return body;
    return Padding(padding: const EdgeInsets.only(bottom: 8), child: body);
  }
}

class _SwipeRow extends StatefulWidget {
  const _SwipeRow({
    required this.item,
    required this.child,
    required this.onEdit,
    required this.onDelete,
    required this.onSkip,
  });

  final CalendarItem item;
  final Widget child;
  final VoidCallback onEdit;
  final VoidCallback onDelete;
  final VoidCallback onSkip;

  @override
  State<_SwipeRow> createState() => _SwipeRowState();
}

class _SwipeRowState extends State<_SwipeRow> {
  double _offset = 0;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final second = widget.item.hasAction('skip');
    return ClipRRect(
      borderRadius: BorderRadius.circular(16),
      child: Stack(
        children: [
          Positioned.fill(
            child: Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                _SwipeButton(
                  label: 'Изменить',
                  color: colors.primary,
                  icon: KIcon.pencil,
                  onTap: widget.onEdit,
                ),
                _SwipeButton(
                  label: second ? 'Не учитывать' : 'Удалить',
                  color: colors.danger,
                  icon: second ? KIcon.eyeOff : KIcon.trash,
                  onTap: second ? widget.onSkip : widget.onDelete,
                ),
              ],
            ),
          ),
          GestureDetector(
            onHorizontalDragUpdate: (details) {
              setState(() {
                _offset = (_offset + details.delta.dx).clamp(-168.0, 0.0);
              });
            },
            onHorizontalDragEnd: (_) {
              setState(() => _offset = _offset < -60 ? -168 : 0);
            },
            child: Transform.translate(
              offset: Offset(_offset, 0),
              child: widget.child,
            ),
          ),
        ],
      ),
    );
  }
}

class _SwipeButton extends StatelessWidget {
  const _SwipeButton({
    required this.label,
    required this.color,
    required this.icon,
    required this.onTap,
  });

  final String label;
  final Color color;
  final String icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: color,
      child: InkWell(
        onTap: onTap,
        child: SizedBox(
          width: 84,
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              KIcon(icon, size: 18, color: Colors.white),
              const SizedBox(height: 4),
              Text(label, style: golos(11, FontWeight.w600, Colors.white)),
            ],
          ),
        ),
      ),
    );
  }
}

class _DirectionIcon extends StatelessWidget {
  const _DirectionIcon({required this.item});
  final CalendarItem item;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final inflow = item.direction == 'in';
    final fill = inflow ? colors.positiveContainer : colors.surfaceMuted;
    final ink = inflow ? colors.positive : colors.text;
    final icon = Container(
      width: 40,
      height: 40,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: fill,
        borderRadius: BorderRadius.circular(12),
      ),
      child: KIcon(
        inflow ? KIcon.arrowDownLeft : KIcon.arrowUpRight,
        size: 18,
        color: ink,
      ),
    );
    if (!item.assumption) return icon;
    return CustomPaint(painter: _DashPainter(colors.textMuted), child: icon);
  }
}

class _DashPainter extends CustomPainter {
  _DashPainter(this.color, {this.radius = 12});
  final Color color;
  final double radius;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2;
    final path = Path()
      ..addRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(1, 1, size.width - 2, size.height - 2),
          Radius.circular(radius),
        ),
      );
    for (final metric in path.computeMetrics()) {
      var start = 0.0;
      while (start < metric.length) {
        final end = (start + 4).clamp(0.0, metric.length).toDouble();
        canvas.drawPath(metric.extractPath(start, end), paint);
        start += 7;
      }
    }
  }

  @override
  bool shouldRepaint(covariant _DashPainter oldDelegate) =>
      oldDelegate.color != color || oldDelegate.radius != radius;
}

class _Badge extends StatelessWidget {
  const _Badge(this.text, {this.dashed = false, this.color, this.textColor});
  final String text;
  final bool dashed;
  final Color? color;
  final Color? textColor;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final child = DecoratedBox(
      decoration: BoxDecoration(
        color: color ?? colors.surfaceMuted,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        child: Text(
          text,
          style: golos(11, FontWeight.w600, textColor ?? colors.textMuted),
        ),
      ),
    );
    if (!dashed) return child;
    return CustomPaint(
      painter: _DashPainter(colors.textMuted, radius: 999),
      child: child,
    );
  }
}

class _TaxFooter extends ConsumerWidget {
  const _TaxFooter();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final events = ref.watch(taxHintProvider).valueOrNull?['events'];
    return Builder(
      builder: (context) {
        if (events is! List || events.isEmpty) return const SizedBox.shrink();
        final first = events.first;
        if (first is! Map) return const SizedBox.shrink();
        final date = asDateOpt(first['expected_date'] ?? first['date']);
        final when = date == null ? '' : formatDayMonth(date);
        return Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Wrap(
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(
                'ЕНП $when сейчас внутри среднего потока модели. ',
                style: golos(13, FontWeight.w400, colors.textMuted),
              ),
              TextButton(
                onPressed: () async {
                  try {
                    final session = ref.read(sessionProvider);
                    final runId = ref
                        .read(forecastBundleProvider)
                        .valueOrNull
                        ?.forecast
                        .runId;
                    if (session.accountId == null) return;
                    final result = await ref
                        .read(apiProvider)
                        .applyTaxCalendar(session.accountId!, runId);
                    ref.read(sessionProvider.notifier).refresh();
                    ref.read(noticeProvider.notifier).state = result.summary;
                  } on ApiException catch (error) {
                    ref.read(noticeProvider.notifier).state = error.message;
                  }
                },
                child: const Text('Показать его отдельным платежом'),
              ),
            ],
          ),
        );
      },
    );
  }
}
