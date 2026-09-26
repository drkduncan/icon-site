// Contact photo export presets.
//
// Each preset describes one place a contact photo ends up: the square pixel
// size to export, the image format, and how much padding to keep inside the
// circle the app crops to. Sizes are chosen so the target never has to
// upscale; each one downsamples to its own thumbnails from there.
//
// Works as a plain <script> (exposes window.ContactPresets) and in Node
// (module.exports) so the sizing math can be tested without a browser.
(function (root) {
  const PRESETS = [
    {
      id: 'ios',
      label: 'iOS Contacts',
      size: 1024,
      format: 'image/png',
      // iOS shows the photo as a circle that touches the square's edges.
      padding: 0.1,
      note: 'Shown as a full-width circle; iOS scales it down for lists and calls.',
    },
    {
      id: 'android',
      label: 'Android',
      size: 720,
      format: 'image/png',
      // Contacts' full-size display photo is capped at 720px; launchers and
      // dialers may mask it as a circle or squircle.
      padding: 0.1,
      note: 'Android stores display photos at up to 720x720.',
    },
    {
      id: 'google',
      label: 'Google Contacts',
      size: 720,
      format: 'image/png',
      padding: 0.1,
      note: 'Syncs to Android, so it matches the Android display photo size.',
    },
    {
      id: 'outlook',
      label: 'Outlook',
      size: 648,
      format: 'image/jpeg',
      // Microsoft 365 profile and contact photos are 648x648 JPEGs, shown in
      // a circle in current Outlook clients.
      padding: 0.1,
      note: 'Microsoft 365 photos are 648x648 JPEGs; transparency is not kept.',
    },
  ];

  function getPreset(id) {
    return PRESETS.find((p) => p.id === id) || null;
  }

  // Radius of the circle the logo must stay inside, in output pixels.
  function safeRadius(preset) {
    return (preset.size / 2) * (1 - 2 * preset.padding);
  }

  // Where to draw a logo of natural size w x h so its whole bounding box
  // (corners included) sits inside the preset's safe circle, centred.
  // `zoom` multiplies the fitted size, so 1 is the largest safe fit.
  function fitLogo(w, h, preset, zoom = 1) {
    if (!(w > 0 && h > 0)) throw new Error('Logo has no size');
    const diagonal = Math.hypot(w, h);
    const scale = ((2 * safeRadius(preset)) / diagonal) * zoom;
    const drawW = w * scale;
    const drawH = h * scale;
    return {
      x: (preset.size - drawW) / 2,
      y: (preset.size - drawH) / 2,
      width: drawW,
      height: drawH,
    };
  }

  // Draws the logo onto a new square canvas for the preset.
  // `background` is any CSS colour; JPEG presets fall back to white so a
  // transparent background does not turn black.
  function renderPreset(image, preset, { background, zoom = 1, doc = root.document } = {}) {
    const canvas = doc.createElement('canvas');
    canvas.width = preset.size;
    canvas.height = preset.size;
    const ctx = canvas.getContext('2d');
    const fill = background || (preset.format === 'image/jpeg' ? '#ffffff' : null);
    if (fill) {
      ctx.fillStyle = fill;
      ctx.fillRect(0, 0, preset.size, preset.size);
    }
    const w = image.naturalWidth || image.width;
    const h = image.naturalHeight || image.height;
    const box = fitLogo(w, h, preset, zoom);
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(image, box.x, box.y, box.width, box.height);
    return canvas;
  }

  // Resolves to a Blob and a suggested file name for the preset.
  function exportPreset(image, preset, options = {}) {
    const canvas = renderPreset(image, preset, options);
    const ext = preset.format === 'image/jpeg' ? 'jpg' : 'png';
    const name = `${options.baseName || 'contact-photo'}-${preset.id}-${preset.size}.${ext}`;
    return new Promise((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve({ blob, name }) : reject(new Error('Export failed'))),
        preset.format,
        0.92
      );
    });
  }

  const api = { PRESETS, getPreset, safeRadius, fitLogo, renderPreset, exportPreset };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ContactPresets = api;
})(typeof window !== 'undefined' ? window : globalThis);
