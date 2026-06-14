# Agent Guide

This file is for coding and automation agents working in the Lillio Archive
repository. Read it before operating the tool or changing its code.

## Project Purpose

Lillio Archive downloads and verifies photos and videos available through a
user's authorized Lillio parent account. It uses Playwright with a persistent
Chromium profile, stores media and metadata locally, and never handles the
user's password directly.

This is an unofficial project and is not affiliated with Lillio. Do not add
features that bypass authentication, access controls, CAPTCHAs, or rate limits.

## Repository Map

- `src/lillio_archive/browser.py`: authentication, pagination, journal API
  capture, and media discovery.
- `src/lillio_archive/downloader.py`: filtering, retry behavior, atomic media
  writes, metadata embedding, manifest updates, and progress output.
- `src/lillio_archive/archive.py`: verification, reconciliation, reporting, and
  generic export batches.
- `src/lillio_archive/manifest.py`: SQLite schema, migrations, media records,
  run history, and export ledger.
- `src/lillio_archive/config.py`: TOML, environment, CLI precedence, and path
  resolution.
- `src/lillio_archive/cli.py`: public commands and exit behavior.
- `tests/`: synthetic fixtures only. Never introduce real account data.

Private runtime directories are ignored by Git:

- `.lillio-profile/`: authenticated browser state.
- `downloads/`: media, sidecars, and `manifest.sqlite3`.
- `artifacts/`: logs, inspection output, and JSON/CSV reports.
- `exports/`: timestamped export batches and the `latest` symlink.

Treat all four as sensitive, even when filenames appear harmless.

## Setup And Checks

Use Python 3.11 or newer:

```bash
uv sync --extra test
uv run playwright install chromium
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run pyright
uv run pytest -q
uv run python -m compileall -q src tests scripts
uv run python scripts/check_public_tree.py
```

Video verification and metadata require `ffmpeg` and `ffprobe`. Check them
before a run involving videos:

```bash
ffmpeg -version
ffprobe -version
```

Do not install packages, browsers, or system tools without the user's approval
when the environment requires elevated permissions or network access.

## Operating The Tool For A User

### 1. Establish Paths And Intent

Run from the repository root unless the user provides explicit paths. Check for
`lillio-archive.toml` without printing its contents if it may contain private
paths. Configuration precedence is:

1. CLI flags
2. `LILLIO_ARCHIVE_*` environment variables
3. `lillio-archive.toml`
4. built-in defaults

Do not relocate, delete, or recreate an existing browser profile, archive,
manifest, or export ledger unless explicitly requested.

### 2. Authenticate Safely

The default `auto` browser mode first tries the saved profile headlessly. If
authentication has expired, the tool reopens visibly and waits for the user to
sign in.

```bash
uv run lillio-archive inspect
```

Use `--visible` when debugging browser state or when a user expects to sign in.
Use `--headless` only when a valid saved session is expected. Never ask the
user to provide credentials in chat, logs, configuration, or shell commands.

The app-reminder popup is dismissed automatically. If login or another
interactive prompt is required, clearly tell the user what window to use and
wait for them rather than attempting to obtain credentials.

### 3. Choose The Download Mode

Normal incremental run:

```bash
uv run lillio-archive download
```

This validates existing local media, loads recent feed pages, and stops after a
complete loaded page is already archived. It is the preferred routine run.

Use a full scan when establishing a new archive, investigating missing history,
or intentionally auditing all older pages:

```bash
uv run lillio-archive download --full-scan
```

Use date filters only when requested. They are inclusive and based on the
authoritative journal API date:

```bash
uv run lillio-archive download --since YYYY-MM-DD --until YYYY-MM-DD
```

Use `--new` only for the explicit created-since-last-success optimization.
Prefer the normal incremental run when completeness matters.

Use `--dry-run` to inspect discovery and filtering without downloading media or
recording a download run:

```bash
uv run lillio-archive download --dry-run
```

Do not combine assumptions about an old archive with destructive cleanup.
Missing or corrupt files are repaired automatically; upstream disappearance
does not delete local media.

### 4. Monitor And Interpret A Run

Interactive stdout reports item count and actual downloaded bytes separately.
Redirected or quiet output remains line-oriented. Detailed logs default to
`artifacts/lillio-archive.log`.

Downloads retry timeouts, HTTP 408/429, and server errors. A permanently failed
item does not stop the batch. The command processes remaining items, records
failures, writes reports, and exits nonzero.

Never treat a nonzero batch exit as proof that nothing succeeded. Inspect the
latest JSON/CSV report and summarize:

- downloaded
- repaired
- skipped
- duplicate
- failed
- bytes transferred

Logs and reports may contain post titles, activity IDs, and local paths. Do not
paste them publicly or commit them. Signed URL query strings and common secret
fields are redacted, but reports are still private.

### 5. Verify After Download

Run verification after a substantial or interrupted download:

```bash
uv run lillio-archive verify
```

Verification checks SQLite integrity, file presence, sizes, hashes, sidecars,
JPEG EXIF, video metadata, and orphan files. A nonzero exit means unresolved
corruption or missing requirements remain.

If video verification reports missing tools, install or locate `ffmpeg` and
`ffprobe` before changing files. Do not rewrite videos merely to silence the
check.

For an authoritative metadata audit:

```bash
uv run lillio-archive reconcile
```

This is a dry audit and always scans the full feed. Review its report before
running:

```bash
uv run lillio-archive reconcile --apply
```

`--apply` may move or rename media, rewrite sidecars and embedded metadata, and
update manifest hashes. Never apply reconciliation without the user's explicit
request or clear prior authorization.

### 6. Export For Another Application

Create a generic incremental export:

```bash
uv run lillio-archive export
```

Each call creates a timestamped batch under `exports/media/` and atomically
updates `exports/media/latest`. Only new or changed media is placed in the new
batch. An up-to-date archive produces an empty latest batch.

Link modes:

```bash
uv run lillio-archive export --link-mode auto
uv run lillio-archive export --link-mode hardlink
uv run lillio-archive export --link-mode copy
```

Use `auto` by default. Use `copy` for independent files or destinations on
another filesystem. Include canonical JSON metadata only when the destination
can consume it:

```bash
uv run lillio-archive export --include-sidecars
```

Applications such as Apple Photos can be pointed at `exports/media/latest`.
Lillio Archive does not automate imports or album management.

## Failure Recovery

- Authentication failure: rerun visibly and let the user sign in.
- Interrupted download: rerun the same command; completed manifest records are
  committed item by item.
- Missing or corrupt file: rerun `download`; it verifies before skipping.
- HTTP 429 or server failure: keep retry defaults unless the user asks for a
  slower policy; use `--retry-count` and `--retry-delay` when appropriate.
- Pagination uncertainty: rerun with `--full-scan`.
- Metadata drift: run `reconcile`, review the report, then use `--apply` only
  with authorization.
- Export destination changed: pass `--export-dir`; the ledger will create a
  fresh export for the new root.
- Non-symlink path named `latest`: stop and ask the user to resolve it. Do not
  overwrite an unexpected file or directory.

## Debugging Lillio Page Or API Changes

Lillio is an external application and may change without notice. When media
discovery, pagination, authentication detection, or metadata extraction stops
working, diagnose the site contract before changing downloader behavior.

### Current Site Contract

`browser.py` currently depends on these observable details:

- A successful feed contains `.activity-modal` elements.
- Activity modal IDs look like `activity-<numeric-id>-modal`.
- Media type is in the modal's `data-type` attribute and is `image` or `video`.
- DOM fallback fields use `.activity-date`, the first `h4`, and
  `.activity-description`.
- The older-page control uses `.more-images-btn`; its text must match
  `LOAD_MORE_PATTERN`.
- End-of-feed detection uses `#no-more-thumbnails`.
- The parent-app reminder contains a link named `Remind me later`.
- Login pages include `/login` in the URL.
- Authoritative activity data arrives in responses whose paths end in
  `all_activities_api` or `journal_api`.
- The expected API body contains `intervals`, whose values are lists of wrapper
  objects containing an `activity` object.
- Relevant activity fields are `id`, `list_date`, `created_at`, `updated_at`,
  `title`, and `description`.
- Download endpoints are synthesized as
  `<base-url>/activities/<activity-id>.<image|video>`.

Treat this list as a hypothesis to verify, not a reason to preserve obsolete
selectors.

### Safe Investigation Order

1. Reproduce visibly with verbose logging and no download mutation:

   ```bash
   uv run lillio-archive inspect --visible --verbose
   ```

2. Confirm the browser is on the feed rather than login, an error page, an
   onboarding screen, or a different Lillio product route.
3. Review `artifacts/inspection.json`. It intentionally records only structural
   information such as visible controls, link attributes, media origins, page
   title, and path.
4. Check the debug log for:
   - page URL and title
   - initial activity count
   - failed requests and HTTP responses
   - captured authoritative activity count
   - unexpected pagination labels
   - DOM fallback-date warnings
5. Use `download --dry-run --visible --verbose` only after inspection succeeds.
   This exercises pagination and candidate discovery without media writes.
6. Use a bounded run while iterating:

   ```bash
   uv run lillio-archive download \
     --dry-run --visible --verbose --max-expand-actions 2
   ```

7. Use `--full-scan` only after the first pages behave correctly.

Do not start by deleting the saved profile, changing dates, or rewriting the
manifest. Those actions can hide the actual page-contract failure.

### Distinguish Failure Classes

- **Authentication:** current URL contains `/login`, a media request returns a
  login URL, or the media content type is `text/html`. Fix session handling,
  not media parsing.
- **Feed selector:** authentication succeeds but waiting for `.activity-modal`
  times out. Inspect the new feed container and loaded-state signal.
- **Pagination:** initial activities are found but no older pages load, the
  control selector changed, or clicking does not increase activity count.
- **API capture:** DOM posts are visible but logs show zero authoritative
  activities or widespread `dom_inference`. Find the replacement response and
  payload shape before accepting DOM dates.
- **Candidate parsing:** activity count is nonzero but discovered image/video
  count is zero or unexpectedly low. Check modal IDs, media type location, and
  whether one post can contain multiple media attachments.
- **Media endpoint:** candidates are correct but media requests return 404,
  redirects, HTML, or a new content type. Inspect how the current UI obtains
  the original media URL.
- **Third-party noise:** analytics, support widgets, and missing thumbnails may
  fail without affecting archive behavior. Do not promote unrelated failures
  to fatal errors.

### Browser Console Probes

When a browser automation tool or Playwright session is available, prefer
small, read-only structural probes. Run them only after the user is signed in.
Do not print `document.cookie`, storage contents, authorization headers, full
response bodies, or media URLs with query strings.

Count and summarize candidate containers:

```javascript
() => ({
  url: location.origin + location.pathname,
  title: document.title,
  activities: document.querySelectorAll(".activity-modal").length,
  loadMore: [...document.querySelectorAll("button, a")]
    .filter((element) => element.offsetParent !== null)
    .map((element) => ({
      tag: element.tagName.toLowerCase(),
      text: (element.innerText || "").trim().slice(0, 80),
      className: String(element.className || "").slice(0, 120),
    }))
    .filter((item) => /load|more|older/i.test(item.text)),
})
```

Inspect sanitized activity structure without titles or descriptions:

```javascript
() => [...document.querySelectorAll(".activity-modal")]
  .slice(0, 10)
  .map((element) => ({
    idShape: element.id.replace(/\d/g, "#"),
    datasetKeys: Object.keys(element.dataset).sort(),
    childClasses: [...element.querySelectorAll("[class]")]
      .slice(0, 30)
      .map((child) => String(child.className))
      .filter(Boolean),
    hasImage: Boolean(element.querySelector("img")),
    hasVideo: Boolean(element.querySelector("video, source")),
  }))
```

For network diagnosis, record only request method, status, origin, path, and
response content type. Strip query strings. Once a likely journal replacement
is identified, inspect only keys and value types first.

### Updating The Integration

When the site contract changes:

1. Prefer stable semantic attributes, accessible roles, or response data over
   generated CSS classes.
2. Keep the journal/API source authoritative for IDs, dates, timestamps, title,
   and description whenever available.
3. Preserve explicit authentication detection. Do not treat redirects or HTML
   login pages as ordinary media failures.
4. If the API payload changes, isolate normalization in
   `_record_journal_payload` or a new parser rather than spreading shape checks
   through discovery.
5. If posts support multiple attachments, change `source_key` so every
   attachment has a stable unique identity. Do not collapse multiple files
   into one `<activity-id>:<media-type>` record.
6. If the original media URL is now returned by the API, prefer it over
   synthesizing an endpoint, but ensure signed query values never enter logs or
   reports.
7. Keep DOM date inference as a clearly logged fallback. Never silently infer
   a year when an authoritative timestamp is available.
8. Preserve pagination stop safety: stop only when every media item on a newly
   loaded page has a valid local file and manifest hash.
9. Add regression tests for the new selector or payload shape before a live
   download.

### Creating Safe Fixtures

Never commit a raw journal response, page HTML, HAR file, screenshot, browser
profile, or inspection artifact from a real account.

To create a fixture:

1. Copy only the minimum object shape needed for the test.
2. Replace IDs with short synthetic values.
3. Replace names, titles, descriptions, dates, URLs, school information, and
   child information.
4. Remove cookies, headers, query strings, storage values, and unrelated API
   fields.
5. Confirm the fixture still reproduces the parser behavior.
6. Run:

   ```bash
   uv run python scripts/check_public_tree.py
   ```

If debugging requires temporary sensitive artifacts, keep them under an ignored
private directory such as `artifacts/` and delete them when no longer needed.
Do not weaken `.gitignore` or the public-tree scanner to accommodate them.

## Coding Rules

- Preserve existing archive and schema migrations.
- Keep writes atomic and owner-only where private data is involved.
- Never log credentials, cookies, authorization headers, or signed URL query
  values.
- Use journal API records as the authoritative source. DOM parsing is a logged
  fallback only.
- Keep commands idempotent and continue processing after item-level failures.
- Add focused tests for behavior changes, including migrations and restarts.
- Use synthetic titles, IDs, timestamps, URLs, and media fixtures.
- Run Ruff formatting and lint checks, Pyright, the full test suite,
  compilation, `git diff --check`, and `scripts/check_public_tree.py` before
  committing.
- Never stage ignored runtime data. Stage an explicit reviewed file list for
  public releases.

## Reporting Back To The User

State the command run, whether authentication was required, totals by status,
verification result, and paths to the generated reports or latest export.
Mention any unresolved failures and the exact recovery action taken. Do not
include private captions, signed URLs, cookies, or credentials in the summary.
