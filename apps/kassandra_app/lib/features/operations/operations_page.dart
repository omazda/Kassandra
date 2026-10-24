import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../api/errors.dart';
import '../../api/models.dart';
import '../../api/v11.dart';
import '../../api/views.dart';
import '../../app/providers.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';
import 'structure.dart';

class OperationFilters {
  const OperationFilters({
    this.preset = 'quarter',
    this.customFrom,
    this.customTo,
    this.direction,
    this.categories = const [],
    this.counterpartyInn,
    this.counterpartyName,
    this.amountMinMinor,
    this.amountMaxMinor,
    this.query = '',
    this.sort = 'date_desc',
    this.grouped = false,
    this.ringDirection = 'out',
    this.structure = true,
  });

  final String preset;
  final DateTime? customFrom;
  final DateTime? customTo;
  final String? direction;
  final List<String> categories;
  final String? counterpartyInn;
  final String? counterpartyName;
  final int? amountMinMinor;
  final int? amountMaxMinor;
  final String query;
  final String sort;
  final bool grouped;
  final String ringDirection;
  final bool structure;

  int get activeCount {
    var count = 0;
    if (preset != 'quarter') count++;
    if (direction != null) count++;
    if (categories.isNotEmpty) count++;
    if (counterpartyInn != null) count++;
    if (amountMinMinor != null || amountMaxMinor != null) count++;
    if (query.trim().isNotEmpty) count++;
    if (sort != 'date_desc') count++;
    return count;
  }

  DateTime _from(DateTime asOf) {
    switch (preset) {
      case 'month':
        return asOf.subtract(const Duration(days: 29));
      case 'year':
        return asOf.subtract(const Duration(days: 364));
      case 'custom':
        return customFrom ?? asOf.subtract(const Duration(days: 89));
      default:
        return asOf.subtract(const Duration(days: 89));
    }
  }

  DateTime _to(DateTime asOf) {
    if (preset == 'custom' && customTo != null && customTo!.isBefore(asOf)) {
      return customTo!;
    }
    return asOf;
  }

  TransactionQuery listQuery(DateTime asOf, {int page = 1}) => TransactionQuery(
    dateFrom: _from(asOf),
    dateTo: _to(asOf),
    direction: direction,
    categories: categories,
    counterpartyInn: counterpartyInn,
    query: query,
    amountMin: amountMinMinor,
    amountMax: amountMaxMinor,
    sort: sort,
    page: page,
  );

  /// Structure ignores category, sort and page: the ring and the list share
  /// every other filter.
  TransactionQuery summaryQuery(DateTime asOf) => TransactionQuery(
    dateFrom: _from(asOf),
    dateTo: _to(asOf),
    direction: direction,
    counterpartyInn: counterpartyInn,
    query: query,
    amountMin: amountMinMinor,
    amountMax: amountMaxMinor,
  );

  String get periodLabel {
    switch (preset) {
      case 'month':
        return 'за месяц';
      case 'year':
        return 'за год';
      case 'custom':
        return 'за период';
      default:
        return 'за 3 месяца';
    }
  }
}

class OperationFiltersNotifier extends Notifier<OperationFilters> {
  @override
  OperationFilters build() => const OperationFilters();

  void replace(OperationFilters next) => state = next;

  void reset() => state = const OperationFilters();
}

final operationFiltersProvider =
    NotifierProvider<OperationFiltersNotifier, OperationFilters>(
      OperationFiltersNotifier.new,
    );

final categorySummaryProvider = FutureProvider<CategorySummary>((ref) async {
  final session = ref.watch(sessionProvider);
  final filters = ref.watch(operationFiltersProvider);
  if (session.accountId == null || session.asOf == null) {
    throw ApiFailure('Выберите счёт и дату расчёта');
  }
  return ref
      .watch(apiProvider)
      .categorySummary(session.accountId!, filters.summaryQuery(session.asOf!));
});

/// First pages of the operations list for the current filters. Kept outside the page so
/// coming back to the tab (or the prefetch after the forecast) shows the list at once.
class OperationsSnapshot {
  const OperationsSnapshot({
    required this.signature,
    required this.items,
    required this.page,
    required this.total,
    required this.totals,
    required this.categories,
    required this.parties,
    required this.more,
  });

  final String signature;
  final List<TransactionItem> items;
  final int page;
  final int total;
  final FlowTotals totals;
  final List<FacetCount> categories;
  final List<CounterpartyFacet> parties;
  final bool more;
}

final operationsCacheProvider = StateProvider<OperationsSnapshot?>(
  (ref) => null,
);

String operationsSignature(SessionState session, OperationFilters filters) {
  final asOf = session.asOf == null ? '' : isoDate(session.asOf!);
  return '${session.accountId}|$asOf|${session.generation}|${filters.preset}|${filters.direction}|${filters.categories.join(",")}|${filters.counterpartyInn}|${filters.query}|${filters.sort}|${filters.amountMinMinor}|${filters.amountMaxMinor}|${filters.customFrom}|${filters.customTo}';
}

/// Loads the first page into the cache unless it already holds these filters.
Future<void> prefetchOperations(WidgetRef ref) async {
  final session = ref.read(sessionProvider);
  final filters = ref.read(operationFiltersProvider);
  if (session.accountId == null || session.asOf == null) return;
  final signature = operationsSignature(session, filters);
  if (ref.read(operationsCacheProvider)?.signature == signature) return;
  final result = await ref
      .read(apiProvider)
      .transactions(
        session.accountId!,
        filters.listQuery(session.asOf!, page: 1),
      );
  final total = result.totals.count == 0 ? result.total : result.totals.count;
  ref.read(operationsCacheProvider.notifier).state = OperationsSnapshot(
    signature: signature,
    items: result.items,
    page: 1,
    total: total,
    totals: result.totals,
    categories: result.categories,
    parties: result.counterparties,
    more: result.items.length < total && result.items.isNotEmpty,
  );
}

class OperationsPage extends ConsumerStatefulWidget {
  const OperationsPage({super.key});

  @override
  ConsumerState<OperationsPage> createState() => _OperationsPageState();
}

class _OperationsPageState extends ConsumerState<OperationsPage> {
  final _search = TextEditingController();
  Timer? _searchTimer;
  var _items = <TransactionItem>[];
  var _page = 1;
  var _total = 0;
  FlowTotals _totals = const FlowTotals(
    count: 0,
    inflowMinor: 0,
    outflowMinor: 0,
  );
  var _facetsIn = <FacetCount>[];
  var _parties = <CounterpartyFacet>[];
  var _loading = true;
  var _more = false;
  Object? _error;
  String? _signature;

  @override
  void initState() {
    super.initState();
    final cached = ref.read(operationsCacheProvider);
    if (cached != null) _restore(cached);
  }

  void _restore(OperationsSnapshot cached) {
    _signature = cached.signature;
    _items = cached.items;
    _page = cached.page;
    _total = cached.total;
    _totals = cached.totals;
    _facetsIn = cached.categories;
    _parties = cached.parties;
    _more = cached.more;
    _loading = false;
  }

  @override
  void dispose() {
    _searchTimer?.cancel();
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider);
    final filters = ref.watch(operationFiltersProvider);
    final phone = isPhone(context);
    final signature = operationsSignature(session, filters);
    if (_signature != signature &&
        session.accountId != null &&
        session.asOf != null) {
      _signature = signature;
      WidgetsBinding.instance.addPostFrameCallback((_) => _load(reset: true));
    }
    final summary = ref.watch(categorySummaryProvider);
    return ListView(
      key: const Key('page-scroll'),
      padding: phone
          ? const EdgeInsets.fromLTRB(16, 12, 16, 120)
          : const EdgeInsets.fromLTRB(32, 28, 32, 40),
      children: [
        _Header(phone: phone, asOf: session.asOf),
        const SizedBox(height: 12),
        if (phone)
          _PhoneBar(filters: filters, search: _search, onQuery: _onQuery)
        else
          _DesktopBar(
            filters: filters,
            search: _search,
            onQuery: _onQuery,
            categories: _facetsIn,
            parties: _parties,
          ),
        const SizedBox(height: 12),
        if (filters.categories.isNotEmpty)
          Wrap(
            spacing: 8,
            children: [
              InputChip(
                label: Text('Категория: ${_categoryChip(filters)}'),
                onDeleted: () => _setCategories(const []),
              ),
            ],
          ),
        if (phone)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: SegmentedButton<bool>(
              segments: const [
                ButtonSegment(value: false, label: Text('Список')),
                ButtonSegment(value: true, label: Text('Структура')),
              ],
              selected: {filters.structure},
              onSelectionChanged: (value) =>
                  _patch(structure: value.first, setStructure: true),
            ),
          ),
        if (!phone || filters.structure)
          summary.when(
            skipLoadingOnReload: true,
            loading: () => const Padding(
              padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator()),
            ),
            error: (error, _) => ErrorPane(
              error: error,
              onRetry: () => ref.invalidate(categorySummaryProvider),
            ),
            data: (data) => _StructureCard(
              summary: data,
              filters: filters,
              onSlice: (slice) {
                _setCategories(slice.categories);
                if (phone) _patch(structure: false, setStructure: true);
              },
            ),
          ),
        if (!phone || !filters.structure) ...[
          const SizedBox(height: 12),
          if (_error != null)
            ErrorPane(error: _error!, onRetry: () => _load(reset: true))
          else if (_loading && _items.isEmpty)
            const Padding(
              padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator()),
            )
          else
            _Table(
              items: _items,
              total: _total,
              totals: _totals,
              direction: filters.direction,
              phone: phone,
              onCategory: _changeCategory,
            ),
          if (_more)
            Align(
              alignment: Alignment.centerRight,
              child: OutlinedButton(
                key: const Key('ops-more'),
                onPressed: _loading ? null : () => _load(reset: false),
                child: const Text('Показать ещё'),
              ),
            ),
        ],
      ],
    );
  }

  String _categoryChip(OperationFilters filters) {
    if (filters.categories.length != 1) return 'Остальное';
    final code = filters.categories.first;
    for (final facet in _facetsIn) {
      if (facet.code == code) return facet.label;
    }
    return categoryLabel(code);
  }

  void _onQuery(String value) {
    _searchTimer?.cancel();
    _searchTimer = Timer(const Duration(milliseconds: 300), () {
      _patch(query: value, setQuery: true);
    });
  }

  void _setCategories(List<String> categories) {
    _patch(categories: categories, setCategories: true);
  }

  void _patch({
    String? preset,
    bool clearCustom = false,
    DateTime? customFrom,
    DateTime? customTo,
    String? direction,
    bool setDirection = false,
    List<String>? categories,
    bool setCategories = false,
    String? counterpartyInn,
    String? counterpartyName,
    bool setParty = false,
    int? amountMinMinor,
    int? amountMaxMinor,
    bool setAmount = false,
    String? query,
    bool setQuery = false,
    String? sort,
    bool? grouped,
    String? ringDirection,
    bool? structure,
    bool setStructure = false,
  }) {
    final current = ref.read(operationFiltersProvider);
    ref
        .read(operationFiltersProvider.notifier)
        .replace(
          OperationFilters(
            preset: preset ?? current.preset,
            customFrom: clearCustom ? null : (customFrom ?? current.customFrom),
            customTo: clearCustom ? null : (customTo ?? current.customTo),
            direction: setDirection ? direction : current.direction,
            categories: setCategories
                ? (categories ?? const [])
                : current.categories,
            counterpartyInn: setParty
                ? counterpartyInn
                : current.counterpartyInn,
            counterpartyName: setParty
                ? counterpartyName
                : current.counterpartyName,
            amountMinMinor: setAmount ? amountMinMinor : current.amountMinMinor,
            amountMaxMinor: setAmount ? amountMaxMinor : current.amountMaxMinor,
            query: setQuery ? (query ?? '') : current.query,
            sort: sort ?? current.sort,
            grouped: grouped ?? current.grouped,
            ringDirection: ringDirection ?? current.ringDirection,
            structure: setStructure ? (structure ?? false) : current.structure,
          ),
        );
  }

  Future<void> _load({required bool reset}) async {
    final session = ref.read(sessionProvider);
    final filters = ref.read(operationFiltersProvider);
    if (session.accountId == null || session.asOf == null) return;
    final page = reset ? 1 : _page + 1;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await ref
          .read(apiProvider)
          .transactions(
            session.accountId!,
            filters.listQuery(session.asOf!, page: page),
          );
      if (!mounted) return;
      setState(() {
        _page = page;
        _totals = result.totals;
        _total = result.totals.count == 0 ? result.total : result.totals.count;
        _facetsIn = result.categories;
        _parties = result.counterparties;
        _items = reset ? result.items : [..._items, ...result.items];
        _more = _items.length < _total && result.items.isNotEmpty;
        _loading = false;
      });
      ref.read(operationsCacheProvider.notifier).state = OperationsSnapshot(
        signature: operationsSignature(session, filters),
        items: _items,
        page: _page,
        total: _total,
        totals: _totals,
        categories: _facetsIn,
        parties: _parties,
        more: _more,
      );
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  Future<void> _changeCategory(TransactionItem item, String category) async {
    final session = ref.read(sessionProvider);
    if (session.asOf == null || session.accountId == null) return;
    try {
      await ref
          .read(apiProvider)
          .updateCategory(
            session.accountId!,
            item.operationId,
            category,
            session.asOf!,
          );
      ref.invalidate(categorySummaryProvider);
      await _load(reset: true);
      ref.read(sessionProvider.notifier).setAsOf(session.asOf!);
    } on ApiException catch (error) {
      ref.read(noticeProvider.notifier).state = error.message;
    }
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.phone, required this.asOf});
  final bool phone;
  final DateTime? asOf;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final when = asOf == null ? '' : formatDateFull(asOf!);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Операции',
                style: unbounded(phone ? 28 : 30, FontWeight.w600, colors.text),
              ),
              const SizedBox(height: 4),
              Text(
                'Выписка по счёту до $when · операции после даты расчёта скрыты',
                style: golos(14, FontWeight.w400, colors.textMuted),
              ),
            ],
          ),
        ),
        if (!phone)
          OutlinedButton(
            onPressed: () => context.go('/sources'),
            child: const Text('Загрузить выписку'),
          ),
      ],
    );
  }
}

class _DesktopBar extends ConsumerWidget {
  const _DesktopBar({
    required this.filters,
    required this.search,
    required this.onQuery,
    required this.categories,
    required this.parties,
  });

  final OperationFilters filters;
  final TextEditingController search;
  final ValueChanged<String> onQuery;
  final List<FacetCount> categories;
  final List<CounterpartyFacet> parties;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final page = context.findAncestorStateOfType<_OperationsPageState>();
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
                _Period(filters: filters),
                if (filters.preset != 'custom' &&
                    ref.watch(sessionProvider).asOf != null)
                  _RangeLabel(
                    filters: filters,
                    asOf: ref.watch(sessionProvider).asOf!,
                  ),
                _Flow(filters: filters),
              ],
            ),
            const SizedBox(height: 8),
            TextField(
              controller: search,
              onChanged: onQuery,
              decoration: const InputDecoration(
                isDense: true,
                hintText: 'Контрагент, ИНН, назначение',
                prefixIcon: Icon(Icons.search, size: 18),
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                PopupMenuButton<String>(
                  onSelected: (value) {
                    page?._setCategories(value.isEmpty ? const [] : [value]);
                  },
                  itemBuilder: (context) => [
                    const PopupMenuItem(
                      value: '',
                      child: Text('Все категории'),
                    ),
                    for (final item in categories)
                      PopupMenuItem(
                        value: item.code,
                        child: Text('${item.label} · ${item.count}'),
                      ),
                  ],
                  child: _ChipLabel(
                    filters.categories.isEmpty
                        ? 'Категория'
                        : 'Категория · ${filters.categories.length}',
                  ),
                ),
                PopupMenuButton<String>(
                  onSelected: (value) {
                    if (value.isEmpty) {
                      page?._patch(setParty: true);
                      return;
                    }
                    final party = parties
                        .where((item) => item.inn == value)
                        .firstOrNull;
                    page?._patch(
                      setParty: true,
                      counterpartyInn: value,
                      counterpartyName: party?.name,
                    );
                  },
                  itemBuilder: (context) => [
                    const PopupMenuItem(
                      value: '',
                      child: Text('Все контрагенты'),
                    ),
                    for (final item in parties)
                      PopupMenuItem(
                        value: item.inn,
                        child: Text('${item.name} · ${item.count}'),
                      ),
                  ],
                  child: _ChipLabel(filters.counterpartyName ?? 'Контрагент'),
                ),
                _OpsAmount(filters: filters),
                PopupMenuButton<String>(
                  onSelected: (value) => page?._patch(sort: value),
                  itemBuilder: (context) => const [
                    PopupMenuItem(
                      value: 'date_desc',
                      child: Text('Сначала новые'),
                    ),
                    PopupMenuItem(
                      value: 'date_asc',
                      child: Text('Сначала старые'),
                    ),
                    PopupMenuItem(
                      value: 'amount_desc',
                      child: Text('Сначала крупные'),
                    ),
                    PopupMenuItem(
                      value: 'amount_asc',
                      child: Text('Сначала мелкие'),
                    ),
                  ],
                  child: _ChipLabel(_sortLabel(filters.sort)),
                ),
                TextButton(
                  onPressed: () {
                    search.clear();
                    ref.read(operationFiltersProvider.notifier).reset();
                  },
                  child: const Text('Сбросить фильтры'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _PhoneBar extends ConsumerWidget {
  const _PhoneBar({
    required this.filters,
    required this.search,
    required this.onQuery,
  });

  final OperationFilters filters;
  final TextEditingController search;
  final ValueChanged<String> onQuery;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            children: [
              _PeriodMenu(filters: filters),
              const SizedBox(width: 8),
              FilterChip(
                label: const Text('Списания'),
                selected: filters.direction == 'out',
                onSelected: (value) => context
                    .findAncestorStateOfType<_OperationsPageState>()
                    ?._patch(
                      direction: value ? 'out' : null,
                      setDirection: true,
                    ),
              ),
              const SizedBox(width: 8),
              FilterChip(
                label: const Text('Поступления'),
                selected: filters.direction == 'in',
                onSelected: (value) => context
                    .findAncestorStateOfType<_OperationsPageState>()
                    ?._patch(
                      direction: value ? 'in' : null,
                      setDirection: true,
                    ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: search,
          onChanged: onQuery,
          decoration: const InputDecoration(
            isDense: true,
            hintText: 'Контрагент, ИНН, назначение',
            prefixIcon: Icon(Icons.search),
            border: OutlineInputBorder(),
          ),
        ),
      ],
    );
  }
}

String _sortLabel(String sort) {
  switch (sort) {
    case 'date_asc':
      return 'Сначала старые';
    case 'amount_desc':
      return 'Сначала крупные';
    case 'amount_asc':
      return 'Сначала мелкие';
    default:
      return 'Сначала новые';
  }
}

class _ChipLabel extends StatelessWidget {
  const _ChipLabel(this.text);
  final String text;

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
        child: Text(text, style: golos(13, FontWeight.w500, colors.text)),
      ),
    );
  }
}

class _PeriodMenu extends ConsumerWidget {
  const _PeriodMenu({required this.filters});
  final OperationFilters filters;

  String get _label {
    switch (filters.preset) {
      case 'month':
        return 'Месяц';
      case 'year':
        return 'Год';
      case 'custom':
        return 'Период…';
      default:
        return '3 месяца';
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final page = context.findAncestorStateOfType<_OperationsPageState>();
    final colors = KassandraColors.of(context);
    return PopupMenuButton<String>(
      onSelected: (value) async {
        if (value != 'custom') {
          page?._patch(preset: value, clearCustom: true);
          return;
        }
        final asOf =
            ref.read(sessionProvider).asOf ?? DateTime.utc(2026, 4, 26);
        final range = await showDateRangePicker(
          context: context,
          firstDate: DateTime.utc(2020),
          lastDate: asOf,
          initialDateRange: DateTimeRange(
            start:
                filters.customFrom ?? asOf.subtract(const Duration(days: 89)),
            end: asOf,
          ),
        );
        if (range == null) return;
        page?._patch(
          preset: 'custom',
          customFrom: dateOnly(range.start),
          customTo: dateOnly(range.end),
        );
      },
      itemBuilder: (context) => const [
        PopupMenuItem(value: 'month', child: Text('Месяц')),
        PopupMenuItem(value: 'quarter', child: Text('3 месяца')),
        PopupMenuItem(value: 'year', child: Text('Год')),
        PopupMenuItem(value: 'custom', child: Text('Период…')),
      ],
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: colors.primaryContainer,
          borderRadius: BorderRadius.circular(999),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(_label, style: golos(14, FontWeight.w600, colors.text)),
              Icon(Icons.expand_more, size: 18, color: colors.text),
            ],
          ),
        ),
      ),
    );
  }
}

class _Period extends ConsumerWidget {
  const _Period({required this.filters});
  final OperationFilters filters;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final page = context.findAncestorStateOfType<_OperationsPageState>();
    return SegmentedButton<String>(
      segments: const [
        ButtonSegment(value: 'month', label: Text('Месяц')),
        ButtonSegment(value: 'quarter', label: Text('3 месяца')),
        ButtonSegment(value: 'year', label: Text('Год')),
        ButtonSegment(value: 'custom', label: Text('Период…')),
      ],
      selected: {filters.preset},
      onSelectionChanged: (value) async {
        final preset = value.first;
        if (preset != 'custom') {
          page?._patch(preset: preset, clearCustom: true);
          return;
        }
        final asOf =
            ref.read(sessionProvider).asOf ?? DateTime.utc(2026, 4, 26);
        final range = await showDateRangePicker(
          context: context,
          firstDate: DateTime.utc(2020),
          lastDate: asOf,
          initialDateRange: DateTimeRange(
            start:
                filters.customFrom ?? asOf.subtract(const Duration(days: 89)),
            end: asOf,
          ),
        );
        if (range == null) return;
        page?._patch(
          preset: 'custom',
          customFrom: dateOnly(range.start),
          customTo: dateOnly(range.end),
        );
      },
    );
  }
}

class _RangeLabel extends StatelessWidget {
  const _RangeLabel({required this.filters, required this.asOf});
  final OperationFilters filters;
  final DateTime asOf;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final query = filters.listQuery(asOf);
    final from = query.dateFrom!;
    final to = query.dateTo!;
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: colors.divider),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Text(
          '${formatDateDot(from)} — ${formatDateDot(to)}',
          style: golos(13, FontWeight.w500, colors.text),
        ),
      ),
    );
  }
}

class _Flow extends StatelessWidget {
  const _Flow({required this.filters});
  final OperationFilters filters;

  @override
  Widget build(BuildContext context) {
    final page = context.findAncestorStateOfType<_OperationsPageState>();
    return SegmentedButton<String>(
      segments: const [
        ButtonSegment(value: 'all', label: Text('Все')),
        ButtonSegment(value: 'in', label: Text('Поступления')),
        ButtonSegment(value: 'out', label: Text('Списания')),
      ],
      selected: {filters.direction ?? 'all'},
      onSelectionChanged: (value) {
        final next = value.first == 'all' ? null : value.first;
        page?._patch(direction: next, setDirection: true);
      },
    );
  }
}

class _OpsAmount extends ConsumerStatefulWidget {
  const _OpsAmount({required this.filters});
  final OperationFilters filters;

  @override
  ConsumerState<_OpsAmount> createState() => _OpsAmountState();
}

class _OpsAmountState extends ConsumerState<_OpsAmount> {
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
    final page = context.findAncestorStateOfType<_OperationsPageState>();
    return PopupMenuButton<void>(
      itemBuilder: (context) => [
        PopupMenuItem(
          enabled: false,
          child: Column(
            children: [
              TextField(
                controller: _from,
                decoration: const InputDecoration(
                  labelText: 'от, ₽',
                  isDense: true,
                ),
              ),
              TextField(
                controller: _to,
                decoration: const InputDecoration(
                  labelText: 'до, ₽',
                  isDense: true,
                ),
              ),
              TextButton(
                onPressed: () {
                  page?._patch(
                    setAmount: true,
                    amountMinMinor: parseRublesToMinor(_from.text),
                    amountMaxMinor: parseRublesToMinor(_to.text),
                  );
                  Navigator.pop(context);
                },
                child: const Text('Применить'),
              ),
            ],
          ),
        ),
      ],
      child: const _ChipLabel('Сумма'),
    );
  }
}

class _StructureCard extends ConsumerWidget {
  const _StructureCard({
    required this.summary,
    required this.filters,
    required this.onSlice,
  });

  final CategorySummary summary;
  final OperationFilters filters;
  final ValueChanged<DonutSlice> onSlice;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final phone = isPhone(context);
    final direction = filters.direction ?? filters.ringDirection;
    final slices = buildSlices(
      rows: filters.grouped ? summary.groups : summary.rows,
      direction: direction,
      grouped: filters.grouped,
      categoryRows: summary.rows,
    );
    final total = summary.totalFor(direction);
    final page = context.findAncestorStateOfType<_OperationsPageState>();
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.surface,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                if (!phone)
                  Text(
                    structureTitle(direction),
                    style: golos(18, FontWeight.w600, colors.text),
                  ),
                if (filters.direction == null)
                  SegmentedButton<String>(
                    segments: const [
                      ButtonSegment(value: 'out', label: Text('Списания')),
                      ButtonSegment(value: 'in', label: Text('Поступления')),
                    ],
                    selected: {filters.ringDirection},
                    onSelectionChanged: (value) =>
                        page?._patch(ringDirection: value.first),
                  ),
                SegmentedButton<bool>(
                  segments: const [
                    ButtonSegment(value: false, label: Text('Категории')),
                    ButtonSegment(value: true, label: Text('Группы')),
                  ],
                  selected: {filters.grouped},
                  onSelectionChanged: (value) =>
                      page?._patch(grouped: value.first),
                ),
              ],
            ),
            const SizedBox(height: 16),
            if (phone) ...[
              Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  DonutChart(
                    slices: slices,
                    center: '',
                    caption: '',
                    size: 150,
                    showCenter: false,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          direction == 'in'
                              ? 'Поступило ${filters.periodLabel}'
                              : 'Списано ${filters.periodLabel}',
                          style: golos(14, FontWeight.w500, colors.textMuted),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          formatMoneyShort(total),
                          key: const Key('ops-total'),
                          style: unbounded(26, FontWeight.w600, colors.text),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '${operationsPhrase(_ops(summary, direction))} · ${formatDateShort(summary.dateFrom)} — ${formatDateShort(summary.dateTo)}',
                          style: golos(13, FontWeight.w400, colors.textMuted),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              for (final slice in slices)
                _LegendRow(
                  slice: slice,
                  selected: _selected(slice, filters),
                  showArrow: true,
                  shortMoney: true,
                  onTap: () => onSlice(slice),
                ),
            ] else
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  DonutChart(
                    slices: slices,
                    center: formatMoneyShort(total),
                    caption: filters.periodLabel,
                  ),
                  const SizedBox(width: 28),
                  Expanded(
                    child: _LegendGrid(
                      slices: slices,
                      filters: filters,
                      onSlice: onSlice,
                    ),
                  ),
                ],
              ),
            const SizedBox(height: 8),
            Text(
              phone
                  ? 'Нажмите на долю — откроется список её операций. Диаграмма считает те же операции, что и список, со всеми фильтрами.'
                  : 'Нажмите на долю — список ниже покажет только её операции. Диаграмма считает те же операции, что и список, со всеми фильтрами.',
              style: golos(13, FontWeight.w400, colors.textMuted),
            ),
          ],
        ),
      ),
    );
  }

  int _ops(CategorySummary summary, String direction) => summary.rows
      .where((row) => row.direction == direction)
      .fold(0, (sum, row) => sum + row.operations);
}

bool _selected(DonutSlice slice, OperationFilters filters) {
  if (filters.categories.isEmpty) return false;
  if (slice.other) {
    return slice.categories.isNotEmpty &&
        slice.categories.every(filters.categories.contains) &&
        filters.categories.length == slice.categories.length;
  }
  return slice.categories.length == 1 &&
      filters.categories.contains(slice.categories.first) &&
      filters.categories.length == 1;
}

class _LegendGrid extends StatelessWidget {
  const _LegendGrid({
    required this.slices,
    required this.filters,
    required this.onSlice,
  });

  final List<DonutSlice> slices;
  final OperationFilters filters;
  final ValueChanged<DonutSlice> onSlice;

  @override
  Widget build(BuildContext context) {
    final rows = <Widget>[];
    for (var i = 0; i < slices.length; i += 2) {
      rows.add(
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: _LegendRow(
                slice: slices[i],
                selected: _selected(slices[i], filters),
                onTap: () => onSlice(slices[i]),
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: i + 1 < slices.length
                  ? _LegendRow(
                      slice: slices[i + 1],
                      selected: _selected(slices[i + 1], filters),
                      onTap: () => onSlice(slices[i + 1]),
                    )
                  : const SizedBox.shrink(),
            ),
          ],
        ),
      );
    }
    return Column(children: rows);
  }
}

class _LegendRow extends StatelessWidget {
  const _LegendRow({
    required this.slice,
    required this.selected,
    required this.onTap,
    this.showArrow = false,
    this.shortMoney = false,
  });

  final DonutSlice slice;
  final bool selected;
  final VoidCallback onTap;
  final bool showArrow;
  final bool shortMoney;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final title = slice.other && slice.detail.isNotEmpty
        ? 'Остальное: ${slice.detail}'
        : slice.label;
    return Material(
      color: selected ? colors.primaryContainer : Colors.transparent,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
          child: Row(
            children: [
              Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(
                  color: slice.color,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  title,
                  maxLines: shortMoney ? 2 : 1,
                  overflow: TextOverflow.ellipsis,
                  style: golos(14, FontWeight.w500, colors.text),
                ),
              ),
              SizedBox(
                width: shortMoney ? 96 : 120,
                child: Text(
                  shortMoney
                      ? formatMoneyShort(slice.amountMinor)
                      : formatMoneyExact(slice.amountMinor),
                  textAlign: TextAlign.right,
                  style: golos(14, FontWeight.w600, colors.text),
                ),
              ),
              const SizedBox(width: 8),
              SizedBox(
                width: 52,
                child: Text(
                  formatShare(slice.share),
                  textAlign: TextAlign.right,
                  style: golos(13, FontWeight.w500, colors.textMuted),
                ),
              ),
              if (showArrow)
                Icon(Icons.chevron_right, color: colors.textMuted, size: 18),
            ],
          ),
        ),
      ),
    );
  }
}

class _Table extends ConsumerWidget {
  const _Table({
    required this.items,
    required this.total,
    required this.totals,
    required this.direction,
    required this.phone,
    required this.onCategory,
  });

  final List<TransactionItem> items;
  final int total;
  final FlowTotals totals;
  final String? direction;
  final bool phone;
  final Future<void> Function(TransactionItem item, String category) onCategory;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final sumText = direction == null
        ? '${formatMoneyKopecks(totals.inflowMinor, plus: true)} · ${formatMoneyKopecks(-totals.outflowMinor)}'
        : direction == 'in'
        ? formatMoneyKopecks(totals.inflowMinor, plus: true)
        : formatMoneyKopecks(-totals.outflowMinor);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.surface,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
        child: Column(
          children: [
            Row(
              children: [
                Text(
                  operationsPhrase(total),
                  key: const Key('ops-count'),
                  maxLines: 1,
                  style: golos(16, FontWeight.w600, colors.text),
                ),
                const SizedBox(width: 12),
                // The sum takes the rest of the row so it sits at the right edge, over the amounts.
                Expanded(
                  child: Text(
                    sumText,
                    key: const Key('ops-sum'),
                    textAlign: TextAlign.right,
                    style: golos(16, FontWeight.w700, colors.text),
                  ),
                ),
              ],
            ),
            if (!phone)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Row(
                  children: [
                    SizedBox(
                      width: 64,
                      child: Text(
                        'Дата',
                        style: golos(12, FontWeight.w600, colors.textMuted),
                      ),
                    ),
                    Expanded(
                      flex: 3,
                      child: Text(
                        'Контрагент и назначение',
                        style: golos(12, FontWeight.w600, colors.textMuted),
                      ),
                    ),
                    Expanded(
                      flex: 2,
                      child: Text(
                        'Категория',
                        style: golos(12, FontWeight.w600, colors.textMuted),
                      ),
                    ),
                    SizedBox(
                      width: 140,
                      child: Text(
                        'Сумма',
                        textAlign: TextAlign.right,
                        style: golos(12, FontWeight.w600, colors.textMuted),
                      ),
                    ),
                  ],
                ),
              ),
            for (final item in items)
              _OpRow(item: item, phone: phone, onCategory: onCategory),
            Align(
              alignment: Alignment.centerLeft,
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  shownPhrase(items.length, total),
                  style: golos(13, FontWeight.w400, colors.textMuted),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _OpRow extends ConsumerWidget {
  const _OpRow({
    required this.item,
    required this.phone,
    required this.onCategory,
  });

  final TransactionItem item;
  final bool phone;
  final Future<void> Function(TransactionItem item, String category) onCategory;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final dictionaries = ref.watch(dictionariesProvider).valueOrNull;
    final options = dictionaries?.categories ?? const <CodeLabel>[];
    final choices = options.isEmpty
        ? [
            for (final entry in categoryLabels.entries)
              CodeLabel(code: entry.key, label: entry.value),
          ]
        : options;
    final asOf = ref.watch(sessionProvider).asOf;
    final amount = Text(
      formatMoneyKopecks(item.signedMinor),
      style: golos(
        14,
        FontWeight.w600,
        item.direction == 'in' ? colors.positive : colors.text,
      ),
    );
    final category = PopupMenuButton<String>(
      tooltip: 'Категория',
      onSelected: (value) => onCategory(item, value),
      itemBuilder: (context) => [
        if (asOf != null)
          PopupMenuItem(
            enabled: false,
            child: Text('Применится с ${formatDateDot(asOf)}'),
          ),
        for (final choice in choices)
          PopupMenuItem(value: choice.code, child: Text(choice.label)),
      ],
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: colors.surfaceMuted,
          borderRadius: BorderRadius.circular(999),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          child: Text(
            categoryLabel(item.category),
            style: golos(13, FontWeight.w500, colors.text),
          ),
        ),
      ),
    );
    if (phone) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 52,
              child: Text(
                formatDateDayMonth(item.date),
                style: golos(13, FontWeight.w500, colors.textMuted),
              ),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    item.counterpartyName,
                    style: golos(14, FontWeight.w600, colors.text),
                  ),
                  Text(
                    item.purpose,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: golos(12, FontWeight.w400, colors.textMuted),
                  ),
                  const SizedBox(height: 4),
                  category,
                ],
              ),
            ),
            amount,
          ],
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 64,
            child: Text(
              formatDateDayMonth(item.date),
              style: golos(13, FontWeight.w500, colors.textMuted),
            ),
          ),
          Expanded(
            flex: 3,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  item.counterpartyName,
                  style: golos(14, FontWeight.w600, colors.text),
                ),
                Text(
                  item.purpose,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: golos(12, FontWeight.w400, colors.textMuted),
                ),
              ],
            ),
          ),
          Expanded(
            flex: 2,
            child: Align(alignment: Alignment.centerLeft, child: category),
          ),
          SizedBox(
            width: 140,
            child: Align(alignment: Alignment.centerRight, child: amount),
          ),
        ],
      ),
    );
  }
}
