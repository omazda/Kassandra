import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../design/icons.dart';
import '../features/ai/assistant.dart';
import '../features/events/calendar_page.dart';
import '../features/operations/operations_page.dart';
import '../features/plan_fact/plan_fact_page.dart';
import '../design/logo.dart';
import '../design/tokens.dart';
import '../format/format.dart';
import 'account_picker.dart';
import 'config.dart';
import 'providers.dart';

class AppShell extends ConsumerStatefulWidget {
  const AppShell({super.key, required this.child});
  final Widget child;

  @override
  ConsumerState<AppShell> createState() => _AppShellState();
}

class _AppShellState extends ConsumerState<AppShell> {
  var _bootstrapped = false;
  String? _prefetched;

  /// Warms the other tabs once a forecast is ready, one request at a time, so their first
  /// opening shows data instead of a spinner. Failures stay silent: the tab retries itself.
  Future<void> _prefetch() async {
    final loads = <Future<Object?> Function()>[
      () => ref.read(paymentCalendarProvider.future),
      () => ref.read(categorySummaryProvider.future),
      () => prefetchOperations(ref),
      () => ref.read(planFactProvider.future),
      () => ref.read(planFactWeekProvider.future),
      () => ref.read(dictionariesProvider.future),
      () => ref.read(counterpartyRisksProvider.future),
      () => ref.read(recurringProvider.future),
      () => ref.read(taxHintProvider.future),
    ];
    for (final load in loads) {
      if (!mounted) return;
      try {
        await load();
      } catch (_) {}
    }
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(forecastBundleProvider, (previous, next) {
      final runId = next.valueOrNull?.forecast.runId;
      if (runId == null || runId == _prefetched) return;
      _prefetched = runId;
      // After the frame: the screen the user is on sends its own requests first.
      WidgetsBinding.instance.addPostFrameCallback((_) => _prefetch());
    });
    ref.listen(noticeProvider, (previous, next) {
      if (next == null || next == previous) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(next)));
      ref.read(noticeProvider.notifier).state = null;
    });

    final accounts = ref.watch(accountsProvider);
    return accounts.when(
      loading: () =>
          const Scaffold(body: Center(child: CircularProgressIndicator())),
      error: (error, _) => Scaffold(
        body: ErrorRetry(
          error: error,
          onRetry: () => ref.invalidate(accountsProvider),
        ),
      ),
      data: (list) {
        _bootstrap(list);
        final session = ref.watch(sessionProvider);
        if (session.accountId == null) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }
        final width = MediaQuery.sizeOf(context).width;
        final path = GoRouterState.of(context).uri.path;
        final phone = width < 600;
        final desktop = width >= 1024;
        final hideNav =
            path.startsWith('/events/new') ||
            (phone && path.startsWith('/gap'));
        final account = _find(list, session.accountId!);
        final assistantOpen = ref.watch(assistantProvider).open;
        return Scaffold(
          backgroundColor: KassandraColors.of(context).background,
          body: Stack(
            children: [
              Row(
                children: [
                  if (desktop)
                    _Sidebar(path: path, account: account)
                  else if (!phone)
                    _Rail(path: path),
                  // The page makes room for the open chat instead of hiding under it.
                  Expanded(
                    child: Padding(
                      padding: EdgeInsets.only(
                        right: !phone && assistantOpen ? 440 : 0,
                      ),
                      child: widget.child,
                    ),
                  ),
                ],
              ),
              // The chat sits beside the page on wide screens, so its actions stay visible.
              if (!phone && assistantOpen)
                Positioned(
                  top: 0,
                  right: 0,
                  bottom: 0,
                  width: 440,
                  child: AssistantPanel(
                    onClose: () =>
                        ref.read(assistantProvider.notifier).setOpen(false),
                  ),
                ),
            ],
          ),
          floatingActionButton: !phone
              ? (assistantOpen || kUseFixtures
                    ? null
                    : const AssistantLauncher())
              : !hideNav && ref.watch(phoneAddVisibleProvider)
              ? Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    if (!kUseFixtures) ...[
                      const AssistantLauncher(compact: true),
                      const SizedBox(height: 12),
                    ],
                    FloatingActionButton(
                      key: const Key('add-event'),
                      heroTag: 'add-event',
                      onPressed: () => _go('/events/new'),
                      tooltip: 'Добавить событие',
                      elevation: 8,
                      backgroundColor: const Color(0xFF1E6E70),
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(18),
                      ),
                      child: const KIcon(
                        KIcon.plus,
                        color: Colors.white,
                        stroke: 2.2,
                      ),
                    ),
                  ],
                )
              : null,
          bottomNavigationBar: phone && !hideNav
              ? _BottomNav(path: path)
              : null,
        );
      },
    );
  }

  void _bootstrap(List<Account> list) {
    if (_bootstrapped || list.isEmpty) return;
    final session = ref.read(sessionProvider);
    if (session.accountId != null) {
      _bootstrapped = true;
      return;
    }
    _bootstrapped = true;
    final uri = GoRouterState.of(context).uri;
    final requested = uri.queryParameters['account'];
    final account = _find(list, requested) ?? list.first;
    final raw = uri.queryParameters['as_of'];
    final asOf = raw == null ? account.lastDate : DateTime.tryParse(raw);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ref.read(sessionProvider.notifier).applyAccount(account, asOf: asOf);
      if ((requested == null || raw == null) && mounted) {
        final next = ref.read(sessionProvider);
        final path = uri.path == '/' ? '/forecast' : uri.path;
        context.go('$path${sectionQuery(next)}');
      }
    });
  }

  Account? _find(List<Account> list, String? id) {
    for (final account in list) {
      if (account.id == id) return account;
    }
    return null;
  }

  void _go(String path) {
    final session = ref.read(sessionProvider);
    context.go('$path${sectionQuery(session)}');
  }
}

class ErrorRetry extends StatelessWidget {
  const ErrorRetry({super.key, required this.error, required this.onRetry});
  final Object error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final message = error is Exception
        ? error.toString()
        : 'Не удалось загрузить счета';
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            message,
            style: golos(16, FontWeight.w500, colors.text),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 12),
          FilledButton(onPressed: onRetry, child: const Text('Повторить')),
        ],
      ),
    );
  }
}

class _NavItem {
  const _NavItem(this.path, this.label, this.icon, this.keyName);
  final String path;
  final String label;
  final String icon;
  final String keyName;
}

const _items = [
  _NavItem('/forecast', 'Прогноз', KIcon.trend, 'nav-forecast'),
  _NavItem('/scenarios', 'Что если', KIcon.split, 'nav-scenarios'),
  _NavItem('/calendar', 'Календарь платежей', KIcon.calendar, 'nav-calendar'),
  _NavItem('/operations', 'Операции', KIcon.list, 'nav-operations'),
  _NavItem('/plan-fact', 'План и факт', KIcon.bars, 'nav-plan'),
  _NavItem('/sources', 'Источники данных', KIcon.download, 'nav-sources'),
];

const _settings = _NavItem('/more', 'Настройки', KIcon.settings, 'nav-more');
const _debug = _NavItem('/debug', 'Отладка', KIcon.flask, 'nav-debug');

bool _selected(String path, String item) {
  if (item == '/forecast') {
    return path.startsWith('/forecast') || path.startsWith('/gap');
  }
  return path.startsWith(item);
}

class _Sidebar extends ConsumerWidget {
  const _Sidebar({required this.path, required this.account});
  final String path;
  final Account? account;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final bundle = ref.watch(forecastBundleProvider);
    final days = bundle.maybeWhen(
      data: (data) => data.forecast.confidence.historyDays,
      orElse: () => null,
    );
    return Container(
      width: 240,
      color: colors.ink,
      padding: const EdgeInsets.fromLTRB(20, 28, 20, 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // The lockup takes the full column width: with side padding it shrank to 168 px.
          const KassandraLogo(fontSize: 22),
          const SizedBox(height: 28),
          Expanded(
            child: ListView(
              children: [
                for (final item in _items)
                  _SideLink(item: item, selected: _selected(path, item.path)),
              ],
            ),
          ),
          _SideLink(item: _debug, selected: _selected(path, _debug.path)),
          _SideLink(item: _settings, selected: _selected(path, _settings.path)),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: colors.inkSurface,
              borderRadius: BorderRadius.circular(KRadius.tile),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  account?.debug == true
                      ? 'ОТЛАДКА · ${account!.id.split('-').first}'
                      : 'КОМПАНИЯ',
                  style: golos(
                    12,
                    FontWeight.w600,
                    colors.inkMuted,
                  ).copyWith(letterSpacing: 0.96),
                ),
                const SizedBox(height: 8),
                Text(
                  account?.name ?? 'Счёт',
                  style: golos(16, FontWeight.w600, colors.onInk),
                ),
                const SizedBox(height: 4),
                Text(
                  days == null
                      ? 'Демо-счёт'
                      : '$days ${daysWord(days).split(' ').last} истории',
                  style: golos(13, FontWeight.w400, colors.inkMuted),
                ),
                const SizedBox(height: 10),
                OutlinedButton(
                  onPressed: () => pickAccount(context, ref),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: colors.onInk,
                    side: const BorderSide(color: Color(0xFF2E5A5D)),
                    minimumSize: const Size.fromHeight(40),
                  ),
                  child: const Text('Сменить счёт'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _SideLink extends ConsumerWidget {
  const _SideLink({required this.item, required this.selected});
  final _NavItem item;
  final bool selected;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: InkWell(
        key: Key(item.keyName),
        borderRadius: BorderRadius.circular(KRadius.control),
        onTap: () => context.go(
          '${item.path}${sectionQuery(ref.read(sessionProvider))}',
        ),
        child: Container(
          constraints: const BoxConstraints(minHeight: 44),
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: BoxDecoration(
            color: selected ? colors.inkSelected : Colors.transparent,
            borderRadius: BorderRadius.circular(KRadius.control),
          ),
          child: Row(
            children: [
              KIcon(
                item.icon,
                size: 20,
                color: selected ? Colors.white : colors.navMuted,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  item.label,
                  style: golos(
                    15,
                    selected ? FontWeight.w600 : FontWeight.w400,
                    selected ? Colors.white : colors.navMuted,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Rail extends ConsumerWidget {
  const _Rail({required this.path});
  final String path;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final destinations = [..._items, _settings];
    final selected = destinations.indexWhere(
      (item) => _selected(path, item.path),
    );
    return NavigationRail(
      backgroundColor: colors.ink,
      selectedIndex: selected < 0 ? 0 : selected,
      onDestinationSelected: (index) {
        context.go(
          '${destinations[index].path}${sectionQuery(ref.read(sessionProvider))}',
        );
      },
      labelType: NavigationRailLabelType.none,
      selectedIconTheme: IconThemeData(color: colors.primary),
      unselectedIconTheme: IconThemeData(color: colors.navMuted),
      destinations: [
        for (final item in destinations)
          NavigationRailDestination(
            icon: KIcon(
              item.icon,
              color: colors.navMuted,
              key: Key(item.keyName),
            ),
            selectedIcon: KIcon(item.icon, color: Colors.white),
            label: Text(item.label),
          ),
      ],
    );
  }
}

class _BottomNav extends ConsumerWidget {
  const _BottomNav({required this.path});
  final String path;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    const items = [
      _NavItem('/forecast', 'Прогноз', KIcon.trend, 'nav-forecast'),
      _NavItem('/scenarios', 'Что если', KIcon.split, 'nav-scenarios'),
      _NavItem('/calendar', 'Календарь', KIcon.calendar, 'nav-calendar'),
      _NavItem('/more', 'Ещё', KIcon.more, 'nav-more'),
    ];
    var index = 0;
    if (path.startsWith('/scenarios')) index = 1;
    if (path.startsWith('/calendar')) index = 2;
    if (path.startsWith('/more') ||
        path.startsWith('/operations') ||
        path.startsWith('/plan-fact') ||
        path.startsWith('/sources')) {
      index = 3;
    }
    return NavigationBar(
      height: 80,
      backgroundColor: colors.surface,
      indicatorColor: colors.primaryContainer,
      selectedIndex: index,
      onDestinationSelected: (value) {
        context.go(
          '${items[value].path}${sectionQuery(ref.read(sessionProvider))}',
        );
      },
      destinations: [
        for (final item in items)
          NavigationDestination(
            key: Key(item.keyName),
            icon: KIcon(item.icon, color: colors.textMuted),
            selectedIcon: KIcon(item.icon, color: colors.primary),
            label: item.label,
          ),
      ],
    );
  }
}
