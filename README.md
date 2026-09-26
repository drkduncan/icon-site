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

## Running in a container behind SWAG

The page still works opened straight from disk. Run as a container, it also gets a small server (`server/app.py`, Python standard library only) that:

- fetches remote logos server-side at `api/logo?url=...`, so "Load Image from URL" and `?src=` never hit cross-origin errors and the export always works. The proxy only allows http/https on ports 80 and 443, refuses hosts that resolve to private, loopback, link-local or reserved addresses (checked on every redirect, and the connection is pinned to the checked address), only returns images, and caps them at 5 MB.
- adds a **Save and Get Link** button that stores the current export in `/data/photos` and shows a public URL such as `https://icons.example.com/photos/acme.png` to use in Google Contacts. Give it a link name and re-saving keeps the same URL; leave it blank and the name comes from the image's hash.

Run it:

```sh
cp docker-compose.example.yml docker-compose.yml   # adjust the network to SWAG's
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d
```

The compose file pulls `ghcr.io/drkduncan/icon-site:latest`, which the `Release image` workflow builds for amd64 and arm64 every time a GitHub release is published. A release tagged `v1.2.3` is also pushed as `1.2.3` and `v1.2.3`, and prereleases never move `latest`. Update with `docker compose pull && docker compose up -d`. To build from a checkout instead, swap `image:` for `build: .` and run `docker compose up -d --build`.

Then copy one of the SWAG samples into `/config/nginx/proxy-confs/` and restart SWAG:

- `swag/icon-site.subdomain.conf.sample` serves `https://icons.<your-domain>/`
- `swag/icon-site.subfolder.conf.sample` serves `https://<your-domain>/icons/`

Both keep `/photos/` public and have commented-out auth includes (Authelia, Authentik, LDAP, basic auth) for the editor and API. Turning one on is recommended so only you can save photos or use the logo proxy.

Settings (environment variables): `PUBLIC_URL` forces the base of saved links (otherwise built from SWAG's `X-Forwarded-*` headers), `MAX_LOGO_BYTES` and `MAX_PHOTO_BYTES` change the 5 MB limits, and `BASE_PATH` is for proxies that pass the subfolder through unstripped. Run the server tests with `python -m unittest discover server`.
