import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/ai.dart';
import '../../api/errors.dart';
import '../../api/models.dart';
import '../../app/config.dart';
import '../../app/providers.dart';
import '../../design/tokens.dart';
import '../../format/format.dart';

/// Scan or photo of an invoice or contract → OCR (tesseract) → the LLM reads the fields →
/// payments the user can add to the forecast in one click.
Future<void> showOcrDialog(BuildContext context, WidgetRef ref) async {
  final picked = await FilePicker.platform.pickFiles(
    type: FileType.custom,
    allowedExtensions: const ['pdf', 'png', 'jpg', 'jpeg', 'webp', 'txt'],
    withData: true,
  );
  final file = picked?.files.singleOrNull;
  if (file?.bytes == null || !context.mounted) return;
  await showDialog<void>(
    context: context,
    builder: (_) => _OcrDialog(bytes: file!.bytes!, filename: file.name),
  );
}

class _OcrDialog extends ConsumerStatefulWidget {
  const _OcrDialog({required this.bytes, required this.filename});
  final List<int> bytes;
  final String filename;

  @override
  ConsumerState<_OcrDialog> createState() => _OcrDialogState();
}

class _OcrDialogState extends ConsumerState<_OcrDialog> {
  AiOcrResult? _result;
  String? _error;
  bool _saving = false;
  final _direction = <int, String>{};
  final _date = <int, DateTime>{};

  @override
  void initState() {
    super.initState();
    _recognize();
  }

  Future<void> _recognize() async {
    try {
      final result = await ref
          .read(apiProvider)
          .aiOcr(
            widget.bytes,
            widget.filename,
            ref.read(sessionProvider).accountId,
          );
      if (!mounted) return;
      setState(() {
        _result = result;
        for (var i = 0; i < result.drafts.length; i++) {
          final draft = result.drafts[i];
          if (draft.direction != null) _direction[i] = draft.direction!;
          if (draft.date != null) _date[i] = draft.date!;
        }
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  bool _ready(int i) => _direction[i] != null && _date[i] != null;

  Future<void> _add() async {
    final result = _result!;
    final forecast = ref.read(forecastBundleProvider).valueOrNull?.forecast;
    if (forecast == null) return;
    setState(() => _saving = true);
    final stamp = DateTime.now().millisecondsSinceEpoch;
    try {
      for (var i = 0; i < result.drafts.length; i++) {
        if (!_ready(i)) continue;
        final draft = result.drafts[i];
        final direction = _direction[i]!;
        await ref
            .read(apiProvider)
            .createEvent(
              PlannedEvent(
                id: 'ocr-$stamp-$i',
                accountId: forecast.accountId,
                componentId: 'manual',
                category: direction == 'in' ? 'customer_payment' : 'supplier',
                amountMinor: draft.amountMinor,
                direction: direction,
                dueDate: _date[i]!,
                expectedDate: _date[i]!,
                knownAt: forecast.asOf,
                source: draft.source,
                status: 'confirmed',
                certainty: draft.certainty,
                label: draft.label,
                counterpartyName: draft.counterpartyName,
                counterpartyInn: draft.counterpartyInn,
              ),
              baseRunId: forecast.runId,
            );
      }
      ref.read(sessionProvider.notifier).refresh();
      ref.read(noticeProvider.notifier).state =
          'Платежи из документа добавлены в прогноз';
      if (mounted) Navigator.pop(context);
    } on ApiException catch (error) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = error.message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final result = _result;
    final ready =
        result != null &&
        List.generate(result.drafts.length, _ready).any((ok) => ok);
    return AlertDialog(
      title: Row(
        children: [
          Icon(Icons.document_scanner_outlined, color: colors.primary),
          const SizedBox(width: 10),
          const Expanded(child: Text('Документ → прогноз')),
        ],
      ),
      content: SizedBox(
        width: 560,
        child: result == null
            ? _error != null
                  ? Text(
                      _error!,
                      style: golos(14, FontWeight.w600, colors.danger),
                    )
                  : Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const LinearProgressIndicator(),
                        const SizedBox(height: 12),
                        Text(
                          '«${widget.filename}»: распознаём текст (OCR) и разбираем поля с $kAiName…',
                          style: golos(14, FontWeight.w400, colors.text),
                        ),
                      ],
                    )
            : SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      '${_docName(result)} · уверенность ${formatPercent(result.confidence)} · ${result.engine}',
                      style: golos(13, FontWeight.w500, colors.textMuted),
                    ),
                    if ((result.notes ?? '').isNotEmpty) ...[
                      const SizedBox(height: 6),
                      Text(
                        result.notes!,
                        style: golos(13, FontWeight.w400, colors.textMuted),
                      ),
                    ],
                    const SizedBox(height: 12),
                    if (result.drafts.isEmpty)
                      Text(
                        'В документе не нашлось суммы к оплате.',
                        style: golos(14, FontWeight.w500, colors.text),
                      ),
                    for (var i = 0; i < result.drafts.length; i++)
                      _DraftRow(
                        draft: result.drafts[i],
                        direction: _direction[i],
                        date: _date[i],
                        asOf: ref.read(sessionProvider).asOf,
                        onDirection: (value) =>
                            setState(() => _direction[i] = value),
                        onDate: (value) => setState(() => _date[i] = value),
                      ),
                    if (result.text.isNotEmpty)
                      ExpansionTile(
                        tilePadding: EdgeInsets.zero,
                        title: Text(
                          'Распознанный текст',
                          style: golos(14, FontWeight.w600, colors.primary),
                        ),
                        children: [
                          SelectableText(
                            result.text,
                            style: mono(12, colors.textMuted),
                          ),
                        ],
                      ),
                    if (_error != null)
                      Text(
                        _error!,
                        style: golos(14, FontWeight.w600, colors.danger),
                      ),
                  ],
                ),
              ),
      ),
      actions: [
        TextButton(
          onPressed: _saving ? null : () => Navigator.pop(context),
          child: const Text('Закрыть'),
        ),
        if (result != null && result.drafts.isNotEmpty)
          FilledButton(
            key: const Key('ocr-add'),
            onPressed: ready && !_saving ? _add : null,
            child: Text(_saving ? 'Добавляем…' : 'Добавить в прогноз'),
          ),
      ],
    );
  }

  static String _docName(AiOcrResult result) {
    final name = switch (result.docType) {
      'invoice' => 'Счёт',
      'contract' => 'Договор',
      'act' => 'Акт',
      'upd' => 'УПД',
      'payment_order' => 'Платёжное поручение',
      _ => 'Документ',
    };
    return result.number == null ? name : '$name №${result.number}';
  }
}

class _DraftRow extends StatelessWidget {
  const _DraftRow({
    required this.draft,
    required this.direction,
    required this.date,
    required this.asOf,
    required this.onDirection,
    required this.onDate,
  });

  final AiDraft draft;
  final String? direction;
  final DateTime? date;
  final DateTime? asOf;
  final ValueChanged<String> onDirection;
  final ValueChanged<DateTime> onDate;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        border: Border.all(color: colors.divider),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(draft.label, style: golos(14, FontWeight.w600, colors.text)),
          const SizedBox(height: 4),
          Text(
            formatMoneyKopecks(draft.amountMinor),
            style: golos(18, FontWeight.w700, colors.text),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'in', label: Text('Поступление')),
                  ButtonSegment(value: 'out', label: Text('Оплата')),
                ],
                emptySelectionAllowed: true,
                selected: {?direction},
                onSelectionChanged: (value) {
                  if (value.isNotEmpty) onDirection(value.first);
                },
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.event, size: 18),
                label: Text(
                  date == null
                      ? 'Срок не найден — выбрать'
                      : formatDateDot(date!),
                ),
                onPressed: () async {
                  final start = asOf ?? DateTime.now();
                  final picked = await showDatePicker(
                    context: context,
                    firstDate: start,
                    lastDate: start.add(const Duration(days: 400)),
                    initialDate: date ?? start.add(const Duration(days: 14)),
                  );
                  if (picked != null) onDate(dateOnly(picked));
                },
              ),
            ],
          ),
        ],
      ),
    );
  }
}
