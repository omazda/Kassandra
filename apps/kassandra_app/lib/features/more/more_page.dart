import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/account_picker.dart';
import '../../app/config.dart';
import '../../app/providers.dart';
import '../../design/tokens.dart';

const _models = <(String, String)>[
  ('ensemble', 'Ансамбль ИИ (основная)'),
  ('hgb', 'Бустинг (hgb)'),
  ('structural', 'Структурная'),
  ('weekday', 'По дням недели'),
  ('mean', 'Среднее'),
  ('constant', 'Без изменений'),
];

class MorePage extends ConsumerWidget {
  const MorePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    final colors = KassandraColors.of(context);
    final saved = ref.watch(savedScenariosProvider);
    final phone = isPhone(context);
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: phone ? double.infinity : 720),
        child: ListView(
          padding: phone
              ? const EdgeInsets.fromLTRB(20, 14, 20, 120)
              : const EdgeInsets.fromLTRB(40, 32, 40, 48),
          children: [
            Text(
              phone ? 'Ещё' : 'Настройки',
              style: unbounded(phone ? 28 : 30, FontWeight.w600, colors.text),
            ),
            const SizedBox(height: 16),
            // The sidebar card with «Сменить счёт» exists only on desktop.
            _CompanyTile(accountId: session.accountId),
            if (phone) ...[
              const SizedBox(height: 8),
              for (final item in const [
                ('/operations', 'Операции'),
                ('/plan-fact', 'План и факт'),
                ('/sources', 'Источники данных'),
                ('/gap', 'Детали разрыва'),
                ('/debug', 'Отладка: загрузить датасет'),
              ])
                ListTile(
                  title: Text(item.$2),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => context.go('${item.$1}${sectionQuery(session)}'),
                ),
              const Divider(),
            ] else
              const SizedBox(height: 20),
            Text('Тема', style: golos(16, FontWeight.w600, colors.text)),
            const SizedBox(height: 8),
            SegmentedButton<ThemeMode>(
              segments: const [
                ButtonSegment(value: ThemeMode.system, label: Text('Система')),
                ButtonSegment(value: ThemeMode.light, label: Text('Светлая')),
                ButtonSegment(value: ThemeMode.dark, label: Text('Тёмная')),
              ],
              selected: {session.themeMode},
              onSelectionChanged: (value) =>
                  ref.read(sessionProvider.notifier).setTheme(value.first),
            ),
            const SizedBox(height: 16),
            Text(
              'Модель прогноза',
              style: golos(16, FontWeight.w600, colors.text),
            ),
            const SizedBox(height: 4),
            Text(
              'Выбор меняет прогноз на всех экранах.',
              style: golos(14, FontWeight.w400, colors.textMuted),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final model in _models)
                  ChoiceChip(
                    label: Text(model.$2),
                    selected: session.model == model.$1,
                    onSelected: (_) =>
                        ref.read(sessionProvider.notifier).setModel(model.$1),
                  ),
              ],
            ),
            const SizedBox(height: 16),
            OutlinedButton(
              onPressed: () async {
                final text = await ref.read(apiProvider).experimentsSummary();
                if (!context.mounted) return;
                ScaffoldMessenger.of(context)
                    .showSnackBar(SnackBar(content: Text(text)));
              },
              child: const Text('Сводка экспериментов'),
            ),
            if (saved.isNotEmpty) ...[
              const SizedBox(height: 16),
              Text(
                'Сохранённые сценарии',
                style: golos(16, FontWeight.w600, colors.text),
              ),
              for (final id in saved)
                Text(id, style: golos(13, FontWeight.w400, colors.textMuted)),
            ],
            const SizedBox(height: 16),
            if (!kUseFixtures)
              OutlinedButton(
                onPressed: () => context.go('/login'),
                child: const Text('Войти с токеном'),
              ),
            TextButton(
              onPressed: () {
                ref.read(sessionProvider.notifier).setModel(session.model);
              },
              child: const Text('Пересчитать прогноз'),
            ),
          ],
        ),
      ),
    );
  }
}

class _CompanyTile extends ConsumerWidget {
  const _CompanyTile({required this.accountId});
  final String? accountId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final accounts = ref.watch(accountsProvider).valueOrNull ?? const [];
    final name = accounts
        .where((account) => account.id == accountId)
        .map((account) => account.name)
        .firstOrNull;
    return Material(
      color: colors.surface,
      borderRadius: BorderRadius.circular(16),
      child: ListTile(
        key: const Key('more-switch-account'),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(
          name ?? 'Счёт',
          style: golos(16, FontWeight.w600, colors.text),
        ),
        subtitle: Text(
          accountId ?? '',
          style: golos(13, FontWeight.w400, colors.textMuted),
        ),
        trailing: TextButton(
          onPressed: () => pickAccount(context, ref),
          child: const Text('Сменить счёт'),
        ),
        onTap: () => pickAccount(context, ref),
      ),
    );
  }
}
