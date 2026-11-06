## Chunk translation

- **Agent:** <GitHub login>
- **Engine/Model:** <engine, model — e.g. DeepSeek deepseek-v4.1-flash via cbc>
- **Source language:** <en|zh>
- **Target language:** vi
- **Book:** <slug>
- **Chunk ID:** <slug>-chNN
- **Issue:** #<issue number>
- **Glossary SHA:** <frozen glossary SHA from the issue>
- **Segments:** <seg_first>..<seg_last>

## Checklist

- [ ] Only `runs/<slug>/chNN/{translation.xml, analyze_additions.json}` changed
- [ ] `book_record.json` and `checklist.json` untouched
- [ ] Segment counts match between source and translation
- [ ] Code blocks and image anchors byte-identical
