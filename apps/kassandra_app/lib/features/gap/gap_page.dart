import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/providers.dart';
import '../../design/icons.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';

class GapPage extends ConsumerWidget {
  const GapPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bundle = ref.watch(forecastBundleProvider);
    final phone = isPhone(context);
    return bundle.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, _) => ErrorPane(
        error: error,
        onRetry: () => ref.invalidate(forecastBundleProvider),
      ),
      data: (data) {
        final forecast = data.forecast;
        final risk = forecast.risk;
        final colors = KassandraColors.of(context);
        final days = risk.firstGapDate?.difference(forecast.asOf).inDays;
        final inflow = forecast.inflowMinor;
        final outflow = forecast.outflowMinor;
        final ratio = outflow == 0 ? 0.0 : (inflow / outflow).clamp(0.0, 1.0);
        return ListView(
          key: const Key('page-scroll'),
          padding: phone
              ? const EdgeInsets.fromLTRB(20, 14, 20, 96)
              : const EdgeInsets.fromLTRB(40, 32, 40, 48),
          children: [
            Row(
              children: [
                _Back(
                  onTap: () => context.go(
                    '/forecast${sectionQuery(ref.read(sessionProvider))}',
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  'Кассовый разрыв',
                  style: golos(
                    phone ? 17 : 30,
                    phone ? FontWeight.w600 : FontWeight.w600,
                    colors.text,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            if (risk.firstGapDate != null)
              Wrap(
                crossAxisAlignment: WrapCrossAlignment.end,
                spacing: 10,
                children: [
                  Text(
                    formatDateShort(risk.firstGapDate!),
                    style: unbounded(
                      phone ? 30 : 36,
                      FontWeight.w600,
                      colors.text,
                    ),
                  ),
                  if (days != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Text(
                        'через ${daysWord(days)}',
                        style: golos(15, FontWeight.w400, colors.textMuted),
                      ),
                    ),
                ],
              ),
            const SizedBox(height: 12),
            Text(
              'Не хватит к ${formatDateShort(forecast.horizonEnd)}',
              style: golos(14, FontWeight.w400, colors.textMuted),
            ),
            const SizedBox(height: 4),
            Text(
              formatMoneyExact(risk.liquidityNeedMinor),
              style: unbounded(
                phone ? 36 : 40,
                FontWeight.w600,
                colors.danger,
                tabular: true,
              ),
            ),
            const SizedBox(height: 14),
            SurfaceCard(
              radius: 18,
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (risk.probability30d == null ||
                      risk.calibrationStatus == 'unavailable')
                    Text(
                      'Вероятность недоступна',
                      style: golos(15, FontWeight.w600, colors.text),
                    )
                  else ...[
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            'Вероятность разрыва',
                            style: golos(14, FontWeight.w400, colors.text),
                          ),
                        ),
                        Text(
                          formatPercent(risk.probability30d!),
                          style: mono(
                            14,
                            colors.warning,
                            weight: FontWeight.w500,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(4),
                      child: LinearProgressIndicator(
                        minHeight: 8,
                        value: risk.probability30d!.clamp(0, 1),
                        backgroundColor: colors.divider,
                        color: colors.warning,
                      ),
                    ),
                  ],
                  const SizedBox(height: 8),
                  Text(
                    risk.probability30d == null
                        ? risk.limitation
                        : risk.calibrationStatus.startsWith('calibrated')
                        ? 'откалибровано'
                        : 'экспериментальная оценка${risk.simulations > 0 ? ' · ${groupDigits('${risk.simulations}')} траекторий' : ''}',
                    style: golos(12, FontWeight.w400, colors.textMuted),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            SurfaceCard(
              radius: 20,
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Почему',
                    style: golos(16, FontWeight.w600, colors.text),
                  ),
                  const SizedBox(height: 10),
                  _Bar(
                    label: 'Списания за 30 дней',
                    value: formatMoneyShort(outflow),
                    width: 1,
                    color: colors.danger,
                  ),
                  const SizedBox(height: 8),
                  _Bar(
                    label: 'Поступления',
                    value: formatMoneyShort(inflow),
                    width: ratio,
                    color: colors.positive,
                  ),
                  for (final reason in data.explanation.why)
                    Padding(
                      padding: const EdgeInsets.only(top: 10),
                      child: Text(
                        reason,
                        style: golos(14, FontWeight.w400, colors.text),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            Text('Что сделать', style: golos(16, FontWeight.w600, colors.text)),
            const SizedBox(height: 8),
            for (final action in data.explanation.actions)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: SurfaceCard(
                  radius: 16,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 10,
                  ),
                  child: Row(
                    children: [
                      Container(
                        width: 36,
                        height: 36,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          color: colors.primaryContainer,
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: KIcon(
                          _icon(action.code),
                          size: 18,
                          color: colors.primary,
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              action.title,
                              style: golos(15, FontWeight.w600, colors.text),
                            ),
                            Text(
                              action.detail,
                              style: golos(
                                13,
                                FontWeight.w400,
                                colors.textMuted,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 12),
            FilledButton(
              key: const Key('worst-scenario'),
              onPressed: () => context.go(
                '/scenarios${sectionQuery(ref.read(sessionProvider))}',
              ),
              child: const Text('Проверить худший сценарий'),
            ),
          ],
        );
      },
    );
  }
}

String _icon(String code) => switch (code) {
  'speed_up_client' => KIcon.arrowUp,
  'postpone_supplier' => KIcon.calendar,
  'credit_line' => KIcon.bank,
  'reserve' => KIcon.vault,
  _ => KIcon.alert,
};

class _Back extends StatelessWidget {
  const _Back({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Material(
      color: colors.surface,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        key: const Key('gap-back'),
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: const SizedBox(
          width: 44,
          height: 44,
          child: Center(child: KIcon(KIcon.back, size: 20)),
        ),
      ),
    );
  }
}

class _Bar extends StatelessWidget {
  const _Bar({
    required this.label,
    required this.value,
    required this.width,
    required this.color,
  });
  final String label;
  final String value;
  final double width;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                label,
                style: golos(14, FontWeight.w400, colors.text),
              ),
            ),
            Text(value, style: golos(14, FontWeight.w500, colors.text)),
          ],
        ),
        const SizedBox(height: 4),
        FractionallySizedBox(
          widthFactor: width.clamp(0.04, 1),
          child: Container(
            height: 6,
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(3),
            ),
          ),
        ),
      ],
    );
  }
}
