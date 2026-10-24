import 'package:flutter/material.dart';

import '../../api/models.dart';
import '../../api/v11.dart';
import '../../api/views.dart';
import '../../design/icons.dart';
import '../../design/tokens.dart';
import '../../format/format.dart';
import 'calendar_logic.dart';

const certaintyChoices = <(String, String)>[
  ('promised', 'Обещание'),
  ('agreed', 'Договор'),
  ('documented', 'Счёт'),
];

class PaymentEditor extends StatefulWidget {
  const PaymentEditor({
    super.key,
    required this.item,
    required this.points,
    required this.knownAt,
    required this.categories,
    required this.onSave,
    required this.onDelete,
    required this.onSkip,
    this.compact = false,
  });

  final CalendarItem item;
  final List<ForecastPoint> points;
  final DateTime knownAt;
  final List<CodeLabel> categories;
  final Future<void> Function(Map<String, dynamic> changed) onSave;
  final Future<void> Function() onDelete;
  final Future<void> Function() onSkip;
  final bool compact;

  @override
  State<PaymentEditor> createState() => _PaymentEditorState();
}

class _PaymentEditorState extends State<PaymentEditor> {
  late DateTime _date;
  late String _category;
  late String _certainty;
  late TextEditingController _amount;
  late TextEditingController _name;
  late TextEditingController _label;
  var _busy = false;

  @override
  void initState() {
    super.initState();
    _reset(widget.item);
  }

  @override
  void didUpdateWidget(PaymentEditor oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.item.id != widget.item.id) _reset(widget.item);
  }

  void _reset(CalendarItem item) {
    _date = item.date;
    _category = item.category;
    _certainty = item.certainty ?? 'documented';
    if (!_ready) {
      _amount = TextEditingController(text: rublesField(item.amountMinor));
      _name = TextEditingController(text: item.counterpartyName ?? '');
      _label = TextEditingController(text: item.label);
      _ready = true;
      return;
    }
    _amount.text = rublesField(item.amountMinor);
    _name.text = item.counterpartyName ?? '';
    _label.text = item.label;
  }

  var _ready = false;

  @override
  void dispose() {
    _amount.dispose();
    _name.dispose();
    _label.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final item = widget.item;
    final preview = previewMove(
      points: widget.points,
      oldDate: item.date,
      newDate: _date,
      direction: item.direction,
      amountMinor: item.amountMinor,
    );
    final categories = [
      for (final entry in widget.categories)
        if (entry.code.isNotEmpty) entry,
    ];
    if (categories.every((entry) => entry.code != _category) &&
        _category.isNotEmpty) {
      categories.insert(
        0,
        CodeLabel(code: _category, label: item.categoryLabel),
      );
    }
    return ListView(
      padding: EdgeInsets.fromLTRB(20, widget.compact ? 8 : 20, 20, 28),
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'ИЗМЕНИТЬ ПЛАТЁЖ',
                style: golos(
                  12,
                  FontWeight.w600,
                  colors.textMuted,
                ).copyWith(letterSpacing: 0.8),
              ),
            ),
            if (widget.compact)
              IconButton(
                tooltip: 'Закрыть',
                onPressed: () => Navigator.pop(context),
                icon: const Icon(Icons.close),
              ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          item.shortTitle,
          style: unbounded(20, FontWeight.w600, colors.text),
        ),
        if ((item.counterpartyName ?? '').isNotEmpty)
          Text(
            item.counterpartyName!,
            style: golos(14, FontWeight.w400, colors.textMuted),
          ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 6,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            // Certainty is edited right below, so the header shows only the source.
            if (item.sourceShort.isNotEmpty) _QuietChip(item.sourceShort),
          ],
        ),
        const SizedBox(height: 16),
        Text(
          'Дата оплаты',
          style: golos(13, FontWeight.w500, colors.textMuted),
        ),
        const SizedBox(height: 6),
        OutlinedButton(
          key: const Key('edit-date'),
          onPressed: _pickDate,
          child: Align(
            alignment: Alignment.centerLeft,
            child: Text(formatDateDot(_date)),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          key: const Key('edit-amount'),
          controller: _amount,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(
            labelText: 'Сумма, ₽',
            border: OutlineInputBorder(),
          ),
        ),
        if (!item.assumption) ...[
          const SizedBox(height: 12),
          Text(
            'Категория',
            style: golos(13, FontWeight.w500, colors.textMuted),
          ),
          const SizedBox(height: 6),
          DropdownButtonFormField<String>(
            key: const Key('edit-category'),
            initialValue: _category.isEmpty ? null : _category,
            decoration: const InputDecoration(border: OutlineInputBorder()),
            items: [
              for (final entry in categories)
                DropdownMenuItem(value: entry.code, child: Text(entry.label)),
            ],
            onChanged: (value) {
              if (value != null) setState(() => _category = value);
            },
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _name,
            decoration: const InputDecoration(
              labelText: 'Контрагент',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          Text(
            'Насколько платёж надёжен',
            style: golos(13, FontWeight.w500, colors.textMuted),
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            children: [
              for (final choice in certaintyChoices)
                ChoiceChip(
                  label: Text(choice.$2),
                  selected: _certainty == choice.$1,
                  onSelected: (_) => setState(() => _certainty = choice.$1),
                ),
            ],
          ),
        ],
        const SizedBox(height: 12),
        TextField(
          controller: _label,
          decoration: const InputDecoration(
            labelText: 'Подпись',
            border: OutlineInputBorder(),
          ),
        ),
        if (preview != null) ...[
          const SizedBox(height: 14),
          DecoratedBox(
            decoration: BoxDecoration(
              color: colors.primaryContainer,
              borderRadius: BorderRadius.circular(16),
            ),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Если перенести на ${formatDayMonth(_date)}',
                    style: golos(14, FontWeight.w600, colors.text),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    preview.text,
                    key: const Key('move-preview'),
                    style: golos(14, FontWeight.w400, colors.text),
                  ),
                ],
              ),
            ),
          ),
        ],
        const SizedBox(height: 12),
        Text(
          item.assumption
              ? 'Ваша версия заменит ожидание из истории в прогнозе на ${formatDateFull(widget.knownAt)} и позже.'
              : 'Правка войдёт в прогноз на ${formatDateFull(widget.knownAt)} и позже. Прогноз на более раннюю дату её не увидит, история правок сохраняется.',
          style: golos(13, FontWeight.w400, colors.textMuted),
        ),
        const SizedBox(height: 16),
        Row(
          children: [
            Expanded(
              child: FilledButton(
                key: const Key('edit-save'),
                onPressed: _busy ? null : _save,
                child: const Text('Сохранить'),
              ),
            ),
            const SizedBox(width: 8),
            TextButton(
              onPressed: _busy
                  ? null
                  : () {
                      if (widget.compact) {
                        Navigator.pop(context);
                      } else {
                        _reset(item);
                        setState(() {});
                      }
                    },
              child: const Text('Отмена'),
            ),
          ],
        ),
        if (item.hasAction('delete')) ...[
          const SizedBox(height: 18),
          TextButton.icon(
            key: const Key('edit-delete'),
            onPressed: _busy ? null : _delete,
            icon: KIcon(KIcon.trash, size: 18, color: colors.danger),
            label: Text(
              'Удалить платёж',
              style: golos(15, FontWeight.w600, colors.danger),
            ),
          ),
          Text(
            'Платёж уйдёт из прогноза. Его можно будет увидеть в фильтре «Показать удалённые».',
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
        ],
        if (item.hasAction('skip')) ...[
          const SizedBox(height: 18),
          TextButton.icon(
            key: const Key('edit-skip'),
            onPressed: _busy ? null : _skip,
            icon: KIcon(KIcon.eyeOff, size: 18, color: colors.danger),
            label: Text(
              'Не учитывать этот платёж',
              style: golos(15, FontWeight.w600, colors.danger),
            ),
          ),
          Text(
            'Например, подписку отменили. Платёж уйдёт из прогноза.',
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
        ],
      ],
    );
  }

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: widget.knownAt,
      lastDate: widget.knownAt.add(const Duration(days: 370)),
    );
    if (picked == null) return;
    setState(() => _date = dateOnly(picked));
  }

  Future<void> _save() async {
    final amount = parseRublesToMinor(_amount.text);
    if (amount == null || amount <= 0) {
      _toast('Введите сумму больше нуля');
      return;
    }
    final changed = widget.item.assumption
        ? assumptionChanges(
            item: widget.item,
            date: _date,
            amountMinor: amount,
            label: _label.text,
          )
        : eventChanges(
            item: widget.item,
            date: _date,
            amountMinor: amount,
            category: _category,
            counterparty: _name.text,
            label: _label.text,
            certainty: _certainty,
          );
    if (changed.isEmpty) {
      if (widget.compact && mounted) Navigator.pop(context);
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.onSave(changed);
      if (widget.compact && mounted) Navigator.pop(context);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete() async {
    setState(() => _busy = true);
    try {
      await widget.onDelete();
      if (widget.compact && mounted) Navigator.pop(context);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _skip() async {
    setState(() => _busy = true);
    try {
      await widget.onSkip();
      if (widget.compact && mounted) Navigator.pop(context);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _toast(String text) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }
}

class _QuietChip extends StatelessWidget {
  const _QuietChip(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.surfaceMuted,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        child: Text(text, style: golos(12, FontWeight.w500, colors.textMuted)),
      ),
    );
  }
}
