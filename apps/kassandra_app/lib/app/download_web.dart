import 'dart:js_interop';

import 'package:web/web.dart' as web;

/// Saves [text] through a temporary link, as a browser download.
bool saveTextFile(String name, String text, {String type = 'text/plain'}) {
  final blob = web.Blob(
    [text.toJS].toJS,
    web.BlobPropertyBag(type: '$type;charset=utf-8'),
  );
  final url = web.URL.createObjectURL(blob);
  final link = web.HTMLAnchorElement()
    ..href = url
    ..download = name;
  web.document.body?.append(link);
  link.click();
  link.remove();
  web.URL.revokeObjectURL(url);
  return true;
}
