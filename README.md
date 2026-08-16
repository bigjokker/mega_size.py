# mega_size.py

Inspect sizes and structure of **public MEGA** links (folders or files).  
CLI and a Windows desktop GUI. No MEGA account required.

Works with old (`#!`, `#F!`) and new (`/file/…#…`, `/folder/…#…`) public link formats.

Author: [bigjokker](https://github.com/bigjokker)

---

## GUI

Paste one link or a whole page of links, inspect sizes and the folder tree, then download **only** the files you tick. A key never starts a download by itself. Keyless links stay inspect-only.

**Windows**

```text
run.bat
```

Or, from this folder:

```text
python app_gui.py
```

That uses a local `.venv` (created by `run.bat` on first run).

**What the GUI does**

- Pull MEGA links out of copied page text, HTML, or a `.txt` file
- Inspect selected or all links (dead/quota links are skipped; the rest continue)
- Filters, search, sort, folders-only, type breakdown, download ETA
- Tick files across several inspected links and download them in one go
- Remembers the last save folder and the pasted link list

Nothing is downloaded until you click **Download selected**.

---

## CLI

```bash
# Python 3.8+ recommended
pip install requests
# Optional (decrypts names when a key is present)
pip install pycryptodome
```

```bash
python mega_size.py <MEGA_PUBLIC_URL> [options]
```

### Common examples

Total size + tree:

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB"
```

Summary only (skip the tree):

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB" --summary
```

Only folders:

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB" -of
```

Filters (combine freely):

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB" \
  --ext .mp4,.mkv --min-size 500MB \
  --since 2024-01-01 --until 2025-08-01
```

Sort by size descending:

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB" --sort size --desc
```

Flat list for scripting:

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB" --flat
# prints: <size_bytes>\t<path> per file
```

Bytes only (for piping):

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB" --bytes-only
```

Export JSON and CSV:

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB" --export json,csv
# -> mega_structure.json, mega_structure.csv
```

Download-time estimate (overall + per top-level folder):

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB" --mbps 100
```

Verbose logging:

```bash
python mega_size.py "https://mega.nz/folder/AAAAA#BBBBB" --verbose
```

### Features

- **Total size** for public folders or single files (repeated again at the bottom for long outputs).
- **Name decryption** when the URL contains a key and `pycryptodome` is installed; otherwise shows handles as “(encrypted)”.
- **Filters** you can combine: `--ext`, `--min-size`, `--since`, `--until`.
- **Breakdown by file type** (video/audio/image/archive/docs/other).
- **Sorting**: `--sort size|name|date` + `--desc`.
- **Only-folders view**: `-of` / `--only-folders`.
- **Output modes**: `--bytes-only`, `--flat`, `--export json,csv`.
- **Download-time estimate**: `--mbps 100`.
- **Exit codes**: `0` OK, `2` bad input, `3` API error, `4` rate limited.

### Options

| Option | Description |
|---|---|
| `--summary` | Print only the total size (skip tree). |
| `-of`, `-OF`, `--only-folders` | Show **only folders** in the printed tree (no files). |
| `--ext` | Comma-separated extensions (e.g., `.mp4,.mkv`). |
| `--min-size` | Minimum file size (e.g., `500MB`). |
| `--since`, `--until` | Date bounds (`YYYY-MM-DD`). Uses local time. |
| `--sort` | `size`, `name`, or `date`. |
| `--desc` | Sort descending. |
| `--flat` | Also print `<size_bytes>\t<path>` per file. |
| `--bytes-only` | Print only the total number of bytes. |
| `--export` | `json`, `csv`, or `json,csv`. |
| `--mbps` | Estimate download time at the given Mbps. |
| `--verbose` | INFO-level logs. |

Filters affect what is printed/exported and what the breakdown/ETA considers. The top “Total Folder Size” header always shows the *actual* total.

### Decryption & names

- If the public URL includes the decryption **key**, and `pycryptodome` is installed, names are decrypted.
- Without a key (or without `pycryptodome`), names appear as handles with “(encrypted)”. Size calculations still work.

### Output files

- `mega_structure.json` — hierarchical tree of the printed view (filtered scope).
- `mega_structure.csv` — `path, type, size_bytes, size_human, ts_iso, handle`.

### Troubleshooting

- **“Warning: No decryption key found in URL”** — expected for keyless links; names won’t decrypt.
- **Rate limited** — the script retries with backoff; if still limited, it exits with code `4`.
- **Large folders** — use `--summary`, `-of`, `--flat`, and/or filters.

---

## License

Copyright (c) 2025-2026 bigjokker. All rights reserved.
See [LICENSE](LICENSE).
