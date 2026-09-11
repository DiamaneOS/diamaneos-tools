# Browser icon dependency

`lucide-1.8.0.min.js` is the unmodified UMD runtime from the `lucide` npm package,
version 1.8.0 (`dist/umd/lucide.min.js`). The adjacent `lucide-LICENSE.txt`
preserves its ISC licence and the MIT notice for icons derived from Feather.
The optional development source map is not required or included.

Source: [Lucide project](https://github.com/lucide-icons/lucide),
[versioned package](https://www.npmjs.com/package/lucide/v/1.8.0).

The standalone study loads this file by relative path. Keep this assets folder
beside the HTML when copying the study. No CDN or network request is needed.
The host-fragment exporter omits this loader; the preview host supplies its
own Lucide runtime. These are prototype icons, not a native Android dependency.
