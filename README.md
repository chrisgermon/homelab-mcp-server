# Homelab MCP Server

A personal homelab MCP server built on FastMCP, deployed to GCP Cloud Run.

Separates personal/homelab tooling from the [Crowd IT business MCP server](https://github.com/chrisgermon/crowdit-mcp-server).

## Current Integrations

- **Proxmox VE** — VM/container/storage/snapshot/backup management (~40 tools)

## Architecture

- FastMCP (Python 3.11)
- GCP Cloud Run (australia-southeast1)
- API key auth via `MCP_API_KEYS` env var or GCP Secret Manager
- Cloudflare Tunnel for Proxmox connectivity (no port forwarding required)

## Environment Variables

| Variable | Description |
|---|---|
| `PROXMOX_HOST` | Proxmox host/tunnel hostname with port (e.g. `proxmox.yourdomain.com` or `192.168.1.x:8006`) |
| `PROXMOX_TOKEN_ID` | API token ID (e.g. `root@pam!homelab`) |
| `PROXMOX_TOKEN_SECRET` | API token secret UUID |
| `PROXMOX_VERIFY_SSL` | `false` for self-signed certs (homelab default) |
| `MCP_API_KEYS` | Comma-separated API keys for auth |

## Deployment

Pushes to `main` auto-trigger Cloud Build → Cloud Run deploy.

```bash
# Set env vars on Cloud Run
gcloud run services update homelab-mcp-server \
  --region australia-southeast1 \
  --update-env-vars \
  PROXMOX_HOST=proxmox.yourdomain.com,\
  PROXMOX_TOKEN_ID=root@pam!homelab,\
  PROXMOX_TOKEN_SECRET=your-uuid,\
  PROXMOX_VERIFY_SSL=false
```

## Adding to Claude

Add to your Claude MCP settings:
```
https://homelab-mcp-server-[hash]-ts.a.run.app/mcp?api_key=YOUR_KEY
```
