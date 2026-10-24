import 'package:flutter/material.dart';

/// Just enough Markdown for LLM replies: paragraphs, «- » and «1. » lists, **bold**.
class SimpleMarkdown extends StatelessWidget {
  const SimpleMarkdown(this.text, {super.key, required this.style});
  final String text;
  final TextStyle style;

  @override
  Widget build(BuildContext context) {
    final blocks = <Widget>[];
    for (final raw in text.trim().split('\n')) {
      final line = raw.trimRight();
      if (line.trim().isEmpty) {
        if (blocks.isNotEmpty) blocks.add(const SizedBox(height: 6));
        continue;
      }
      final bullet = RegExp(r'^\s*(?:[-*•]|\d+[.)])\s+').firstMatch(line);
      final heading = RegExp(r'^#{1,4}\s+').firstMatch(line);
      if (bullet != null) {
        final marker = line.substring(0, bullet.end).trim();
        blocks.add(
          Padding(
            padding: const EdgeInsets.only(left: 4, top: 2),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 20,
                  child: Text(
                    RegExp(r'^\d').hasMatch(marker) ? marker : '•',
                    style: style,
                  ),
                ),
                Expanded(child: _inline(line.substring(bullet.end))),
              ],
            ),
          ),
        );
      } else if (heading != null) {
        blocks.add(
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: _inline(
              line.substring(heading.end),
              base: style.copyWith(fontWeight: FontWeight.w700),
            ),
          ),
        );
      } else {
        blocks.add(_inline(line));
      }
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: blocks,
    );
  }

  Widget _inline(String line, {TextStyle? base}) {
    final spans = <TextSpan>[];
    final parts = line.split('**');
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].isEmpty) continue;
      spans.add(
        TextSpan(
          text: parts[i].replaceAll('`', ''),
          style: i.isOdd ? const TextStyle(fontWeight: FontWeight.w700) : null,
        ),
      );
    }
    return Text.rich(TextSpan(style: base ?? style, children: spans));
  }
}
