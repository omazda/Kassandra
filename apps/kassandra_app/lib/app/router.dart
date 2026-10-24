import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/auth/login_page.dart';
import '../features/debug/debug_page.dart';
import '../features/events/calendar_page.dart';
import '../features/events/event_page.dart';
import '../features/forecast/forecast_page.dart';
import '../features/gap/gap_page.dart';
import '../features/more/more_page.dart';
import '../features/operations/operations_page.dart';
import '../features/plan_fact/plan_fact_page.dart';
import '../features/scenarios/scenario_page.dart';
import '../features/sources/sources_page.dart';
import '../design/tokens.dart';
import 'config.dart';
import 'providers.dart';
import 'shell.dart';

final routerProvider = Provider<GoRouter>((ref) {
  final refresh = ValueNotifier<int>(0);
  ref.listen(needsLoginProvider, (_, _) => refresh.value++);
  ref.onDispose(refresh.dispose);
  return GoRouter(
    initialLocation: '/forecast',
    refreshListenable: refresh,
    redirect: (context, state) {
      final login = ref.read(needsLoginProvider);
      final onLogin = state.uri.path == '/login';
      if (login && !onLogin) return '/login';
      if (!login && onLogin && kUseFixtures) return '/forecast';
      if (state.uri.path == '/') return '/forecast';
      return null;
    },
    routes: [
      GoRoute(path: '/login', builder: (_, _) => const LoginPage()),
      ShellRoute(
        builder: (_, _, child) => AppShell(child: child),
        routes: [
          GoRoute(
            path: '/forecast',
            pageBuilder: (_, state) => _tab(state, const ForecastPage()),
          ),
          GoRoute(
            path: '/gap',
            pageBuilder: (_, state) => _tab(state, const GapPage()),
          ),
          GoRoute(
            path: '/scenarios',
            pageBuilder: (_, state) => _tab(state, const ScenarioPage()),
          ),
          GoRoute(
            path: '/events/new',
            pageBuilder: (_, state) => _tab(state, const EventPage()),
          ),
          GoRoute(
            path: '/calendar',
            pageBuilder: (_, state) => _tab(state, const CalendarPage()),
          ),
          GoRoute(
            path: '/operations',
            pageBuilder: (_, state) => _tab(state, const OperationsPage()),
          ),
          GoRoute(
            path: '/plan-fact',
            pageBuilder: (_, state) => _tab(state, const PlanFactPage()),
          ),
          GoRoute(
            path: '/sources',
            pageBuilder: (_, state) => _tab(state, const SourcesPage()),
          ),
          GoRoute(
            path: '/more',
            pageBuilder: (_, state) => _tab(state, const MorePage()),
          ),
          GoRoute(
            path: '/debug',
            pageBuilder: (_, state) => _tab(state, const DebugPage()),
          ),
        ],
      ),
    ],
  );
});

/// Tabs fade into each other instead of cutting: the old screen fades out over the first
/// third, the new one fades in and rises a few pixels. Query changes keep the same page.
Page<void> _tab(GoRouterState state, Widget child) {
  return CustomTransitionPage<void>(
    key: state.pageKey,
    transitionDuration: const Duration(milliseconds: 260),
    reverseTransitionDuration: const Duration(milliseconds: 200),
    child: child,
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      final incoming = CurvedAnimation(
        parent: animation,
        curve: const Interval(0.25, 1, curve: Curves.easeOutCubic),
      );
      final outgoing = CurvedAnimation(
        parent: secondaryAnimation,
        curve: const Interval(0, 0.35, curve: Curves.easeIn),
      );
      return FadeTransition(
        opacity: ReverseAnimation(outgoing),
        child: FadeTransition(
          opacity: incoming,
          child: SlideTransition(
            position: Tween(
              begin: const Offset(0, 0.006),
              end: Offset.zero,
            ).animate(incoming),
            // Opaque, so the new page hides the one leaving under it; a Material, not a
            // ColoredBox, so list tiles keep their ink.
            child: Material(
              color: KassandraColors.of(context).background,
              child: child,
            ),
          ),
        ),
      );
    },
  );
}
