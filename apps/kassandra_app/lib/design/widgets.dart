import 'package:flutter/material.dart';

import '../api/errors.dart';
import 'icons.dart';
import 'tokens.dart';

class SurfaceCard extends StatelessWidget {
  const SurfaceCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(24),
    this.color,
    this.borderColor,
    this.radius = KRadius.card,
  });

  final Widget child;
  final EdgeInsets padding;
  final Color? color;
  final Color? borderColor;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: color ?? colors.surface,
        borderRadius: BorderRadius.circular(radius),
        border: borderColor == null ? null : Border.all(color: borderColor!),
      ),
      child: Padding(padding: padding, child: child),
    );
  }
}

class Eyebrow extends StatelessWidget {
  const Eyebrow(this.text, {super.key, required this.color, this.icon});
  final String text;
  final Color color;
  final String? icon;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        if (icon != null) ...[
          KIcon(icon!, size: 18, color: color, stroke: 2),
          const SizedBox(width: 8),
        ],
        Flexible(
          child: Text(
            text.toUpperCase(),
            style: golos(
              13,
              FontWeight.w600,
              color,
              height: 1.2,
            ).copyWith(letterSpacing: 1.04),
          ),
        ),
      ],
    );
  }
}

class ErrorPane extends StatelessWidget {
  const ErrorPane({super.key, required this.error, this.onRetry});
  final Object error;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final message = error is ApiException
        ? error.toString()
        : 'Не удалось загрузить данные';
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 460),
        child: SurfaceCard(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(message, style: golos(16, FontWeight.w600, colors.text)),
              if (error is ForbiddenException)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    'Нет доступа к этому счёту',
                    style: golos(14, FontWeight.w400, colors.textMuted),
                  ),
                ),
              if (onRetry != null) ...[
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: onRetry,
                  child: const Text('Повторить'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class EmptyHint extends StatelessWidget {
  const EmptyHint({super.key, required this.text, this.action, this.onAction});
  final String text;
  final String? action;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(text, style: golos(15, FontWeight.w400, colors.textMuted)),
          if (action != null) ...[
            const SizedBox(height: 12),
            OutlinedButton(onPressed: onAction, child: Text(action!)),
          ],
        ],
      ),
    );
  }
}

class SourceChip extends StatelessWidget {
  const SourceChip(this.label, {super.key, this.emphasis = false});
  final String label;
  final bool emphasis;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(
        color: emphasis ? colors.primaryContainer : colors.surfaceMuted,
        borderRadius: BorderRadius.circular(KRadius.chip),
      ),
      child: Text(
        label,
        style: golos(
          12,
          FontWeight.w500,
          emphasis ? colors.primary : colors.textMuted,
          height: 1.2,
        ),
      ),
    );
  }
}

class WindowSwitch extends StatelessWidget {
  const WindowSwitch({
    super.key,
    required this.labels,
    required this.index,
    required this.onChanged,
  });
  final List<String> labels;
  final int index;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.background,
        borderRadius: BorderRadius.circular(KRadius.control),
      ),
      child: Padding(
        padding: const EdgeInsets.all(4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (var i = 0; i < labels.length; i++)
              Padding(
                padding: const EdgeInsets.only(right: 4),
                child: InkWell(
                  borderRadius: BorderRadius.circular(9),
                  onTap: () => onChanged(i),
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      color: i == index ? colors.surface : Colors.transparent,
                      borderRadius: BorderRadius.circular(9),
                      boxShadow: i == index
                          ? const [
                              BoxShadow(
                                color: Color(0x1F0F2B2E),
                                blurRadius: 2,
                                offset: Offset(0, 1),
                              ),
                            ]
                          : null,
                    ),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 8,
                      ),
                      child: Text(
                        labels[i],
                        style: golos(
                          14,
                          i == index ? FontWeight.w600 : FontWeight.w400,
                          i == index ? colors.text : colors.textMuted,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
