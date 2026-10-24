import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/providers.dart';
import '../../design/logo.dart';
import '../../design/tokens.dart';

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _controller = TextEditingController();
  var _busy = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const KassandraLogo(fontSize: 28, onDark: false),
                const SizedBox(height: 24),
                Text(
                  'Вход',
                  style: unbounded(30, FontWeight.w600, colors.text),
                ),
                const SizedBox(height: 8),
                Text(
                  'Токен не сохраняется в сборке. На телефоне и компьютере он лежит в защищённом хранилище, в браузере — только пока открыта вкладка.',
                  style: golos(14, FontWeight.w400, colors.textMuted),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _controller,
                  obscureText: true,
                  decoration: const InputDecoration(labelText: 'Токен'),
                  onSubmitted: (_) => _submit(),
                ),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _busy ? null : _submit,
                  child: const Text('Продолжить'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _submit() async {
    final token = _controller.text.trim();
    if (token.isEmpty) return;
    setState(() => _busy = true);
    await ref.read(tokenStoreProvider).save(token);
    ref.read(needsLoginProvider.notifier).state = false;
    ref.invalidate(accountsProvider);
    if (!mounted) return;
    context.go('/forecast');
  }
}
