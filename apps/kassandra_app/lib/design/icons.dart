import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

/// Contour icons, 24×24, stroke 1.8, round caps — the same paths as the mock.
class KIcon extends StatelessWidget {
  const KIcon(
    this.name, {
    super.key,
    this.size = 22,
    this.color,
    this.stroke = 1.8,
  });

  final String name;
  final double size;
  final Color? color;
  final double stroke;

  static const trend = 'trend';
  static const split = 'split';
  static const calendar = 'calendar';
  static const list = 'list';
  static const bars = 'bars';
  static const download = 'download';
  static const alert = 'alert';
  static const arrowUp = 'arrowUp';
  static const bank = 'bank';
  static const vault = 'vault';
  static const plus = 'plus';
  static const bell = 'bell';
  static const chevron = 'chevron';
  static const back = 'back';
  static const close = 'close';
  static const more = 'more';
  static const settings = 'settings';
  static const flask = 'flask';
  static const pencil = 'pencil';
  static const trash = 'trash';
  static const eyeOff = 'eyeOff';
  static const search = 'search';
  static const filter = 'filter';
  static const arrowDownLeft = 'arrowDownLeft';
  static const arrowUpRight = 'arrowUpRight';

  static const _paths = {
    trend: '<path d="M3 17l5-5 4 3 8-8"/><path d="M14 7h6v6"/>',
    split: '<path d="M6 3v12"/><path d="M18 9v12"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><path d="M9 18h3a6 6 0 0 0 6-6"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18"/><path d="M8 3v4"/><path d="M16 3v4"/>',
    list: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h10"/>',
    bars: '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20H2"/>',
    download:
        '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 21h16"/>',
    alert: '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    arrowUp: '<path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>',
    bank: '<path d="M3 10l9-6 9 6"/><path d="M5 10v9"/><path d="M19 10v9"/><path d="M9 19v-5h6v5"/><path d="M3 21h18"/>',
    vault: '<rect x="4" y="3" width="16" height="18" rx="2"/><circle cx="12" cy="12" r="3"/><path d="M12 9v-2"/>',
    plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    chevron: '<path d="M9 6l6 6-6 6"/>',
    back: '<path d="M15 18l-6-6 6-6"/>',
    close: '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    flask: '<path d="M9 3h6"/><path d="M10 3v6l-5.5 9.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3"/><path d="M7.5 15h9"/>',
    pencil: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>',
    trash:
        '<path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="M7 7l1 13h8l1-13"/>',
    eyeOff: '<path d="M3 3l18 18"/><path d="M10.6 10.6a2 2 0 0 0 2.8 2.8"/><path d="M9.9 5.1A9 9 0 0 1 12 5c5 0 9 4.5 10 7a11 11 0 0 1-2.3 3.3"/><path d="M6.1 6.1C4 7.5 2.5 9.5 2 12c1 2.5 5 7 10 7 1.2 0 2.3-.2 3.3-.6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    filter: '<path d="M4 5h16l-6 7v6l-4 2v-8z"/>',
    arrowDownLeft: '<path d="M17 7L7 17"/><path d="M17 17H7V7"/>',
    arrowUpRight: '<path d="M7 17L17 7"/><path d="M7 7h10v10"/>',
  };

  @override
  Widget build(BuildContext context) {
    final paint =
        color ??
        IconTheme.of(context).color ??
        Theme.of(context).colorScheme.onSurface;
    final hex = paint.toARGB32().toRadixString(16).padLeft(8, '0').substring(2);
    final body = _paths[name] ?? _paths[list]!;
    final svg =
        '''
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#$hex" stroke-width="$stroke" stroke-linecap="round" stroke-linejoin="round">$body</svg>
''';
    return SvgPicture.string(svg, width: size, height: size);
  }
}
