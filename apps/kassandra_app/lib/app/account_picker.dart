import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../design/tokens.dart';
import '../format/format.dart';
import 'providers.dart';

/// Picks another company and reopens the current screen for it.
/// 150 companies do not fit a plain sheet: the list scrolls and filters by name or id.
Future<void> pickAccount(BuildContext context, WidgetRef ref) async {
  final accounts = [
    for (final account in await ref.read(accountsProvider.future))
      if (!account.debug) account,
  ];
  if (!context.mounted) return;
  final current = ref.read(sessionProvider).accountId;
  final Account? picked;
  if (isPhone(context)) {
    picked = await showModalBottomSheet<Account>(
      context: context,
      // Above the shell: the bottom bar and the «+» button must not cover the list.
      useRootNavigator: true,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.85,
        maxChildSize: 0.95,
        builder: (context, controller) => _AccountList(
          accounts: accounts,
          current: current,
          controller: controller,
        ),
      ),
    );
  } else {
    picked = await showDialog<Account>(
      context: context,
      builder: (context) => Dialog(
        insetPadding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560, maxHeight: 680),
          child: Padding(
            padding: const EdgeInsets.only(top: 20),
            child: _AccountList(accounts: accounts, current: current),
          ),
        ),
      ),
    );
  }
  if (picked == null || picked.id == current || !context.mounted) return;
  ref.read(sessionProvider.notifier).applyAccount(picked);
  // Stay on the same screen; a half-filled new event belongs to the old company.
  final path = GoRouterState.of(context).uri.path;
  final target = path.startsWith('/events/new') || path == '/'
      ? '/forecast'
      : path;
  context.go('$target${sectionQuery(ref.read(sessionProvider))}');
}

class _AccountList extends StatefulWidget {
  const _AccountList({
    required this.accounts,
    required this.current,
    this.controller,
  });

  final List<Account> accounts;
  final String? current;
  final ScrollController? controller;

  @override
  State<_AccountList> createState() => _AccountListState();
}

class _AccountListState extends State<_AccountList> {
  var _query = '';

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final query = _query.trim().toLowerCase();
    final shown = [
      for (final account in widget.accounts)
        if (query.isEmpty ||
            account.name.toLowerCase().contains(query) ||
            account.id.toLowerCase().contains(query))
          account,
    ];
    return Column(
      // The dialog shrinks to a short search result instead of keeping 680 px.
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Text(
            'Сменить счёт',
            style: golos(20, FontWeight.w600, colors.text),
          ),
        ),
        const SizedBox(height: 12),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: TextField(
            key: const Key('account-search'),
            autofocus: !isPhone(context),
            onChanged: (value) => setState(() => _query = value),
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.search),
              hintText: 'Название или номер, например C042',
            ),
          ),
        ),
        const SizedBox(height: 8),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Text(
            query.isEmpty
                ? '${widget.accounts.length} ${countNoun(widget.accounts.length, 'компания', 'компании', 'компаний')}'
                : 'Найдено: ${shown.length}',
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
        ),
        const SizedBox(height: 4),
        Flexible(
          child: shown.isEmpty
              ? Padding(
                  padding: const EdgeInsets.all(20),
                  child: Text(
                    'Ничего не нашлось',
                    style: golos(15, FontWeight.w400, colors.textMuted),
                  ),
                )
              : ListView.builder(
                  controller: widget.controller,
                  shrinkWrap: widget.controller == null,
                  padding: const EdgeInsets.only(bottom: 12),
                  itemCount: shown.length,
                  itemBuilder: (context, index) {
                    final account = shown[index];
                    final selected = account.id == widget.current;
                    final period =
                        account.firstDate == null || account.lastDate == null
                        ? ''
                        : ' · ${formatDateDot(account.firstDate!)} — ${formatDateDot(account.lastDate!)}';
                    return ListTile(
                      key: Key('account-${account.id}'),
                      selected: selected,
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 20,
                      ),
                      title: Text(account.name),
                      subtitle: Text('${account.id}$period'),
                      trailing: selected ? const Icon(Icons.check) : null,
                      onTap: () => Navigator.pop(context, account),
                    );
                  },
                ),
        ),
      ],
    );
  }
}
