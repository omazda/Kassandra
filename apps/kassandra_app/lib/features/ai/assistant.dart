import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../api/ai.dart';
import '../../api/errors.dart';
import '../../api/models.dart';
import '../../app/config.dart';
import '../../app/providers.dart';
import '../../design/tokens.dart';
import 'markdown.dart';

/// Chat with Kassandra (the LLM with tools). The server runs the tools — forecast, calendar,
/// scenarios, adding payments — and returns what the screen should do next.
class ChatEntry {
  const ChatEntry({
    required this.user,
    required this.text,
    this.actions = const [],
    this.reasoning = '',
    this.error = false,
    this.seconds,
  });

  final bool user;
  final String text;
  final List<AiAction> actions;
  final String reasoning;
  final bool error;

  /// How long the model worked on this answer.
  final double? seconds;
}

class AssistantState {
  const AssistantState({
    this.entries = const [],
    this.busy = false,
    this.open = false,
    this.startedAt,
    this.live,
  });

  final List<ChatEntry> entries;
  final bool busy;
  final bool open;

  /// When the running answer started: the panel ticks the thinking time from it.
  final DateTime? startedAt;

  /// Stage, tools and thinking of the running answer, as the server reports them.
  final AiChatProgress? live;

  AssistantState copyWith({
    List<ChatEntry>? entries,
    bool? busy,
    bool? open,
    DateTime? startedAt,
    AiChatProgress? live,
    bool clearLive = false,
  }) => AssistantState(
    entries: entries ?? this.entries,
    busy: busy ?? this.busy,
    open: open ?? this.open,
    startedAt: clearLive ? null : startedAt ?? this.startedAt,
    live: clearLive ? null : live ?? this.live,
  );
}

class AssistantNotifier extends Notifier<AssistantState> {
  @override
  AssistantState build() => const AssistantState();

  void setOpen(bool open) => state = state.copyWith(open: open);

  void clear() => state = state.copyWith(entries: const []);

  /// Sends the question with the screen context; returns the reply for the UI actions.
  /// The server works in the background; polling shows the stage, tools and thinking.
  Future<AiChatReply?> send(String text, Map<String, dynamic> context) async {
    final question = text.trim();
    if (question.isEmpty || state.busy) return null;
    final entries = [...state.entries, ChatEntry(user: true, text: question)];
    // clearLive drops the previous answer's progress; the timer starts right after.
    state = state.copyWith(entries: entries, busy: true, clearLive: true);
    state = state.copyWith(startedAt: DateTime.now());
    final history = [
      for (final entry in entries)
        if (!entry.error)
          {'role': entry.user ? 'user' : 'assistant', 'content': entry.text},
    ];
    final api = ref.read(apiProvider);
    try {
      final job = await api.aiChatStart(history, context);
      while (true) {
        await Future<void>.delayed(const Duration(milliseconds: 700));
        final progress = await api.aiChatProgress(job);
        if (progress.status == 'running') {
          state = state.copyWith(live: progress);
          continue;
        }
        if (progress.status == 'error' || progress.result == null) {
          throw ApiFailure(progress.error ?? 'Ассистент не ответил');
        }
        final reply = progress.result!;
        state = state.copyWith(
          busy: false,
          clearLive: true,
          entries: [
            ...state.entries,
            ChatEntry(
              user: false,
              text: reply.reply,
              actions: reply.actions,
              reasoning: reply.reasoning,
              seconds: progress.elapsed,
            ),
          ],
        );
        return reply;
      }
    } on ApiException catch (error) {
      state = state.copyWith(
        busy: false,
        clearLive: true,
        entries: [
          ...state.entries,
          ChatEntry(user: false, text: error.message, error: true),
        ],
      );
      return null;
    }
  }
}

final assistantProvider = NotifierProvider<AssistantNotifier, AssistantState>(
  AssistantNotifier.new,
);

const _suggestions = [
  'Когда будет кассовый разрыв?',
  'Что сделать, чтобы избежать разрыва?',
  'Покажи платежи на ближайшую неделю',
  'Что будет, если выручка упадёт на 20%?',
  'Проверь прогноз с ИИ и дай рекомендации',
];

/// Opens the chat: a side panel on wide screens, a full-height sheet on phones.
Future<void> openAssistant(BuildContext context, WidgetRef ref) async {
  if (!isPhone(context)) {
    ref.read(assistantProvider.notifier).setOpen(true);
    return;
  }
  await showModalBottomSheet<void>(
    context: context,
    useRootNavigator: true,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (sheet) => FractionallySizedBox(
      heightFactor: 0.94,
      child: AssistantPanel(onClose: () => Navigator.pop(sheet), sheet: true),
    ),
  );
}

class AssistantLauncher extends ConsumerWidget {
  const AssistantLauncher({super.key, this.compact = false});
  final bool compact;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    if (compact) {
      return FloatingActionButton.small(
        key: const Key('assistant-open'),
        heroTag: 'assistant',
        tooltip: 'Спросить Кассандру',
        backgroundColor: colors.ink,
        foregroundColor: Colors.white,
        onPressed: () => openAssistant(context, ref),
        child: const Icon(Icons.auto_awesome),
      );
    }
    return FloatingActionButton.extended(
      key: const Key('assistant-open'),
      heroTag: 'assistant',
      backgroundColor: colors.ink,
      foregroundColor: Colors.white,
      onPressed: () => openAssistant(context, ref),
      icon: const Icon(Icons.auto_awesome),
      label: const Text('Спросить Кассандру'),
    );
  }
}

class AssistantPanel extends ConsumerStatefulWidget {
  const AssistantPanel({super.key, required this.onClose, this.sheet = false});
  final VoidCallback onClose;
  final bool sheet;

  @override
  ConsumerState<AssistantPanel> createState() => _AssistantPanelState();
}

class _AssistantPanelState extends ConsumerState<AssistantPanel> {
  final _input = TextEditingController();
  final _scroll = ScrollController();

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Map<String, dynamic> _context() {
    final session = ref.read(sessionProvider);
    final router = GoRouter.of(context);
    return {
      'account_id': session.accountId,
      'as_of': session.asOf == null ? null : isoDate(session.asOf!),
      'model': session.model,
      'path': router.routerDelegate.currentConfiguration.uri.path,
      'run_id': ref.read(forecastBundleProvider).valueOrNull?.forecast.runId,
    };
  }

  Future<void> _send(String text) async {
    _input.clear();
    final router = GoRouter.of(context);
    final reply = await ref
        .read(assistantProvider.notifier)
        .send(text, _context());
    _toBottom();
    if (reply == null || !mounted) return;
    await _apply(reply.ui, router);
  }

  /// Account first, then data refresh, then the page: the page opens with fresh data.
  Future<void> _apply(List<Map<String, dynamic>> ui, GoRouter router) async {
    final session = ref.read(sessionProvider.notifier);
    var switched = false;
    for (final action in ui.where((a) => a['type'] == 'switch_account')) {
      final accounts = await ref.read(accountsProvider.future);
      final found = accounts.where((a) => a.id == action['account_id']);
      if (found.isEmpty) continue;
      session.applyAccount(found.first, asOf: asDateOpt(action['as_of']));
      switched = true;
    }
    if (!switched && ui.any((a) => a['type'] == 'refresh')) session.refresh();
    final pages = ui.where((a) => a['type'] == 'navigate').toList();
    final path = pages.isEmpty ? null : pages.last['path']?.toString();
    if (path != null) {
      router.go('$path${sectionQuery(ref.read(sessionProvider))}');
      if (widget.sheet && mounted) widget.onClose();
    } else if (switched) {
      final current = router.routerDelegate.currentConfiguration.uri.path;
      router.go('$current${sectionQuery(ref.read(sessionProvider))}');
    }
  }

  void _toBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final state = ref.watch(assistantProvider);
    ref.listen(assistantProvider, (previous, next) {
      if (previous?.entries.length != next.entries.length || next.busy) {
        _toBottom();
      }
    });
    return Material(
      color: colors.background,
      elevation: widget.sheet ? 0 : 12,
      child: SafeArea(
        child: Column(
          children: [
            Container(
              padding: const EdgeInsets.fromLTRB(20, 14, 8, 12),
              decoration: BoxDecoration(
                color: colors.ink,
                borderRadius: widget.sheet
                    ? const BorderRadius.vertical(top: Radius.circular(20))
                    : null,
              ),
              child: Row(
                children: [
                  const Icon(Icons.auto_awesome, color: Colors.white, size: 20),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Кассандра',
                          style: golos(17, FontWeight.w700, Colors.white),
                        ),
                        Text(
                          'ИИ-ассистент · $kAiName · сама выполняет действия',
                          style: golos(12, FontWeight.w400, colors.inkMuted),
                        ),
                      ],
                    ),
                  ),
                  if (state.entries.isNotEmpty)
                    IconButton(
                      tooltip: 'Новый разговор',
                      onPressed: state.busy
                          ? null
                          : ref.read(assistantProvider.notifier).clear,
                      icon: const Icon(Icons.refresh, color: Colors.white),
                    ),
                  IconButton(
                    key: const Key('assistant-close'),
                    tooltip: 'Закрыть',
                    onPressed: widget.onClose,
                    icon: const Icon(Icons.close, color: Colors.white),
                  ),
                ],
              ),
            ),
            Expanded(
              child: ListView(
                controller: _scroll,
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                children: [
                  if (state.entries.isEmpty) ...[
                    Text(
                      'Спросите о деньгах компании или попросите сделать что-то в сервисе: '
                      'построить прогноз, проверить сценарий, добавить платёж, открыть календарь.',
                      style: golos(14, FontWeight.w400, colors.textMuted),
                    ),
                    const SizedBox(height: 12),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final text in _suggestions)
                          ActionChip(
                            label: Text(text),
                            onPressed: state.busy ? null : () => _send(text),
                          ),
                      ],
                    ),
                  ],
                  for (final entry in state.entries) _Bubble(entry: entry),
                  if (state.busy)
                    _Thinking(startedAt: state.startedAt, live: state.live),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      key: const Key('assistant-input'),
                      controller: _input,
                      enabled: !state.busy,
                      minLines: 1,
                      maxLines: 4,
                      textInputAction: TextInputAction.send,
                      onSubmitted: _send,
                      decoration: const InputDecoration(
                        hintText:
                            'Например: добавь поступление 300 000 ₽ 15 мая',
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    key: const Key('assistant-send'),
                    tooltip: 'Отправить',
                    onPressed: state.busy ? null : () => _send(_input.text),
                    icon: const Icon(Icons.arrow_upward),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// While the model works: a ticking timer, the current stage, finished steps and the
/// model's thoughts so far — the user sees that the question is being worked on.
class _Thinking extends StatefulWidget {
  const _Thinking({required this.startedAt, required this.live});
  final DateTime? startedAt;
  final AiChatProgress? live;

  @override
  State<_Thinking> createState() => _ThinkingState();
}

class _ThinkingState extends State<_Thinking> {
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(
      const Duration(milliseconds: 100),
      (_) => setState(() {}),
    );
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final live = widget.live;
    final seconds = widget.startedAt == null
        ? 0.0
        : DateTime.now().difference(widget.startedAt!).inMilliseconds / 1000;
    final thoughts = live?.reasoning ?? const <String>[];
    return Container(
      key: const Key('assistant-thinking'),
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: colors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: colors.primary.withValues(alpha: 0.25)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  'Кассандра думает',
                  style: golos(14, FontWeight.w600, colors.text),
                ),
              ),
              Text(
                '${seconds.toStringAsFixed(1).replaceAll('.', ',')} с',
                key: const Key('assistant-timer'),
                style: mono(13, colors.primary),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            live?.stage.isNotEmpty == true ? live!.stage : 'Читаю вопрос',
            style: golos(13, FontWeight.w500, colors.textMuted),
          ),
          if (live != null && live.actions.isNotEmpty) ...[
            const SizedBox(height: 8),
            _ActionChips(actions: live.actions),
          ],
          if (thoughts.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              'Мысли модели',
              style: golos(12, FontWeight.w600, colors.primary),
            ),
            const SizedBox(height: 2),
            Text(
              thoughts.last,
              maxLines: 4,
              overflow: TextOverflow.ellipsis,
              style: mono(11, colors.textMuted),
            ),
          ],
        ],
      ),
    );
  }
}

/// Tool calls as chips; repeated calls fold into one chip «×N» so a search loop stays short.
class _ActionChips extends StatelessWidget {
  const _ActionChips({required this.actions});
  final List<AiAction> actions;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final groups = <(String, bool), (int, String?)>{};
    for (final action in actions) {
      final key = (action.title, action.ok);
      final seen = groups[key];
      groups[key] = (seen == null ? 1 : seen.$1 + 1, action.error ?? seen?.$2);
    }
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        for (final MapEntry(key: key, value: value) in groups.entries)
          Tooltip(
            message: value.$2 ?? key.$1,
            child: Chip(
              visualDensity: VisualDensity.compact,
              avatar: Icon(
                key.$2 ? Icons.check : Icons.close,
                size: 16,
                color: key.$2 ? colors.positive : colors.danger,
              ),
              label: Text(
                value.$1 > 1 ? '${key.$1} ×${value.$1}' : key.$1,
                style: golos(12, FontWeight.w500, colors.text),
              ),
            ),
          ),
      ],
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.entry});
  final ChatEntry entry;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final user = entry.user;
    return Align(
      alignment: user ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 360),
        child: Container(
          margin: const EdgeInsets.only(bottom: 10),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: user
                ? colors.primary
                : entry.error
                ? const Color(0xFFF8E4E2)
                : colors.surface,
            borderRadius: BorderRadius.circular(16),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (entry.actions.isNotEmpty) ...[
                _ActionChips(actions: entry.actions),
                const SizedBox(height: 8),
              ],
              SimpleMarkdown(
                entry.text,
                style: golos(
                  14,
                  FontWeight.w400,
                  user ? Colors.white : colors.text,
                  height: 1.35,
                ),
              ),
              if (entry.seconds != null)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(
                    'Думала ${entry.seconds!.toStringAsFixed(1).replaceAll('.', ',')} с',
                    style: golos(11, FontWeight.w500, colors.textMuted),
                  ),
                ),
              if (entry.reasoning.isNotEmpty)
                Theme(
                  data: Theme.of(context)
                      .copyWith(dividerColor: Colors.transparent),
                  child: ExpansionTile(
                    tilePadding: EdgeInsets.zero,
                    childrenPadding: EdgeInsets.zero,
                    dense: true,
                    title: Text(
                      'Ход рассуждений',
                      style: golos(12, FontWeight.w600, colors.primary),
                    ),
                    children: [
                      SelectableText(
                        entry.reasoning,
                        style: mono(11, colors.textMuted),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
