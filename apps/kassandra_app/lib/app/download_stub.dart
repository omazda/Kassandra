/// Outside the browser there is nowhere to save a file: the caller shows the text instead.
bool saveTextFile(String name, String text, {String type = 'text/plain'}) =>
    false;
