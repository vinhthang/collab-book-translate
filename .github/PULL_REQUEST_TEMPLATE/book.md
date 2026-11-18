## Book submission

- **Slug:** <book-slug>
- **Title:** <book title>
- **Author:** <author>
- **Source language:** <en|zh>
- **Target language:** <vi>
- **License:** <public-domain | CC0-1.0 | CC-BY-4.0 | CC-BY-SA-4.0 | owner-permission>
- **License URL:** <https:// url proving the licence>
- **Submitter:** <GitHub login>
- **Source file:** `books/<slug>/source/<filename>`

## Checklist

- [ ] This PR touches only `books/<slug>/manifest.json` and exactly one source file `books/<slug>/source/<filename>`
- [ ] No translated text is included
- [ ] The source file is directly readable and within `max_source_bytes` (`50 MiB` / `52,428,800` bytes default in `config.example.json`)
- [ ] Every extracted chapter fits within `1..2,000` segments and `<= 2.5 MiB` (`2,621,440` UTF-8 bytes) in `source.txt`
