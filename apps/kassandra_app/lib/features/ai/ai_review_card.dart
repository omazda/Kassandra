import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/ai.dart';
import '../../api/errors.dart';
import '../../api/models.dart';
import '../../app/config.dart';
import '../../app/providers.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';

/// Reviews already on the server for a run: free to read, so the card asks first.
final aiCachedReviewProvider = FutureProvider.family<AiReview?, String>(
  (ref, runId) => ref.watch(apiProvider).aiCachedReview(runId),
);

/// «ИИ-проверка»: The LLM, a thinking model, checks the ML forecast at the end of the pipeline
/// and gives recommendations. A paid call, so it starts on a button and is cached per run.
class AiReviewCard extends ConsumerStatefulWidget {
  const AiReviewCard({super.key, required this.forecast});
  final ForecastResult forecast;

  @override
  ConsumerState<AiReviewCard> createState() => _AiReviewCardState();
}

class _AiReviewCardState extends ConsumerState<AiReviewCard> {
  AiReview? _fresh;
  String? _error;
  bool _busy = false;
  DateTime? _startedAt;
  Timer? _tick;

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant AiReviewCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.forecast.runId != widget.forecast.runId) {
      _fresh = null;
      _error = null;
    }
  }

  Future<void> _run({bool refresh = false}) async {
    setState(() {
      _busy = true;
      _error = null;
      _startedAt = DateTime.now();
    });
    // The seconds tick while the model thinks, so a long review does not look frozen.
    _tick = Timer.periodic(const Duration(milliseconds: 200), (_) {
      if (mounted) setState(() {});
    });
    try {
      final review = await ref
          .read(apiProvider)
          .aiReview(widget.forecast.runId, refresh: refresh);
      if (mounted) setState(() => _fresh = review);
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      _tick?.cancel();
      if (mounted) setState(() => _busy = false);
    }
  }

  double _seconds() => _startedAt == null
      ? 0
      : DateTime.now().difference(_startedAt!).inMilliseconds / 1000;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final cached = ref
        .watch(aiCachedReviewProvider(widget.forecast.runId))
        .valueOrNull;
    final review = _fresh ?? cached;
    final phone = isPhone(context);
    return SurfaceCard(
      padding: const EdgeInsets.all(20),
      borderColor: colors.primary.withValues(alpha: 0.25),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 10,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Icon(Icons.auto_awesome, color: colors.primary, size: 22),
              Text(
                'ИИ-проверка прогноза',
                style: golos(18, FontWeight.w600, colors.text),
              ),
              _Tag(
                '$kAiName · думающая модель',
                color: colors.primaryContainer,
              ),
              if (review != null && !_busy)
                TextButton(
                  onPressed: () => _run(refresh: true),
                  child: const Text('Проверить заново'),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'ML-модель построила прогноз; $kAiName проверяет его как финансовый директор: '
            'согласованность цифр, точность на этом счёте месяц назад, полноту данных — и даёт рекомендации.',
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
          const SizedBox(height: 14),
          if (_busy)
            Row(
              children: [
                const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    '$kAiName думает над прогнозом — обычно до минуты',
                    style: golos(14, FontWeight.w500, colors.text),
                  ),
                ),
                Text(
                  '${_seconds().toStringAsFixed(0)} с',
                  key: const Key('ai-review-timer'),
                  style: mono(14, colors.primary),
                ),
              ],
            )
          else if (review == null)
            FilledButton.icon(
              key: const Key('ai-review'),
              onPressed: _run,
              icon: const Icon(Icons.auto_awesome, size: 18),
              label: const Text('Проверить с ИИ'),
            )
          else
            _ReviewBody(review: review, phone: phone),
          if (_error != null) ...[
            const SizedBox(height: 10),
            Text(_error!, style: golos(14, FontWeight.w600, colors.danger)),
          ],
        ],
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag(this.text, {required this.color, this.textColor});
  final String text;
  final Color color;
  final Color? textColor;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        text,
        style: golos(
          12,
          FontWeight.w600,
          textColor ?? KassandraColors.of(context).text,
        ),
      ),
    );
  }
}

class _ReviewBody extends StatelessWidget {
  const _ReviewBody({required this.review, required this.phone});
  final AiReview review;
  final bool phone;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final (verdictText, verdictColor, verdictInk) = switch (review.verdict) {
      'ok' => (
        'Можно опираться',
        colors.positiveContainer,
        colors.onPositiveContainer,
      ),
      'weak' => ('Только как ориентир', const Color(0xFFF8E4E2), colors.danger),
      _ => ('С оговорками', colors.warningContainer, colors.onWarningContainer),
    };
    final test = review.backtest;
    final left = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 8,
          runSpacing: 6,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            _Tag(verdictText, color: verdictColor, textColor: verdictInk),
            if (review.cached)
              Text(
                'сохранённая проверка',
                style: golos(12, FontWeight.w400, colors.textMuted),
              ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          review.headline,
          style: golos(17, FontWeight.w600, colors.text, height: 1.3),
        ),
        if (review.trust.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(review.trust, style: golos(14, FontWeight.w400, colors.text)),
        ],
        if (test['model_mae'] != null) ...[
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: colors.surfaceMuted,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(
              'Проверка на этом счёте: прогноз от ${_day(test['forecast_made_on'])} '
              'ошибался в среднем на ${test['model_mae']} против ${test['constant_mae']} '
              'у прогноза «остаток не меняется»; минимум — ${test['forecast_minimum']} '
              'в прогнозе, ${test['actual_minimum']} по факту.',
              style: golos(13, FontWeight.w500, colors.text),
            ),
          ),
        ] else if (test['note'] != null) ...[
          const SizedBox(height: 10),
          Text(
            'Проверка на этом счёте: ${test['note']}',
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
        ],
        const SizedBox(height: 14),
        for (final check in review.checks) _CheckRow(check: check),
      ],
    );
    final right = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Рекомендации', style: golos(15, FontWeight.w700, colors.text)),
        const SizedBox(height: 6),
        for (final rec in review.recommendations) _RecommendationRow(rec: rec),
        if (review.improvements.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text(
            'Как сделать прогноз точнее',
            style: golos(15, FontWeight.w700, colors.text),
          ),
          for (final note in review.improvements)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text.rich(
                TextSpan(
                  children: [
                    TextSpan(
                      text: '${note.title}. ',
                      style: golos(14, FontWeight.w600, colors.text),
                    ),
                    TextSpan(
                      text: note.detail,
                      style: golos(14, FontWeight.w400, colors.textMuted),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ],
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (phone) ...[
          left,
          const SizedBox(height: 16),
          right,
        ] else
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: left),
              const SizedBox(width: 28),
              Expanded(child: right),
            ],
          ),
        if (review.reasoning.isNotEmpty) ...[
          const SizedBox(height: 8),
          Theme(
            data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
            child: ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: Text(
                'Как рассуждала модель',
                style: golos(14, FontWeight.w600, colors.primary),
              ),
              children: [
                Align(
                  alignment: Alignment.centerLeft,
                  child: SelectableText(
                    review.reasoning,
                    style: mono(12, colors.textMuted),
                  ),
                ),
              ],
            ),
          ),
        ],
        Text(
          '${review.model} · думала ${review.elapsed.toStringAsFixed(0)} с',
          style: golos(12, FontWeight.w400, colors.textMuted),
        ),
      ],
    );
  }

  static String _day(Object? iso) {
    final date = asDateOpt(iso);
    return date == null ? '—' : formatDayMonth(date);
  }
}

class _CheckRow extends StatelessWidget {
  const _CheckRow({required this.check});
  final AiCheck check;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final (icon, color) = switch (check.status) {
      'ok' => (Icons.check_circle_outline, colors.positive),
      'problem' => (Icons.error_outline, colors.danger),
      _ => (Icons.warning_amber_rounded, colors.warning),
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: color),
          const SizedBox(width: 8),
          Expanded(
            child: Text.rich(
              TextSpan(
                children: [
                  TextSpan(
                    text: '${check.title}. ',
                    style: golos(14, FontWeight.w600, colors.text),
                  ),
                  TextSpan(
                    text: check.detail,
                    style: golos(14, FontWeight.w400, colors.textMuted),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _RecommendationRow extends StatelessWidget {
  const _RecommendationRow({required this.rec});
  final AiRecommendation rec;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final meta = [
      if (rec.deadline != null) 'до ${formatDayMonth(rec.deadline!)}',
      if (rec.amountMinor != null)
        formatMoneyShort(rec.amountMinor!, ceil: true),
    ];
    final (label, tagColor) = switch (rec.priority) {
      'high' => ('важно', colors.warningContainer),
      'low' => ('можно позже', colors.surfaceMuted),
      _ => ('стоит сделать', colors.primaryContainer),
    };
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        border: Border.all(color: colors.divider),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(rec.title, style: golos(14, FontWeight.w700, colors.text)),
              _Tag(label, color: tagColor),
              if (meta.isNotEmpty)
                Text(
                  meta.join(' · '),
                  style: golos(12, FontWeight.w500, colors.textMuted),
                ),
            ],
          ),
          const SizedBox(height: 4),
          Text(rec.detail, style: golos(13, FontWeight.w400, colors.textMuted)),
        ],
      ),
    );
  }
}
