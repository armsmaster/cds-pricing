# Deployment guide

The app runs as a single Docker container that serves the UI and the API. At
**runtime it needs no internet** (Plotly is vendored locally); only the **build**
needs a package registry + index. Two things are parameterized so you can build
behind an internal mirror such as **Nexus**:

| Build arg / env | Purpose | Default (public) |
|---|---|---|
| `BASE_IMAGE` | Docker base image | `python:3.12-slim-bookworm` |
| `PIP_INDEX_URL` | Python package index (pip + uv) | `https://pypi.org/simple` |
| `certs/*.crt` | Corporate root CA(s) trusted in the image | none (no-op) |

The base image lives on **Docker Hub** and `uv` is installed from the package
index, so a Nexus mirror of Docker Hub + PyPI is sufficient — no access to
`ghcr.io` or the public internet is required.

---

## Build on the work machine (behind Nexus)

### 1. Trust the corporate CA (TLS-intercepting proxy)

- **In the image (build):** copy your corporate root CA into `certs/`:
  ```bash
  cp /path/to/corp-ca.crt certs/
  ```
  It is copied into the image and trusted by pip and uv. (`certs/*.crt` is
  gitignored — never committed.)
- **On the host (image pull):** the Docker daemon must also trust the CA to pull
  the base image from Nexus over TLS. On managed machines this is usually already
  in the OS trust store. Docker Desktop uses the Windows/macOS trust store; on
  Linux, add the CA to the system store (or `/etc/docker/certs.d/<nexus-host>/ca.crt`)
  and restart Docker.

### 2. Point the build at Nexus

Create a `.env` file next to `docker-compose.yml` (gitignored):

```dotenv
BASE_IMAGE=<nexus-docker-host>/python:3.12-slim-bookworm
PIP_INDEX_URL=<nexus-pypi-url>/simple
```

Examples (adjust to your Nexus):
```dotenv
BASE_IMAGE=nexus.example.com:8082/python:3.12-slim-bookworm
PIP_INDEX_URL=https://nexus.example.com/repository/pypi-proxy/simple
```

If your Nexus Docker registry requires authentication:
```bash
docker login <nexus-docker-host>
```

### 3. Build and run

```bash
docker compose build
docker compose up -d
```

Open **http://localhost:8127**. Manage with:
```bash
docker compose logs -f
docker compose down
```

---

## Plain internet build (defaults)

With normal internet access, no `.env` and no `certs/` are needed:

```bash
docker compose up -d --build
```

---

## Notes & troubleshooting

- **SSL errors during `pip install` / `uv sync`** — the corporate CA isn't
  trusted. Ensure the `.crt` is in `certs/` (PEM format) before building.
- **Base image pull fails / `ghcr.io` referenced** — make sure `BASE_IMAGE`
  points at your Nexus Docker Hub proxy; the default is a Docker Hub image.
- **Dependencies still download from `files.pythonhosted.org`** — `uv.lock` pins
  each wheel to an absolute PyPI URL, and `PIP_INDEX_URL`/`UV_INDEX_URL` only
  affect index *resolution*. The `Dockerfile` therefore runs `uv lock` before
  `uv sync` to re-resolve the artifact URLs against your index. This needs your
  Nexus pypi repo to *serve* the wheels (a proxy/hosted repo — the normal case)
  rather than redirect to upstream.
- **`uv sync --frozen` rejects the index** — Nexus normally serves the same
  artifacts as PyPI so the locked hashes match. If uv still objects, either drop
  `--frozen` in the `Dockerfile`, or add the Nexus index to `pyproject.toml`:
  ```toml
  [[tool.uv.index]]
  name = "nexus"
  url = "https://nexus.example.com/repository/pypi-proxy/simple"
  default = true
  ```
- **Fully offline alternative** — build on an internet-connected machine, then:
  ```bash
  docker save pricing-cds-app | gzip > pricing-cds-app.tar.gz
  # copy over, then on the work machine:
  docker load < pricing-cds-app.tar.gz
  docker compose up -d            # uses the loaded image
  ```
