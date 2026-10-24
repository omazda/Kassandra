import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_web_plugins/url_strategy.dart';

import 'api/token_store.dart';
import 'app/providers.dart';
import 'app/router.dart';
import 'app/theme.dart';
import 'design/tokens.dart';

Future<void> main() async {
  usePathUrlStrategy();
  WidgetsFlutterBinding.ensureInitialized();
  final tokens = await openTokenStore();
  runApp(
    ProviderScope(
      overrides: [tokenStoreProvider.overrideWithValue(tokens)],
      child: const KassandraRoot(),
    ),
  );
}

class KassandraRoot extends ConsumerWidget {
  const KassandraRoot({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);
    final mode = ref.watch(
      sessionProvider.select((session) => session.themeMode),
    );
    return MaterialApp.router(
      title: 'Kassandra',
      debugShowCheckedModeBanner: false,
      theme: buildKassandraTheme(KassandraColors.light, Brightness.light),
      darkTheme: buildKassandraTheme(KassandraColors.dark, Brightness.dark),
      themeMode: mode,
      locale: const Locale('ru'),
      supportedLocales: const [Locale('ru')],
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      routerConfig: router,
    );
  }
}
