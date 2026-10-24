import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/debug.dart';
import '../../api/errors.dart';
import '../../app/providers.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';

final forecastTraceProvider = FutureProvider.autoDispose
    .family<List<TraceStep>, String>(
      (ref, runId) => ref.watch(apiProvider).forecastTrace(runId),
    );

/// «Как построен прогноз»: the server's step log for this run, folded until asked.
class ForecastTraceCard extends ConsumerStatefulWidget {
  const ForecastTraceCard({super.key, required this.runId});
  final String runId;

  @override
  ConsumerState<ForecastTraceCard> createState() => _ForecastTraceCardState();
}

class _ForecastTraceCardState extends ConsumerState<ForecastTraceCard> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return SurfaceCard(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InkWell(
            key: const Key('forecast-trace-toggle'),
            onTap: () => setState(() => _open = !_open),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Row(
                children: [
                  Icon(Icons.account_tree_outlined, color: colors.primary),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'Как построен прогноз — пошагово',
                      style: golos(16, FontWeight.w600, colors.text),
                    ),
                  ),
                  Icon(
                    _open ? Icons.expand_less : Icons.expand_more,
                    color: colors.textMuted,
                  ),
                ],
              ),
            ),
          ),
          if (_open)
            ref
                .watch(forecastTraceProvider(widget.runId))
                .when(
                  loading: () => const Padding(
                    padding: EdgeInsets.all(16),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                  error: (error, _) => EmptyHint(
                    text: error is ApiException
                        ? error.message
                        : 'Журнал расчёта недоступен',
                  ),
                  data: (steps) => steps.isEmpty
                      ? const EmptyHint(text: 'Журнал расчёта недоступен')
                      : TraceList(steps: steps),
                ),
        ],
      ),
    );
  }
}

/// The forecast trace: numbered steps from the statement to the gap verdict.
class TraceList extends StatelessWidget {
  const TraceList({super.key, required this.steps});
  final List<TraceStep> steps;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Column(
      key: const Key('forecast-trace'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final step in steps)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 26,
                  height: 26,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: colors.primaryContainer,
                    shape: BoxShape.circle,
                  ),
                  child: Text(
                    '${step.n}',
                    style: golos(12, FontWeight.w700, colors.primary),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        step.title,
                        style: golos(14, FontWeight.w600, colors.text),
                      ),
                      if (step.detail.isNotEmpty)
                        Text(
                          step.detail,
                          style: golos(13, FontWeight.w400, colors.textMuted),
                        ),
                      for (final item in step.items)
                        Padding(
                          padding: const EdgeInsets.only(top: 2),
                          child: Text(
                            '• $item',
                            style: golos(13, FontWeight.w400, colors.text),
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
