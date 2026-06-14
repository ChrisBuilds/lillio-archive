# Lillio Archive

Lillio Archive is a local, incremental archive utility for photos and videos
available through an authorized Lillio parent account.

> [!IMPORTANT]
> This is an unofficial community project. It is not affiliated with, endorsed
> by, or supported by Lillio. Use it only with an account and media you are
> authorized to access.

The tool never asks for or stores a password. Authentication happens in a
persistent local Chromium profile. Browser data, downloaded media, manifests,
logs, reports, and exports are excluded from Git.

## Requirements

- macOS or Linux
- Python 3.11 or newer
- Chromium installed through Playwright
- `ffmpeg` and `ffprobe` for video metadata

Install `ffmpeg` with Homebrew on macOS:

```bash
brew install ffmpeg
```

On Debian or Ubuntu:

```bash
sudo apt-get install ffmpeg
```

## Installation

With `uv`:

```bash
git clone https://github.com/ChrisBuilds/lillio-archive.git
cd lillio-archive
uv sync --extra test
uv run playwright install chromium
```

With a virtual environment and `pip`:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
playwright install chromium
```

Commands below use `uv run`. In an activated environment, omit `uv run`.

## First Run

Inspect the authenticated site without downloading media:

```bash
uv run lillio-archive inspect
```

If the saved session is unavailable, Chromium opens visibly. Sign in normally,
navigate to the activity feed, then return to the terminal. The inspection
report is structural and redacted.

Download the archive:

```bash
uv run lillio-archive download
```

Media is stored under `downloads/YYYY-MM-DD/`. Filenames contain the
authoritative date, sanitized post title, and stable activity ID:

```text
downloads/2025-01-15/2025-01-15_Art-Project_100001.jpg
```

Each file has a JSON sidecar with its title, description, activity ID, source
timestamps, and media type. JPEG metadata is updated without recompressing the
image. Videos are remuxed with `ffmpeg -codec copy` to add title, description,
and creation time without re-encoding.

Subsequent downloads verify existing sizes and hashes. Pagination stops after a
complete loaded page is already archived. Use `--full-scan` to load all older
pages.

```bash
uv run lillio-archive download --since 2025-01-01
uv run lillio-archive download --until 2025-12-31
uv run lillio-archive download --new
uv run lillio-archive download --dry-run
uv run lillio-archive download --full-scan
```

## Archive Commands

```bash
uv run lillio-archive verify
uv run lillio-archive reconcile
uv run lillio-archive reconcile --apply
uv run lillio-archive report
uv run lillio-archive repair-dates
```

- `verify` checks the database, media, sizes, hashes, sidecars, embedded
  metadata, and orphan files.
- `reconcile` audits the local archive against authoritative Lillio metadata.
  Add `--apply` to update local metadata and filenames.
- `report` writes JSON and CSV archive summaries.
- `repair-dates` preserves compatibility with archives created before
  authoritative year handling was added.

## Generic Export

`export` creates timestamped, media-only batches under `exports/media/`.
Only new or changed media is included. The relative `latest` symlink always
points to the newest batch.

```bash
uv run lillio-archive export
uv run lillio-archive export --link-mode copy
uv run lillio-archive export --include-sidecars
```

Link modes:

- `auto` tries a hard link and falls back to a copy.
- `hardlink` requires hard-link support and fails otherwise.
- `copy` always creates independent files.

Add `--include-sidecars` for systems that can consume adjacent JSON metadata.
Each batch has a detailed `media-export-*.csv` report under
`artifacts/reports/`.

The output is ordinary filesystem media suitable for manual import into Apple
Photos, Google Photos, Immich, PhotoPrism, Synology Photos, or another archive.
For Apple Photos, import `exports/media/latest` and add the imported items to
the desired album. Lillio Archive does not automate any photo application.

## Configuration

Copy the example and adjust it locally:

```bash
cp lillio-archive.example.toml lillio-archive.toml
```

Precedence is CLI flags, `LILLIO_ARCHIVE_*` environment variables,
`lillio-archive.toml`, then built-in defaults. Paths in TOML are relative to
the configuration file. Other relative paths use the current working
directory. `~` is expanded.

```toml
[lillio-archive]
browser-mode = "auto"
profile-dir = ".lillio-profile"
download-dir = "downloads"
artifact-dir = "artifacts"
export-dir = "exports/media"
max-expand-actions = 500
retry-count = 3
retry-delay = 1.0
```

All paths can also be supplied directly:

```bash
uv run lillio-archive download \
  --profile-dir ~/.local/share/lillio-archive/profile \
  --download-dir ~/Pictures/lillio-archive \
  --artifact-dir ~/.local/state/lillio-archive
```

## Logging And Privacy

Normal output reports stages and progress. Detailed logs default to
`artifacts/lillio-archive.log`; use `--no-log-file` to disable them. URL query
strings and common credential fields are redacted.

```bash
uv run lillio-archive download --verbose
uv run lillio-archive download --quiet
uv run lillio-archive inspect --no-log-file
```

The project does not bypass login, access controls, CAPTCHAs, or rate limits.
Review reports before sharing them because post titles and local filenames may
still be personally meaningful.
