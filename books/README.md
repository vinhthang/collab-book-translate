# Books Catalog

This directory contains the submitted source books ingested into the collaborative translation pipeline.

## Directory Structure

```text
books/<slug>/
├── manifest.json          # Book metadata, licensing, and language pair definition
└── source/                # Single source file (≤ 50 MB, or Git LFS)
```

## Manifest Schema (`manifest.json`)

Each book requires a valid `manifest.json` with the following mandatory fields:

```json
{
  "slug": "cixin-liu-the-dark-forest",
  "title": "The Dark Forest",
  "author": "Liu Cixin",
  "source_lang": "en",
  "target_lang": "vi",
  "license": "CC-BY-4.0",
  "license_url": "https://creativecommons.org/licenses/by/4.0/",
  "submitter": "github-username"
}
```

### Supported Language Pairs
- `en` → `vi` (English to Vietnamese)
- `zh` → `vi` (Chinese to Vietnamese)

### Allowed Licences
Submissions must be strictly open or public-domain:
- `public-domain`
- `CC0-1.0`
- `CC-BY-4.0`
- `CC-BY-SA-4.0`

For full submission guidelines and review process, see [CONTRIBUTING.md](../CONTRIBUTING.md).
