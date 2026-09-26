# icon-site
Site for creating contact photos from logos

## Contact photo presets

`presets.js` holds export presets for common contact photo targets. Every preset is a square, and the logo is scaled so its whole bounding box stays inside a circle with 10% padding, so circular crops never clip it.

| Preset | Size | Format |
| --- | --- | --- |
| iOS Contacts | 1024 x 1024 | PNG |
| Android | 720 x 720 | PNG |
| Google Contacts | 720 x 720 | PNG |
| Outlook | 648 x 648 | JPEG |

The page's "Export for" menu picks the preset, and the preview uses the same padding as the export. From code, load `<script src="presets.js"></script>` and call `ContactPresets.exportPreset(img, ContactPresets.getPreset('ios'), { background: '#fff' })`, which resolves to `{ blob, name }`. Run the tests with `node --test`.
