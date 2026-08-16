# VPS deployment

This guide configures an Ubuntu VPS for manual production deployments from GitHub Actions.
The VPS pulls a prebuilt image from GHCR and does not build the application locally.

## 1. Prepare the VPS

Install Git, curl, Docker Engine, and the Docker Compose plugin. Follow the official Docker
installation instructions for your Ubuntu release rather than using an unversioned shell
installer.

Create a dedicated deployment user and application directory:

```sh
sudo adduser --disabled-password --gecos "" bbp
sudo usermod -aG docker bbp
sudo install -d -o bbp -g bbp /opt/baidu-buzz-proxy
sudo -u bbp git clone https://github.com/dactDMA/baidu-buzz-proxy.git /opt/baidu-buzz-proxy
sudo -u bbp cp /opt/baidu-buzz-proxy/.env.example /opt/baidu-buzz-proxy/.env
sudo -u bbp chmod 600 /opt/baidu-buzz-proxy/.env
```

Log out and back in after adding `bbp` to the `docker` group. Confirm that the user can run:

```sh
sudo -iu bbp docker version
sudo -iu bbp docker compose version
```

Edit `/opt/baidu-buzz-proxy/.env` and set the production credentials. Keep
`BBP_HTTP_BIND=127.0.0.1` when another reverse proxy on the host terminates HTTPS.

## 2. Create a deployment SSH key

Generate a dedicated key on a trusted computer:

```sh
ssh-keygen -t ed25519 -C "baidu-buzz-proxy deployment" -f bbp_deploy
```

Add the contents of `bbp_deploy.pub` to `/home/bbp/.ssh/authorized_keys` on the VPS. The
directory must have mode `700`, and `authorized_keys` must have mode `600` and be owned by
`bbp`.

Do not reuse a personal SSH key. GitHub receives only the private deployment key, while the
VPS receives only its public half.

## 3. Configure the GitHub production environment

Open the repository and go to **Settings**, **Environments**, then create an environment
named `production`. Add these environment secrets:

| Secret | Value |
| --- | --- |
| `VPS_HOST` | VPS hostname or IP address |
| `VPS_PORT` | SSH port, normally `22` |
| `VPS_USER` | `bbp` |
| `VPS_SSH_PRIVATE_KEY` | Complete contents of `bbp_deploy` |
| `VPS_KNOWN_HOSTS` | Verified SSH host key entry for the VPS |

Generate a known-hosts entry with:

```sh
ssh-keyscan -p 22 -H your-vps.example.com
```

Compare its fingerprint with the fingerprint shown by the VPS provider or by
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server before storing it.

## 4. Make the container package public

The first push to `main` creates `ghcr.io/dactdma/baidu-buzz-proxy`. Open the package
settings on GitHub and change its visibility to **Public**. A public package lets the VPS
pull images without storing an additional GitHub token.

## 5. Deploy a release

Wait for the **CI and image** workflow to finish successfully. Copy the full 40-character
commit SHA, open **Actions**, **Deploy production**, select **Run workflow**, and paste the
SHA.

The deployment workflow:

1. validates the commit and image name;
2. connects to the VPS with strict SSH host-key checking;
3. backs up SQLite when an earlier release is running;
4. pulls the exact `sha-<commit>` image;
5. waits for all container health checks;
6. restores the previous image if the new release fails.

Run production deployment only while no long transfer is active. Multipart transfers do
not survive a complete worker replacement.

## 6. Authenticate the service accounts

The application container copies BaiduPCS-Go into the persistent `app-data` volume. Log in
once after the first deployment:

```sh
cd /opt/baidu-buzz-proxy
docker compose --env-file .env --env-file .image.env -f compose.prod.yaml \
  exec app /app/data/baidu/BaiduPCS-Go login
docker compose --env-file .env --env-file .image.env -f compose.prod.yaml \
  exec app /app/data/baidu/BaiduPCS-Go quota
```

Complete the interactive prompts. The account must include a valid `STOKEN`, because the
public-share import command requires it. Do not paste cookies into `.env`, deployment logs,
GitHub Actions, or a command saved in shell history. BaiduPCS-Go stores its own login state
under `/app/data/baidu` in the persistent volume.

Set the Buzzheavier account identifier/token in `.env`:

```dotenv
BBP_BUZZHEAVIER_ACCESS_TOKEN=replace-with-the-account-token
BBP_ADMIN_ACCESS_TOKEN=replace-with-a-long-admin-password
BBP_ADMIN_JWT_SECRET=replace-with-an-independent-random-secret
```

`BBP_ADMIN_JWT_SECRET` signs the HTTP-only administrator session cookie. It may be left
empty, in which case the application derives a signing key from `BBP_ADMIN_ACCESS_TOKEN`,
but a separate random value makes later password rotation cleaner.

Restart the application after editing `.env`, then verify both the local and public paths:

```sh
docker compose --env-file .env --env-file .image.env -f compose.prod.yaml up -d
curl http://127.0.0.1:8080/api/health
curl https://baidu.example.com/api/health
```

The first real test should use a small public share. Confirm that the job reaches
`awaiting_selection`, that the Buzzheavier link works, and that the corresponding
`/ProxyJobs/<job-id>` folder is no longer present in Baidu after completion. The project
uses Buzzheavier's web multipart protocol, which is not currently described in its public
API reference; repeat this small smoke test after image upgrades.

## HTTPS

The production Compose file listens on `127.0.0.1:8080` by default. Put Caddy, Nginx, or a
Cloudflare tunnel in front of this address and expose only ports 80 and 443 publicly.

### Nginx and Certbot on Ubuntu

Install the host reverse proxy and certificate tooling:

```sh
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

Create `/etc/nginx/sites-available/baidu-buzz-proxy`:

```nginx
server {
    listen 80;
    listen [::]:80;

    server_name baidu.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
    }
}
```

Replace `baidu.example.com` with the deployment domain, then enable the site:

```sh
sudo ln -s /etc/nginx/sites-available/baidu-buzz-proxy /etc/nginx/sites-enabled/baidu-buzz-proxy
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

Confirm that the application is reachable through Nginx before requesting a certificate:

```sh
curl http://baidu.example.com/api/health
sudo certbot --nginx -d baidu.example.com --redirect
curl https://baidu.example.com/api/health
```

`curl -I` sends a `HEAD` request. A `405 Method Not Allowed` response from an endpoint that
only implements `GET` still proves that the reverse proxy reached the application. Use a
normal `curl URL` request for an application health check.

If the public endpoint returns `502 Bad Gateway`, check the local upstream first:

```sh
curl http://127.0.0.1:8080/api/health
sudo ss -tlnp | grep -E ':80|:443|:8080'
docker compose --env-file .env --env-file .image.env -f compose.prod.yaml ps
```

When the local health request succeeds, the host reverse proxy must use
`http://127.0.0.1:8080`, not HTTPS and not the container-only port 8000.

For a temporary direct test, set the following values in `.env` and allow port 8080 in the
firewall:

```dotenv
BBP_HTTP_BIND=0.0.0.0
BBP_HTTP_PORT=8080
```

Do not use the direct HTTP configuration for production credentials.
