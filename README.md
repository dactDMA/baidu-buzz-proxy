# Baidu Buzz Proxy

[![CI and image](https://github.com/dactDMA/baidu-buzz-proxy/actions/workflows/pipeline.yml/badge.svg)](https://github.com/dactDMA/baidu-buzz-proxy/actions/workflows/pipeline.yml)

Baidu Buzz Proxy is a self-hosted service for transferring files and folders from public
Baidu Netdisk shares to Buzzheavier. Transfers are streamed through the service without
storing complete source files on the host.

The project provides an English web interface and API and is designed for deployment on a
small Linux VPS.

## Features

- Browse public Baidu Netdisk shares without exposing Baidu credentials to visitors
- Transfer individual files or complete folders to Buzzheavier
- Download each Baidu file with ordered parallel HTTP ranges
- Stream ZIP64 archives without compression while preserving folder structure
- Limit concurrent jobs and reserve configurable Baidu account storage
- Keep temporary job pages for the lifetime of the resulting Buzzheavier upload
- Review, filter, open, and cancel recent jobs from the administrator dashboard
- Run as a non-root user in a read-only Alpine-based container
- Persist application state and BaiduPCS-Go configuration in Docker volumes

## Architecture

The default Docker Compose stack contains:

- **FastAPI** for the web interface and API
- **BaiduPCS-Go** for access to Baidu Netdisk
- **Redis** for coordination, locks, and job state
- **SQLite** for persistent application data
- **Nginx** as the public reverse proxy

## Requirements

- Docker Engine with the Compose plugin
- A Baidu Netdisk account that can access the source files
- At least 2 GB of RAM for the recommended two concurrent transfer jobs

A Buzzheavier account is optional. With an empty access token, uploads are anonymous and
the resulting public link is still returned to the job page.

## Quick start

Clone the repository and create the local configuration file:

```sh
git clone https://github.com/dactDMA/baidu-buzz-proxy.git
cd baidu-buzz-proxy
cp .env.example .env
```

Add the required credentials to `.env`, then build and start the stack:

```sh
docker compose up -d --build
```

Open <http://127.0.0.1:8080>. API documentation is available at
<http://127.0.0.1:8080/docs>.

The administrator dashboard is available at <http://127.0.0.1:8080/admin>. Sign in with
the value configured in `BBP_ADMIN_ACCESS_TOKEN`. The browser receives an HttpOnly
administrator session cookie valid for 12 hours; the access token is not stored by the
page.

Authenticate the persisted BaiduPCS-Go installation before creating the first job:

```sh
docker compose exec app /app/data/baidu/BaiduPCS-Go login
docker compose exec app /app/data/baidu/BaiduPCS-Go quota
```

The account must have a valid `STOKEN`; BaiduPCS-Go requires it for importing public
shares. The login configuration is stored in the `app-data` volume, not in the image.

Useful commands:

```sh
docker compose ps
docker compose logs -f app nginx
docker compose down
```

`docker compose down` preserves application data. Do not add `--volumes` unless you intend
to delete the SQLite database, Redis data, and BaiduPCS-Go configuration.

## Configuration

Configuration is read from `.env`. See [.env.example](.env.example) for the complete list.
Production deployments can additionally synchronize these values from the GitHub
`production` environment into `.runtime.env`; synchronized values override `.env`.

| Variable | Description | Default |
| --- | --- | --- |
| `BBP_BUZZHEAVIER_ACCESS_TOKEN` | Optional token for account-owned uploads | Empty (anonymous) |
| `BBP_BUZZHEAVIER_BASE_URL` | Buzzheavier API origin | `https://buzzheavier.com` |
| `BBP_BUZZHEAVIER_PART_SIZE_MIB` | Multipart chunk size | `100` |
| `BBP_BUZZHEAVIER_PART_CONCURRENCY` | Concurrent chunks per job | `2` |
| `BBP_BUZZHEAVIER_PART_RETRIES` | Retry count for one failed chunk | `5` |
| `BBP_ADMIN_ACCESS_TOKEN` | Password used to access administrative functions | Required |
| `BBP_ADMIN_JWT_SECRET` | Signing secret for admin cookies | Derived from admin token |
| `BBP_TURNSTILE_SITE_KEY` | Cloudflare Turnstile public site key | Empty |
| `BBP_TURNSTILE_SECRET_KEY` | Cloudflare Turnstile secret key | Empty |
| `BBP_BAIDU_RESERVE_GIB` | Baidu storage that must remain unused | `300` |
| `BBP_BAIDU_DOWNLOAD_CONCURRENCY` | Parallel Baidu range requests per file | `10` |
| `BBP_BAIDU_RANGE_SIZE_MIB` | In-memory size of each ordered Baidu range | `16` |
| `BBP_BAIDU_DOWNLOAD_RETRIES` | Retry count for a failed Baidu range | `5` |
| `BBP_MAX_ACTIVE_JOBS` | Maximum number of simultaneous jobs | `2` |
| `BBP_MAX_PENDING_JOBS` | Maximum number of queued or waiting jobs | `100` |
| `BBP_JOB_PAGE_TTL_DAYS` | Completed job page retention | `8` |
| `BBP_FAILED_JOB_TTL_HOURS` | Failed job retention | `24` |
| `BBP_STALLED_JOB_TIMEOUT_HOURS` | Timeout for stalled jobs | `24` |

Parallel Baidu downloads buffer at most one range per connection. The defaults use up to
about 160 MiB of range buffers per active job (`10 × 16 MiB`) in addition to Buzzheavier
multipart buffers. Reduce concurrency or range size on memory-constrained hosts.

Never commit `.env`, BaiduPCS-Go configuration, Baidu cookies, Buzzheavier credentials, or
administrator tokens.

`BBP_BAIDU_PCS_CONFIG` and `BBP_BAIDU_COOKIES` are deployment-only GitHub secrets rather
than application environment variables. A tested full `pcs_config.json` is preferred; it
preserves a working custom PCS endpoint and avoids a new login during startup. The cookie
secret is a fallback when no configuration secret is supplied. A valid Baidu login is
required for transfers. Leaving both secrets empty works only when BaiduPCS-Go was logged
in manually in the persistent volume. See
[VPS deployment](docs/VPS_DEPLOYMENT.md#baidupcs-go-configuration-format) for formats and
the complete GitHub secret list.

Cloudflare Turnstile is optional. Leave both Turnstile settings empty to run without a
CAPTCHA, or configure the matching site and secret keys together.

## Persistent data

Docker Compose creates two named volumes:

- `app-data` contains the SQLite database and BaiduPCS-Go configuration.
- `redis-data` contains the Redis append-only log.

The application image can be rebuilt or replaced without deleting these volumes.

## Transfer behavior

- A new job first imports the entire public share into `/ProxyJobs/<job-id>` in the
  service account. This is necessary because BaiduPCS-Go does not accept a destination or
  selected file IDs for its public-share transfer command.
- Once the import is complete, the secret job page displays the imported tree and accepts
  either individual selections or the complete share.
- Source files are streamed and are not retained as complete files on the VPS.
- Folders are represented as uncompressed ZIP64 archives.
- Failed multipart requests can be retried while the worker remains running.
- An interrupted transfer does not resume after a complete worker restart and must be
  started again.
- Result pages expire after the configured retention period.
- Cleanup records the temporary folder's `fs_id`, moves only that folder to the Baidu
  recycle bin, and permanently deletes only that recorded item. It never clears the whole
  recycle bin.

Buzzheavier documents both anonymous and account-owned uploads. Its web uploader currently
uses a multipart protocol that retries individual chunks; this project implements that
protocol in anonymous mode by default. Because the multipart endpoint is not part of
Buzzheavier's published API reference, test a small transfer after upgrades before starting
a very large job.

## Security

Use a dedicated Baidu account for a public deployment. If account-owned Buzzheavier uploads
are enabled, use a dedicated Buzzheavier account as well. Keep credentials on the server,
place the service behind HTTPS, enable Turnstile, and apply network-level rate limiting.
The service should accept only supported Baidu Netdisk URLs and must never be exposed as a
general-purpose URL proxy.

## Development

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required for local development.

```sh
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run mypy src
```

To run the API locally while keeping Redis in Docker:

```sh
docker compose up -d redis
uv run uvicorn baidu_buzz_proxy.main:app --reload
```

## Production deployment

Every push to `main` is tested and published to GitHub Container Registry with an immutable
`sha-<commit>` tag. Production deployment is a separate manual GitHub Actions workflow so
an update cannot unexpectedly interrupt a long-running transfer.

See [VPS deployment](docs/VPS_DEPLOYMENT.md) for the Ubuntu setup, SSH key, GitHub
environment secrets, HTTPS, health checks, and rollback procedure.

## License

Released under the [MIT License](LICENSE).
