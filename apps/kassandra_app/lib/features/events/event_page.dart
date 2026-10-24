import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/config.dart';
import '../ai/ocr_dialog.dart';
import '../../api/errors.dart';
import '../../api/models.dart';
import '../../app/providers.dart';
import '../../design/icons.dart';
import '../../design/tokens.dart';
import '../../format/format.dart';
import 'event_preview.dart';

class EventPage extends ConsumerStatefulWidget {
  const EventPage({super.key});

  @override
  ConsumerState<EventPage> createState() => _EventPageState();
}

class _EventPageState extends ConsumerState<EventPage> {
  late final String _id = 'manual-${newId()}';
  var _inflow = true;
  final _phrase = TextEditingController();
  final _amount = TextEditingController(text: '300 000');
  final _date = TextEditingController();
  final _who = TextEditingController();
  final _note = TextEditingController(text: 'Клиент обещал заплатить 20-го');
  var _certainty = 'promised';
  String? _parseNote;
  String? _amountError;
  String? _dateError;
  String? _formError;
  var _saving = false;

  @override
  void dispose() {
    _phrase.dispose();
    _amount.dispose();
    _date.dispose();
    _who.dispose();
    _note.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final bundle = ref.watch(forecastBundleProvider);
    final phone = isPhone(context);
    final colors = KassandraColors.of(context);
    return bundle.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, _) => Center(child: Text(error.toString())),
      data: (data) {
        final preview = _preview(data.forecast);
        final body = ListView(
          key: const Key('page-scroll'),
          padding: phone
              ? const EdgeInsets.fromLTRB(20, 14, 20, 100)
              : const EdgeInsets.fromLTRB(40, 32, 40, 48),
          children: [
            Row(
              children: [
                IconButton(
                  tooltip: 'Закрыть',
                  onPressed: () => context.go(
                    '/forecast${sectionQuery(ref.read(sessionProvider))}',
                  ),
                  icon: const KIcon(KIcon.close),
                ),
                Expanded(
                  child: Text(
                    'Новое событие',
                    style: golos(phone ? 17 : 28, FontWeight.w600, colors.text),
                  ),
                ),
                if (!kUseFixtures)
                  TextButton.icon(
                    key: const Key('event-ocr'),
                    onPressed: () => showOcrDialog(context, ref),
                    icon: const Icon(Icons.document_scanner_outlined, size: 18),
                    label: const Text('Из документа'),
                  )
                else
                  Text(
                    'Ручной ввод',
                    style: golos(13, FontWeight.w400, colors.textMuted),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('event-phrase'),
              controller: _phrase,
              decoration: InputDecoration(
                labelText: 'Опишите словами',
                hintText: 'Клиент обещал заплатить 300 000 рублей 20-го',
                helperText: _parseNote,
              ),
              onSubmitted: (_) => _parse(data.forecast.asOf),
            ),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton(
                onPressed: () => _parse(data.forecast.asOf),
                child: const Text('Заполнить форму'),
              ),
            ),
            DecoratedBox(
              decoration: BoxDecoration(
                color: colors.divider,
                borderRadius: BorderRadius.circular(14),
              ),
              child: Padding(
                padding: const EdgeInsets.all(4),
                child: Row(
                  children: [
                    Expanded(
                      child: _seg(
                        'Поступление',
                        _inflow,
                        colors.positive,
                        () => setState(() => _inflow = true),
                      ),
                    ),
                    Expanded(
                      child: _seg(
                        'Списание',
                        !_inflow,
                        colors.danger,
                        () => setState(() => _inflow = false),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('event-amount'),
              controller: _amount,
              keyboardType: TextInputType.number,
              style: unbounded(24, FontWeight.w600, colors.text, tabular: true),
              decoration: InputDecoration(
                labelText: 'Сумма',
                errorText: _amountError,
              ),
              onChanged: (_) => setState(() => _amountError = null),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('event-date'),
              controller: _date,
              decoration: InputDecoration(
                labelText: 'Когда ждём',
                hintText: '20.05.2026',
                errorText: _dateError,
              ),
              onChanged: (_) => setState(() => _dateError = null),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: ValueKey(_certainty),
              initialValue: _certainty,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Уверенность'),
              items: const [
                DropdownMenuItem(
                  value: 'promised',
                  child: Text('Клиент обещал'),
                ),
                DropdownMenuItem(
                  value: 'agreed',
                  child: Text('Подписан договор'),
                ),
                DropdownMenuItem(
                  value: 'documented',
                  child: Text('Выставлен счёт'),
                ),
              ],
              onChanged: (value) =>
                  setState(() => _certainty = value ?? 'promised'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _who,
              decoration: const InputDecoration(
                labelText: 'От кого',
                hintText: 'Название или ИНН контрагента',
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _note,
              decoration: const InputDecoration(labelText: 'Комментарий'),
            ),
            const SizedBox(height: 12),
            _PreviewCard(forecast: data.forecast, preview: preview),
            if (_formError != null) ...[
              const SizedBox(height: 8),
              Text(
                _formError!,
                style: golos(14, FontWeight.w500, colors.danger),
              ),
            ],
            const SizedBox(height: 16),
            FilledButton(
              onPressed: _saving ? null : () => _save(data.forecast),
              child: Text(_saving ? 'Сохраняем…' : 'Добавить в прогноз'),
            ),
          ],
        );
        if (phone) return body;
        return Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 560),
            child: body,
          ),
        );
      },
    );
  }

  Widget _seg(String label, bool selected, Color color, VoidCallback onTap) {
    final colors = KassandraColors.of(context);
    return Padding(
      padding: const EdgeInsets.all(2),
      child: Material(
        color: selected ? colors.surface : Colors.transparent,
        borderRadius: BorderRadius.circular(11),
        elevation: selected ? 1 : 0,
        child: InkWell(
          borderRadius: BorderRadius.circular(11),
          onTap: onTap,
          child: SizedBox(
            height: 44,
            child: Center(
              child: Text(
                label,
                style: golos(
                  15,
                  FontWeight.w600,
                  selected ? color : colors.textMuted,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  BalancePreview? _preview(ForecastResult forecast) {
    final amount = parseRublesToMinor(_amount.text);
    final date = parseLooseDate(_date.text);
    if (amount == null || amount <= 0 || date == null) return null;
    return previewEvent(
      points: forecast.points,
      openingMinor: forecast.openingBalanceMinor,
      thresholdMinor: forecast.risk.thresholdMinor,
      expectedDate: date,
      signedMinor: _inflow ? amount : -amount,
    );
  }

  Future<void> _parse(DateTime asOf) async {
    final text = _phrase.text.trim();
    if (text.isEmpty) {
      setState(() => _parseNote = 'Напишите фразу, например сумму и дату');
      return;
    }
    try {
      final draft = await ref.read(apiProvider).parseEvent(text, asOf);
      if (!mounted) return;
      setState(() {
        if (draft.direction == 'out') _inflow = false;
        if (draft.direction == 'in') _inflow = true;
        if (draft.amountMinor != null) {
          _amount.text = formatMoneyExact(draft.amountMinor!)
              .replaceAll('₽', '')
              .trim();
        }
        if (draft.expectedDate != null) {
          _date.text = formatDateDot(draft.expectedDate!);
        }
        if (draft.certainty != null) _certainty = draft.certainty!;
        if (draft.label != null && draft.label!.isNotEmpty) {
          _note.text = draft.label!;
        }
        if (draft.counterpartyName != null &&
            draft.counterpartyName != 'null') {
          _who.text = draft.counterpartyName!;
        }
        final missing = draft.missing.isEmpty
            ? ''
            : ' Не хватает: ${draft.missing.join(', ')}.';
        _parseNote = '${draft.notes.join(' ')}$missing'.trim();
        _dateError = draft.missing.contains('expected_date')
            ? 'Укажите дату'
            : null;
        _amountError = draft.missing.contains('amount_minor')
            ? 'Укажите сумму'
            : null;
      });
    } on ApiException catch (error) {
      setState(() => _parseNote = error.message);
    }
  }

  Future<void> _save(ForecastResult forecast) async {
    final amount = parseRublesToMinor(_amount.text);
    final date = parseLooseDate(_date.text);
    setState(() {
      _amountError = amount == null || amount <= 0
          ? 'Укажите сумму больше нуля'
          : null;
      _dateError = date == null ? 'Укажите дату' : null;
      _formError = null;
    });
    if (_amountError != null ||
        _dateError != null ||
        date == null ||
        amount == null) {
      return;
    }
    final who = _who.text.trim();
    final inn = RegExp(r'^\d{10}$|^\d{12}$').hasMatch(who) ? who : null;
    final event = PlannedEvent(
      id: _id,
      accountId: forecast.accountId,
      componentId: 'manual',
      category: _inflow ? 'revenue' : 'operating_expense',
      amountMinor: amount,
      direction: _inflow ? 'in' : 'out',
      dueDate: date,
      expectedDate: date,
      knownAt: forecast.asOf,
      source: 'manual',
      status: 'confirmed',
      certainty: _certainty,
      decision: 'additional',
      label: _note.text.trim().isEmpty ? 'Плановое событие' : _note.text.trim(),
      counterpartyName: inn == null && who.isNotEmpty ? who : null,
      counterpartyInn: inn,
    );
    setState(() => _saving = true);
    try {
      await ref.read(apiProvider).createEvent(event, baseRunId: forecast.runId);
      ref.read(sessionProvider.notifier).refresh();
      if (!mounted) return;
      context.go('/forecast${sectionQuery(ref.read(sessionProvider))}');
    } on ValidationException catch (error) {
      setState(() {
        _saving = false;
        _formError = error.message;
        _amountError = error.fields['amount_minor'];
        _dateError = error.fields['expected_date'] ?? error.fields['due_date'];
      });
    } catch (error) {
      setState(() {
        _saving = false;
        _formError = error is ApiException
            ? error.message
            : 'Не удалось сохранить событие';
      });
    }
  }
}

class _PreviewCard extends StatelessWidget {
  const _PreviewCard({required this.forecast, required this.preview});
  final ForecastResult forecast;
  final BalancePreview? preview;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final baseGap = forecast.risk.firstGapDate;
    return Container(
      key: const Key('event-preview'),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: colors.positiveContainer,
        borderRadius: BorderRadius.circular(KRadius.cardSmall),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Как изменится прогноз',
            style: golos(15, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 10),
          if (preview == null)
            Text(
              'Укажите сумму и дату — здесь появится новая дата разрыва.',
              style: golos(13, FontWeight.w400, colors.onPositiveContainer),
            )
          else ...[
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Разрыв',
                        style: golos(
                          12,
                          FontWeight.w400,
                          colors.onPositiveContainer,
                        ),
                      ),
                      Text(
                        preview!.gapDate == null
                            ? 'нет'
                            : formatDateShort(preview!.gapDate!),
                        key: const Key('event-preview-gap'),
                        style: unbounded(
                          18,
                          FontWeight.w600,
                          colors.text,
                          tracking: 0,
                        ),
                      ),
                      Text(
                        'было ${baseGap == null ? '—' : formatDateShort(baseGap)}',
                        style: golos(
                          12,
                          FontWeight.w400,
                          colors.onPositiveContainer,
                        ),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Не хватит',
                        style: golos(
                          12,
                          FontWeight.w400,
                          colors.onPositiveContainer,
                        ),
                      ),
                      Text(
                        formatMoneyShort(preview!.liquidityNeedMinor),
                        style: unbounded(
                          18,
                          FontWeight.w600,
                          colors.text,
                          tabular: true,
                          tracking: 0,
                        ),
                      ),
                      Text(
                        'было ${formatMoneyShort(forecast.risk.liquidityNeedMinor)}',
                        style: golos(
                          12,
                          FontWeight.w400,
                          colors.onPositiveContainer,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            if (!preview!.insideHorizon)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  'Дата за пределами 30 дней: на остаток горизонта она не влияет.',
                  style: golos(13, FontWeight.w400, colors.onPositiveContainer),
                ),
              ),
            const SizedBox(height: 8),
            Text(
              'Событие будет видно в календаре с пометкой «ручной ввод». Когда деньги придут, Kassandra сверит его с выпиской.',
              style: golos(13, FontWeight.w400, colors.onPositiveContainer),
            ),
          ],
        ],
      ),
    );
  }
}
