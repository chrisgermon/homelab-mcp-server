"""
Proxmox VE Integration Tools for Homelab MCP Server

Provides comprehensive Proxmox Virtual Environment management via the Proxmox API.

Capabilities:
- Cluster status and resource overview
- Node management (status, network)
- VM (QEMU) management (list, start, stop, reboot, status, config, clone, delete, migrate, resize)
- Container (LXC) management (list, start, stop, reboot, status, config, clone, delete, migrate)
- Storage management (list, content)
- Snapshot management (create, list, delete, rollback)
- Task management (list, status)
- Backup management (list, create)
- Pool, template, ISO, firewall, HA management

Authentication: API token (PVEAPIToken)

Environment Variables:
    PROXMOX_HOST: Proxmox host/tunnel hostname (e.g. proxmox.yourdomain.com or 192.168.1.x:8006)
    PROXMOX_PORT: Port (default: 8006, ignored if host already contains port)
    PROXMOX_TOKEN_ID: API token ID (e.g. root@pam!homelab)
    PROXMOX_TOKEN_SECRET: API token secret UUID
    PROXMOX_VERIFY_SSL: 'true' or 'false' (default: false for homelab self-signed certs)
"""

import os
import json
import logging
import asyncio
from typing import Optional, Any

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

class ProxmoxConfig:
    """Proxmox VE API configuration using API token authentication."""

    def __init__(self):
        self._host: Optional[str] = None
        self._port: Optional[str] = None
        self._token_id: Optional[str] = None
        self._token_secret: Optional[str] = None
        self._verify_ssl: Optional[bool] = None

    def _load_env(self, env_var: str, default: str = "") -> str:
        return os.getenv(env_var, default)

    @property
    def host(self) -> str:
        if self._host is None:
            self._host = self._load_env("PROXMOX_HOST")
        return self._host

    @property
    def port(self) -> str:
        if self._port is None:
            self._port = self._load_env("PROXMOX_PORT", "8006")
        return self._port

    @property
    def token_id(self) -> str:
        if self._token_id is None:
            self._token_id = self._load_env("PROXMOX_TOKEN_ID")
        return self._token_id

    @property
    def token_secret(self) -> str:
        if self._token_secret is None:
            self._token_secret = self._load_env("PROXMOX_TOKEN_SECRET")
        return self._token_secret

    @property
    def verify_ssl(self) -> bool:
        if self._verify_ssl is None:
            val = self._load_env("PROXMOX_VERIFY_SSL", "false")
            self._verify_ssl = val.lower() in ("true", "1", "yes")
        return self._verify_ssl

    @property
    def base_url(self) -> str:
        host = self.host
        # If host already contains a port, use it directly
        if ":" in host:
            return f"https://{host}/api2/json"
        return f"https://{host}:{self.port}/api2/json"

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.token_id and self.token_secret)

    @property
    def not_configured_error(self) -> str:
        return (
            "Error: Proxmox VE not configured. "
            "Set PROXMOX_HOST, PROXMOX_TOKEN_ID, and PROXMOX_TOKEN_SECRET environment variables."
        )

    async def do_request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
        json_body: dict = None,
        timeout: float = 30.0,
    ) -> Any:
        """Make an authenticated Proxmox API request with retry on rate limit."""
        import httpx

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"PVEAPIToken={self.token_id}={self.token_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=timeout) as client:
            for attempt in range(3):
                try:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=json_body,
                    )
                except httpx.RequestError as e:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise Exception(f"Network error connecting to Proxmox: {e}")

                if response.status_code == 429:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise Exception("Rate limited by Proxmox API.")

                if response.status_code >= 400:
                    try:
                        error_data = response.json()
                        errors = error_data.get("errors", {})
                        error_msg = json.dumps(errors) if errors else response.text
                        raise Exception(f"Proxmox API error ({response.status_code}): {error_msg}")
                    except (json.JSONDecodeError, KeyError):
                        response.raise_for_status()

                if response.status_code == 204:
                    return {"status": "success"}

                data = response.json()
                return data.get("data", data)


def _safe_pct(used: float, total: float) -> str:
    """Return a percentage string, or 'N/A' if total is zero."""
    if total > 0:
        return f"{used / total * 100:.0f}%"
    return "N/A"


# =============================================================================
# Tool Registration
# =============================================================================

def register_proxmox_tools(mcp, config: ProxmoxConfig):
    """Register all Proxmox VE tools with the MCP server."""

    from pydantic import Field

    def _check_config() -> Optional[str]:
        if not config.is_configured:
            return config.not_configured_error
        return None

    # =========================================================================
    # Cluster & Node Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_cluster_status() -> str:
        """Get Proxmox cluster status including all nodes and their health."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", "/cluster/status")
            if not data:
                return "No cluster status data available."
            lines = []
            for item in data:
                item_type = item.get("type", "unknown")
                name = item.get("name", "unknown")
                if item_type == "cluster":
                    quorate = "Yes" if item.get("quorate") else "No"
                    lines.append(f"Cluster: {name} | Quorate: {quorate} | Nodes: {item.get('nodes', 'N/A')} | Version: {item.get('version', 'N/A')}")
                elif item_type == "node":
                    online = "Online" if item.get("online") else "Offline"
                    lines.append(f"  Node: {name} | Status: {online} | ID: {item.get('nodeid', 'N/A')} | IP: {item.get('ip', 'N/A')}")
            return "\n".join(lines) if lines else json.dumps(data, indent=2)
        except Exception as e:
            return f"Error getting cluster status: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_cluster_resources(
        resource_type: Optional[str] = Field(None, description="Filter by type: vm, storage, node, sdn, pool")
    ) -> str:
        """Get all cluster resources (VMs, containers, storage, nodes) with status and usage."""
        err = _check_config()
        if err:
            return err
        try:
            params = {}
            if resource_type:
                params["type"] = resource_type
            data = await config.do_request("GET", "/cluster/resources", params=params)
            if not data:
                return "No resources found."
            lines = []
            for r in data:
                rtype = r.get("type", "unknown")
                name = r.get("name", r.get("storage", "unknown"))
                status = r.get("status", "unknown")
                node = r.get("node", "")
                vmid = r.get("vmid", "")
                cpu = r.get("cpu", 0)
                mem = r.get("mem", 0) / (1024**3) if r.get("mem") else 0
                maxmem = r.get("maxmem", 0) / (1024**3) if r.get("maxmem") else 0
                cpu_pct = f"{cpu * 100:.1f}%" if cpu else "N/A"
                if rtype in ("qemu", "lxc"):
                    lines.append(f"  [{rtype.upper()}] {name} (VMID: {vmid}) | Node: {node} | Status: {status} | CPU: {cpu_pct} | RAM: {mem:.1f}/{maxmem:.1f} GB")
                elif rtype == "node":
                    lines.append(f"  [NODE] {name} | Status: {status} | CPU: {cpu_pct} | RAM: {mem:.1f}/{maxmem:.1f} GB")
                elif rtype == "storage":
                    disk = r.get("disk", 0) / (1024**3) if r.get("disk") else 0
                    maxdisk = r.get("maxdisk", 0) / (1024**3) if r.get("maxdisk") else 0
                    lines.append(f"  [STORAGE] {name} | Node: {node} | Status: {status} | Used: {disk:.1f}/{maxdisk:.1f} GB")
                else:
                    lines.append(f"  [{rtype.upper()}] {name} | Node: {node} | Status: {status}")
            header = f"Found {len(data)} resources"
            if resource_type:
                header += f" (type: {resource_type})"
            return header + ":\n" + "\n".join(lines)
        except Exception as e:
            return f"Error getting cluster resources: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_nodes() -> str:
        """List all nodes in the Proxmox cluster with status, uptime, CPU, and memory."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", "/nodes")
            if not data:
                return "No nodes found."
            lines = [f"Found {len(data)} nodes:"]
            for node in sorted(data, key=lambda x: x.get("node", "")):
                name = node.get("node", "unknown")
                status = node.get("status", "unknown")
                cpu = node.get("cpu", 0)
                maxcpu = node.get("maxcpu", 0)
                mem = node.get("mem", 0) / (1024**3) if node.get("mem") else 0
                maxmem = node.get("maxmem", 0) / (1024**3) if node.get("maxmem") else 0
                uptime_h = node.get("uptime", 0) / 3600
                cpu_pct = f"{cpu * 100:.1f}%" if cpu else "N/A"
                lines.append(f"  {name} | Status: {status} | CPU: {cpu_pct} ({maxcpu} cores) | RAM: {mem:.1f}/{maxmem:.1f} GB | Uptime: {uptime_h:.1f}h")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing nodes: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_node_status(
        node: str = Field(..., description="Node name (e.g. 'pve', 'node1')")
    ) -> str:
        """Get detailed status of a Proxmox node: CPU, memory, disk, kernel info."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/status")
            if not data:
                return f"No status data for node '{node}'."
            cpu = data.get("cpu", 0)
            maxcpu = data.get("cpuinfo", {}).get("cpus", 0)
            model = data.get("cpuinfo", {}).get("model", "N/A")
            mem = data.get("memory", {})
            mem_used = mem.get("used", 0) / (1024**3)
            mem_total = mem.get("total", 0) / (1024**3)
            swap = data.get("swap", {})
            swap_used = swap.get("used", 0) / (1024**3)
            swap_total = swap.get("total", 0) / (1024**3)
            rootfs = data.get("rootfs", {})
            disk_used = rootfs.get("used", 0) / (1024**3)
            disk_total = rootfs.get("total", 0) / (1024**3)
            uptime_d = data.get("uptime", 0) / 86400
            kernel = data.get("kversion", "N/A")
            pveversion = data.get("pveversion", "N/A")
            loadavg = data.get("loadavg", ["N/A", "N/A", "N/A"])

            return (
                f"Node: {node}\n"
                f"  PVE Version: {pveversion}\n"
                f"  Kernel: {kernel}\n"
                f"  CPU: {model} ({maxcpu} cores) | Usage: {cpu * 100:.1f}%\n"
                f"  Load Average: {', '.join(str(l) for l in loadavg)}\n"
                f"  Memory: {mem_used:.1f}/{mem_total:.1f} GB ({_safe_pct(mem_used, mem_total)} used)\n"
                f"  Swap: {swap_used:.1f}/{swap_total:.1f} GB\n"
                f"  Root Disk: {disk_used:.1f}/{disk_total:.1f} GB ({_safe_pct(disk_used, disk_total)} used)\n"
                f"  Uptime: {uptime_d:.1f} days"
            )
        except Exception as e:
            return f"Error getting node status: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_node_network(
        node: str = Field(..., description="Node name")
    ) -> str:
        """List network interfaces on a Proxmox node."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/network")
            if not data:
                return f"No network interfaces found on node '{node}'."
            lines = [f"Network interfaces on {node}:"]
            for iface in sorted(data, key=lambda x: x.get("iface", "")):
                name = iface.get("iface", "unknown")
                itype = iface.get("type", "unknown")
                address = iface.get("address", "N/A")
                cidr = iface.get("cidr", "N/A")
                active = "Active" if iface.get("active") else "Inactive"
                lines.append(f"  {name} | Type: {itype} | Address: {address} | CIDR: {cidr} | {active}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting network interfaces: {e}"

    # =========================================================================
    # VM (QEMU) Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_vms(
        node: Optional[str] = Field(None, description="Node name. Leave empty to list VMs from all nodes.")
    ) -> str:
        """List all QEMU virtual machines with status, CPU, and memory usage."""
        err = _check_config()
        if err:
            return err
        try:
            if node:
                nodes = [node]
            else:
                node_data = await config.do_request("GET", "/nodes")
                nodes = [n["node"] for n in node_data if n.get("status") == "online"]
            all_vms = []
            for n in nodes:
                try:
                    vms = await config.do_request("GET", f"/nodes/{n}/qemu")
                    for vm in (vms or []):
                        vm["_node"] = n
                        all_vms.append(vm)
                except Exception:
                    pass
            if not all_vms:
                return "No VMs found."
            lines = [f"Found {len(all_vms)} VMs:"]
            for vm in sorted(all_vms, key=lambda x: x.get("vmid", 0)):
                vmid = vm.get("vmid", "?")
                name = vm.get("name", "unnamed")
                status = vm.get("status", "unknown")
                cpu = vm.get("cpu", 0)
                cpus = vm.get("cpus", 0)
                mem = vm.get("mem", 0) / (1024**3) if vm.get("mem") else 0
                maxmem = vm.get("maxmem", 0) / (1024**3) if vm.get("maxmem") else 0
                cpu_pct = f"{cpu * 100:.1f}%" if cpu else "N/A"
                lines.append(f"  {vmid}: {name} | Node: {vm['_node']} | Status: {status} | CPU: {cpu_pct} ({cpus} cores) | RAM: {mem:.1f}/{maxmem:.1f} GB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing VMs: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_vm_status(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Get detailed status of a specific VM: CPU, memory, disk, network, uptime."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/qemu/{vmid}/status/current")
            if not data:
                return f"No status data for VM {vmid}."
            name = data.get("name", "unnamed")
            status = data.get("status", "unknown")
            qmpstatus = data.get("qmpstatus", "unknown")
            cpu = data.get("cpu", 0)
            cpus = data.get("cpus", 0)
            mem = data.get("mem", 0) / (1024**3) if data.get("mem") else 0
            maxmem = data.get("maxmem", 0) / (1024**3) if data.get("maxmem") else 0
            disk = data.get("disk", 0) / (1024**3) if data.get("disk") else 0
            maxdisk = data.get("maxdisk", 0) / (1024**3) if data.get("maxdisk") else 0
            netin = data.get("netin", 0) / (1024**2) if data.get("netin") else 0
            netout = data.get("netout", 0) / (1024**2) if data.get("netout") else 0
            uptime_h = data.get("uptime", 0) / 3600
            pid = data.get("pid", "N/A")
            return (
                f"VM {vmid}: {name}\n"
                f"  Status: {status} | QMP: {qmpstatus} | PID: {pid}\n"
                f"  CPU: {cpu * 100:.1f}% ({cpus} cores)\n"
                f"  Memory: {mem:.1f}/{maxmem:.1f} GB ({_safe_pct(mem, maxmem)} used)\n"
                f"  Disk: {disk:.1f}/{maxdisk:.1f} GB\n"
                f"  Network In: {netin:.1f} MB | Out: {netout:.1f} MB\n"
                f"  Uptime: {uptime_h:.1f} hours"
            )
        except Exception as e:
            return f"Error getting VM status: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_vm_config(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Get VM configuration: CPU, memory, disks, network, boot order."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/qemu/{vmid}/config")
            if not data:
                return f"No config data for VM {vmid}."
            lines = [f"VM {vmid} Configuration:"]
            important_keys = ["name", "memory", "cores", "sockets", "cpu", "ostype",
                              "boot", "machine", "bios", "agent", "onboot", "tags"]
            shown = set(important_keys)
            for key in important_keys:
                if key in data:
                    lines.append(f"  {key}: {data[key]}")
            for key in sorted(data.keys()):
                if any(key.startswith(p) for p in ("scsi", "virtio", "ide", "sata", "efidisk", "tpmstate")):
                    if key != "scsihw":  # scsihw shown in important_keys already if present
                        shown.add(key)
                        lines.append(f"  {key}: {data[key]}")
            for key in sorted(data.keys()):
                if key.startswith("net"):
                    shown.add(key)
                    lines.append(f"  {key}: {data[key]}")
            for key in sorted(data.keys()):
                if key not in shown and not any(key.startswith(p) for p in ("unused", "digest")):
                    lines.append(f"  {key}: {data[key]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting VM config: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_start(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Start a stopped VM."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/start")
            return f"VM {vmid} start initiated. Task: {data}"
        except Exception as e:
            return f"Error starting VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_stop(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Force stop a VM (power off). Use proxmox_vm_shutdown for graceful stop."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/stop")
            return f"VM {vmid} stop initiated. Task: {data}"
        except Exception as e:
            return f"Error stopping VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_shutdown(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID"),
        force_stop_after: Optional[int] = Field(None, description="Seconds before force stop if graceful shutdown fails")
    ) -> str:
        """Gracefully shut down a VM via ACPI."""
        err = _check_config()
        if err:
            return err
        try:
            body = {}
            if force_stop_after is not None:
                body["forceStop"] = 1
                body["timeout"] = force_stop_after
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/shutdown", json_body=body or None)
            return f"VM {vmid} shutdown initiated. Task: {data}"
        except Exception as e:
            return f"Error shutting down VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_reboot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Gracefully reboot a VM via ACPI."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/reboot")
            return f"VM {vmid} reboot initiated. Task: {data}"
        except Exception as e:
            return f"Error rebooting VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_reset(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Hard reset a VM (like pressing the physical reset button)."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/reset")
            return f"VM {vmid} reset initiated. Task: {data}"
        except Exception as e:
            return f"Error resetting VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_suspend(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID"),
        to_disk: bool = Field(False, description="Suspend to disk (hibernate) instead of RAM")
    ) -> str:
        """Suspend a VM to RAM or disk."""
        err = _check_config()
        if err:
            return err
        try:
            body = {"todisk": 1} if to_disk else {}
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/suspend", json_body=body or None)
            return f"VM {vmid} suspend initiated. Task: {data}"
        except Exception as e:
            return f"Error suspending VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_resume(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Resume a suspended VM."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/resume")
            return f"VM {vmid} resume initiated. Task: {data}"
        except Exception as e:
            return f"Error resuming VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_clone(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Source VM ID"),
        newid: int = Field(..., description="New VM ID for the clone"),
        name: Optional[str] = Field(None, description="Name for the cloned VM"),
        full: bool = Field(True, description="Full clone (true) or linked clone (false)"),
        target_node: Optional[str] = Field(None, description="Target node for cross-node cloning"),
        target_storage: Optional[str] = Field(None, description="Target storage pool")
    ) -> str:
        """Clone a VM to a new VMID."""
        err = _check_config()
        if err:
            return err
        try:
            body: dict = {"newid": newid}
            if name:
                body["name"] = name
            if full:
                body["full"] = 1
            if target_node:
                body["target"] = target_node
            if target_storage:
                body["storage"] = target_storage
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/clone", json_body=body)
            return f"VM {vmid} clone to {newid} initiated. Task: {data}"
        except Exception as e:
            return f"Error cloning VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_delete(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID to delete"),
        purge: bool = Field(False, description="Remove from all related configs (backup jobs, HA, replication)")
    ) -> str:
        """Delete a VM. The VM must be stopped first."""
        err = _check_config()
        if err:
            return err
        try:
            params = {"purge": 1} if purge else {}
            data = await config.do_request("DELETE", f"/nodes/{node}/qemu/{vmid}", params=params)
            return f"VM {vmid} deletion initiated. Task: {data}"
        except Exception as e:
            return f"Error deleting VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_migrate(
        node: str = Field(..., description="Source node"),
        vmid: int = Field(..., description="VM ID"),
        target: str = Field(..., description="Target node"),
        online: bool = Field(True, description="Live migration (true) or offline (false)")
    ) -> str:
        """Migrate a VM to another node."""
        err = _check_config()
        if err:
            return err
        try:
            body: dict = {"target": target}
            if online:
                body["online"] = 1
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/migrate", json_body=body)
            return f"VM {vmid} migration to {target} initiated. Task: {data}"
        except Exception as e:
            return f"Error migrating VM {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_resize_disk(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID"),
        disk: str = Field(..., description="Disk name (e.g. 'scsi0', 'virtio0')"),
        size: str = Field(..., description="New size or increment (e.g. '+10G', '50G')")
    ) -> str:
        """Resize a VM disk. Prefix with '+' to add to current size (e.g. '+10G')."""
        err = _check_config()
        if err:
            return err
        try:
            await config.do_request("PUT", f"/nodes/{node}/qemu/{vmid}/resize", json_body={"disk": disk, "size": size})
            return f"VM {vmid} disk {disk} resized to {size}."
        except Exception as e:
            return f"Error resizing disk: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_update_config(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID"),
        settings: str = Field(..., description='JSON object with settings to update. E.g. {"memory": 4096, "cores": 2, "onboot": 1, "tags": "tag1;tag2"}')
    ) -> str:
        """Update VM configuration. Some changes require a reboot."""
        err = _check_config()
        if err:
            return err
        try:
            body = json.loads(settings)
            await config.do_request("PUT", f"/nodes/{node}/qemu/{vmid}/config", json_body=body)
            return f"VM {vmid} configuration updated: {list(body.keys())}"
        except json.JSONDecodeError:
            return "Error: 'settings' must be valid JSON."
        except Exception as e:
            return f"Error updating VM config: {e}"

    # =========================================================================
    # Container (LXC) Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_containers(
        node: Optional[str] = Field(None, description="Node name. Leave empty for all nodes.")
    ) -> str:
        """List all LXC containers with status, CPU, and memory usage."""
        err = _check_config()
        if err:
            return err
        try:
            if node:
                nodes = [node]
            else:
                node_data = await config.do_request("GET", "/nodes")
                nodes = [n["node"] for n in node_data if n.get("status") == "online"]
            all_cts = []
            for n in nodes:
                try:
                    cts = await config.do_request("GET", f"/nodes/{n}/lxc")
                    for ct in (cts or []):
                        ct["_node"] = n
                        all_cts.append(ct)
                except Exception:
                    pass
            if not all_cts:
                return "No containers found."
            lines = [f"Found {len(all_cts)} containers:"]
            for ct in sorted(all_cts, key=lambda x: x.get("vmid", 0)):
                vmid = ct.get("vmid", "?")
                name = ct.get("name", "unnamed")
                status = ct.get("status", "unknown")
                cpu = ct.get("cpu", 0)
                cpus = ct.get("cpus", 0)
                mem = ct.get("mem", 0) / (1024**3) if ct.get("mem") else 0
                maxmem = ct.get("maxmem", 0) / (1024**3) if ct.get("maxmem") else 0
                cpu_pct = f"{cpu * 100:.1f}%" if cpu else "N/A"
                lines.append(f"  {vmid}: {name} | Node: {ct['_node']} | Status: {status} | CPU: {cpu_pct} ({cpus} cores) | RAM: {mem:.1f}/{maxmem:.1f} GB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing containers: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_container_status(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Get detailed status of an LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/lxc/{vmid}/status/current")
            if not data:
                return f"No status data for container {vmid}."
            name = data.get("name", "unnamed")
            status = data.get("status", "unknown")
            cpu = data.get("cpu", 0)
            cpus = data.get("cpus", 0)
            mem = data.get("mem", 0) / (1024**3) if data.get("mem") else 0
            maxmem = data.get("maxmem", 0) / (1024**3) if data.get("maxmem") else 0
            disk = data.get("disk", 0) / (1024**3) if data.get("disk") else 0
            maxdisk = data.get("maxdisk", 0) / (1024**3) if data.get("maxdisk") else 0
            netin = data.get("netin", 0) / (1024**2) if data.get("netin") else 0
            netout = data.get("netout", 0) / (1024**2) if data.get("netout") else 0
            uptime_h = data.get("uptime", 0) / 3600
            return (
                f"Container {vmid}: {name}\n"
                f"  Status: {status}\n"
                f"  CPU: {cpu * 100:.1f}% ({cpus} cores)\n"
                f"  Memory: {mem:.1f}/{maxmem:.1f} GB ({_safe_pct(mem, maxmem)} used)\n"
                f"  Disk: {disk:.1f}/{maxdisk:.1f} GB\n"
                f"  Network In: {netin:.1f} MB | Out: {netout:.1f} MB\n"
                f"  Uptime: {uptime_h:.1f} hours"
            )
        except Exception as e:
            return f"Error getting container status: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_container_config(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Get LXC container configuration."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/lxc/{vmid}/config")
            if not data:
                return f"No config data for container {vmid}."
            lines = [f"Container {vmid} Configuration:"]
            important_keys = ["hostname", "memory", "swap", "cores", "ostype",
                              "arch", "onboot", "unprivileged", "tags", "description",
                              "features", "protection", "startup", "hookscript"]
            shown = set(important_keys)
            for key in important_keys:
                if key in data:
                    lines.append(f"  {key}: {data[key]}")
            for key in sorted(data.keys()):
                if key in ("rootfs",) or key.startswith("mp"):
                    shown.add(key)
                    lines.append(f"  {key}: {data[key]}")
            for key in sorted(data.keys()):
                if key.startswith("net"):
                    shown.add(key)
                    lines.append(f"  {key}: {data[key]}")
            for key in sorted(data.keys()):
                if key not in shown and not key.startswith("digest"):
                    lines.append(f"  {key}: {data[key]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting container config: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_start(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Start a stopped LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/status/start")
            return f"Container {vmid} start initiated. Task: {data}"
        except Exception as e:
            return f"Error starting container {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_stop(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Force stop an LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/status/stop")
            return f"Container {vmid} stop initiated. Task: {data}"
        except Exception as e:
            return f"Error stopping container {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_shutdown(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID"),
        force_stop_after: Optional[int] = Field(None, description="Seconds before force stop")
    ) -> str:
        """Gracefully shut down an LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            body = {}
            if force_stop_after is not None:
                body["forceStop"] = 1
                body["timeout"] = force_stop_after
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/status/shutdown", json_body=body or None)
            return f"Container {vmid} shutdown initiated. Task: {data}"
        except Exception as e:
            return f"Error shutting down container {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_reboot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Reboot an LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/status/reboot")
            return f"Container {vmid} reboot initiated. Task: {data}"
        except Exception as e:
            return f"Error rebooting container {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_clone(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Source container ID"),
        newid: int = Field(..., description="New container ID"),
        hostname: Optional[str] = Field(None, description="Hostname for the clone"),
        full: bool = Field(True, description="Full clone (true) or linked clone (false)"),
        target_node: Optional[str] = Field(None, description="Target node"),
        target_storage: Optional[str] = Field(None, description="Target storage pool")
    ) -> str:
        """Clone an LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            body: dict = {"newid": newid}
            if hostname:
                body["hostname"] = hostname
            if full:
                body["full"] = 1
            if target_node:
                body["target"] = target_node
            if target_storage:
                body["storage"] = target_storage
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/clone", json_body=body)
            return f"Container {vmid} clone to {newid} initiated. Task: {data}"
        except Exception as e:
            return f"Error cloning container {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_delete(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID to delete"),
        purge: bool = Field(False, description="Remove from all related configs")
    ) -> str:
        """Delete an LXC container. Must be stopped first."""
        err = _check_config()
        if err:
            return err
        try:
            params = {"purge": 1} if purge else {}
            data = await config.do_request("DELETE", f"/nodes/{node}/lxc/{vmid}", params=params)
            return f"Container {vmid} deletion initiated. Task: {data}"
        except Exception as e:
            return f"Error deleting container {vmid}: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_migrate(
        node: str = Field(..., description="Source node"),
        vmid: int = Field(..., description="Container ID"),
        target: str = Field(..., description="Target node"),
        restart: bool = Field(False, description="Restart container after migration")
    ) -> str:
        """Migrate an LXC container to another node."""
        err = _check_config()
        if err:
            return err
        try:
            body: dict = {"target": target}
            if restart:
                body["restart"] = 1
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/migrate", json_body=body)
            return f"Container {vmid} migration to {target} initiated. Task: {data}"
        except Exception as e:
            return f"Error migrating container {vmid}: {e}"

    # =========================================================================
    # Storage Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_storage(
        node: Optional[str] = Field(None, description="Node name. Leave empty for all storage.")
    ) -> str:
        """List all storage pools with type, usage, and status."""
        err = _check_config()
        if err:
            return err
        try:
            endpoint = f"/nodes/{node}/storage" if node else "/storage"
            data = await config.do_request("GET", endpoint)
            if not data:
                return "No storage found."
            lines = [f"Found {len(data)} storage pools:"]
            for s in sorted(data, key=lambda x: x.get("storage", "")):
                name = s.get("storage", "unknown")
                stype = s.get("type", "unknown")
                content = s.get("content", "N/A")
                active = "Active" if s.get("active", s.get("enabled")) else "Inactive"
                total = s.get("total", 0) / (1024**3) if s.get("total") else 0
                used = s.get("used", 0) / (1024**3) if s.get("used") else 0
                if total > 0:
                    lines.append(f"  {name} | Type: {stype} | Content: {content} | {active} | Used: {used:.1f}/{total:.1f} GB ({_safe_pct(used, total)})")
                else:
                    lines.append(f"  {name} | Type: {stype} | Content: {content} | {active}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing storage: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_storage_content(
        node: str = Field(..., description="Node name"),
        storage: str = Field(..., description="Storage pool name"),
        content_type: Optional[str] = Field(None, description="Filter: images, rootdir, vztmpl, backup, iso, snippets")
    ) -> str:
        """List contents of a storage pool (ISOs, backups, disk images, templates)."""
        err = _check_config()
        if err:
            return err
        try:
            params = {}
            if content_type:
                params["content"] = content_type
            data = await config.do_request("GET", f"/nodes/{node}/storage/{storage}/content", params=params)
            if not data:
                return f"No content found in storage '{storage}'."
            lines = [f"Found {len(data)} items in '{storage}':"]
            for item in sorted(data, key=lambda x: x.get("volid", "")):
                volid = item.get("volid", "unknown")
                fmt = item.get("format", "unknown")
                size = item.get("size", 0) / (1024**3) if item.get("size") else 0
                ctype = item.get("content", "unknown")
                lines.append(f"  {volid} | Type: {ctype} | Format: {fmt} | Size: {size:.2f} GB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing storage content: {e}"

    # =========================================================================
    # Snapshot Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_snapshots(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID"),
        vm_type: str = Field("qemu", description="'qemu' for VMs, 'lxc' for containers")
    ) -> str:
        """List all snapshots for a VM or container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/{vm_type}/{vmid}/snapshot")
            if not data:
                return f"No snapshots found for {vm_type}/{vmid}."
            from datetime import datetime
            lines = [f"Snapshots for {vm_type}/{vmid}:"]
            for snap in data:
                name = snap.get("name", "unknown")
                desc = snap.get("description", "")
                snaptime = snap.get("snaptime", 0)
                parent = snap.get("parent", "")
                if name == "current":
                    lines.append(f"  [current] You are here (parent: {parent})")
                else:
                    time_str = datetime.fromtimestamp(snaptime).strftime("%Y-%m-%d %H:%M:%S") if snaptime else "N/A"
                    lines.append(f"  {name} | Created: {time_str} | Description: {desc or 'N/A'}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing snapshots: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_create_snapshot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID"),
        snapname: str = Field(..., description="Snapshot name (alphanumeric, no spaces)"),
        description: Optional[str] = Field(None, description="Snapshot description"),
        vm_type: str = Field("qemu", description="'qemu' for VMs, 'lxc' for containers"),
        vmstate: bool = Field(False, description="Include VM RAM state (QEMU only, VM must be running)")
    ) -> str:
        """Create a snapshot of a VM or container."""
        err = _check_config()
        if err:
            return err
        try:
            body: dict = {"snapname": snapname}
            if description:
                body["description"] = description
            if vmstate and vm_type == "qemu":
                body["vmstate"] = 1
            data = await config.do_request("POST", f"/nodes/{node}/{vm_type}/{vmid}/snapshot", json_body=body)
            return f"Snapshot '{snapname}' creation initiated for {vm_type}/{vmid}. Task: {data}"
        except Exception as e:
            return f"Error creating snapshot: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_delete_snapshot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID"),
        snapname: str = Field(..., description="Snapshot name to delete"),
        vm_type: str = Field("qemu", description="'qemu' for VMs, 'lxc' for containers")
    ) -> str:
        """Delete a snapshot from a VM or container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("DELETE", f"/nodes/{node}/{vm_type}/{vmid}/snapshot/{snapname}")
            return f"Snapshot '{snapname}' deletion initiated. Task: {data}"
        except Exception as e:
            return f"Error deleting snapshot: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_rollback_snapshot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID"),
        snapname: str = Field(..., description="Snapshot name to rollback to"),
        vm_type: str = Field("qemu", description="'qemu' for VMs, 'lxc' for containers")
    ) -> str:
        """Rollback a VM or container to a snapshot. WARNING: overwrites current state."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/{vm_type}/{vmid}/snapshot/{snapname}/rollback")
            return f"Rollback to snapshot '{snapname}' initiated. Task: {data}"
        except Exception as e:
            return f"Error rolling back snapshot: {e}"

    # =========================================================================
    # Task Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_tasks(
        node: Optional[str] = Field(None, description="Node name. Leave empty for cluster-wide tasks."),
        limit: int = Field(20, description="Max tasks to return (1-100)"),
        vmid: Optional[int] = Field(None, description="Filter by VM/container ID"),
        status_filter: Optional[str] = Field(None, description="Filter by status: 'running', 'ok', 'error'")
    ) -> str:
        """List recent cluster tasks (backups, migrations, clones, etc.)."""
        err = _check_config()
        if err:
            return err
        try:
            endpoint = f"/nodes/{node}/tasks" if node else "/cluster/tasks"
            params: dict = {"limit": min(limit, 100)}
            if vmid is not None:
                params["vmid"] = vmid
            data = await config.do_request("GET", endpoint, params=params)
            if not data:
                return "No tasks found."
            from datetime import datetime
            filtered = []
            for task in data:
                task_status = task.get("status", "running")
                if status_filter and task_status != status_filter:
                    continue
                filtered.append(task)
            lines = [f"Found {len(filtered)} tasks:"]
            for task in filtered:
                task_type = task.get("type", "unknown")
                task_status = task.get("status", "running")
                task_node = task.get("node", "")
                starttime = task.get("starttime", 0)
                endtime = task.get("endtime", 0)
                user = task.get("user", "")
                task_vmid = task.get("id", "")
                start_str = datetime.fromtimestamp(starttime).strftime("%Y-%m-%d %H:%M:%S") if starttime else "N/A"
                duration = f" | Duration: {endtime - starttime}s" if endtime and starttime else ""
                lines.append(f"  [{task_status}] {task_type} | Node: {task_node} | VMID: {task_vmid} | User: {user} | Started: {start_str}{duration}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing tasks: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_task_status(
        node: str = Field(..., description="Node name"),
        upid: str = Field(..., description="Task UPID")
    ) -> str:
        """Get the status and log output of a specific task."""
        err = _check_config()
        if err:
            return err
        try:
            status = await config.do_request("GET", f"/nodes/{node}/tasks/{upid}/status")
            log_data = await config.do_request("GET", f"/nodes/{node}/tasks/{upid}/log", params={"limit": 50})
            lines = [
                "Task Status:",
                f"  Type: {status.get('type', 'N/A')}",
                f"  Status: {status.get('status', 'N/A')}",
                f"  Exit Status: {status.get('exitstatus', 'N/A')}",
                f"  Node: {status.get('node', 'N/A')}",
                f"  User: {status.get('user', 'N/A')}",
            ]
            if log_data:
                lines.append(f"\nTask Log (last {len(log_data)} lines):")
                for entry in log_data:
                    lines.append(f"  {entry.get('t', '')}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting task status: {e}"

    # =========================================================================
    # Backup Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_backups(
        node: str = Field(..., description="Node name"),
        storage: str = Field(..., description="Storage pool name"),
        vmid: Optional[int] = Field(None, description="Filter by VM/container ID")
    ) -> str:
        """List available backups on a storage pool."""
        err = _check_config()
        if err:
            return err
        try:
            params: dict = {"content": "backup"}
            if vmid is not None:
                params["vmid"] = vmid
            data = await config.do_request("GET", f"/nodes/{node}/storage/{storage}/content", params=params)
            if not data:
                return f"No backups found on storage '{storage}'."
            from datetime import datetime
            lines = [f"Found {len(data)} backups on '{storage}':"]
            for item in sorted(data, key=lambda x: x.get("ctime", 0), reverse=True):
                volid = item.get("volid", "unknown")
                size = item.get("size", 0) / (1024**3) if item.get("size") else 0
                fmt = item.get("format", "unknown")
                ctime = item.get("ctime", 0)
                time_str = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S") if ctime else "N/A"
                notes = item.get("notes", "")
                lines.append(f"  {volid} | Size: {size:.2f} GB | Format: {fmt} | Created: {time_str}" + (f" | Notes: {notes}" if notes else ""))
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing backups: {e}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_create_backup(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID to backup"),
        storage: str = Field(..., description="Target storage pool"),
        mode: str = Field("snapshot", description="Backup mode: 'snapshot' (no downtime), 'suspend', 'stop'"),
        compress: str = Field("zstd", description="Compression: 'zstd' (recommended), 'lzo', 'gzip', 'none'"),
        notes: Optional[str] = Field(None, description="Notes for the backup")
    ) -> str:
        """Create a backup of a VM or container."""
        err = _check_config()
        if err:
            return err
        try:
            body: dict = {"vmid": str(vmid), "storage": storage, "mode": mode, "compress": compress}
            if notes:
                body["notes-template"] = notes
            data = await config.do_request("POST", f"/nodes/{node}/vzdump", json_body=body)
            return f"Backup of {vmid} initiated on storage '{storage}'. Task: {data}"
        except Exception as e:
            return f"Error creating backup: {e}"

    # =========================================================================
    # Pool, Template & ISO Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_pools() -> str:
        """List all resource pools."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", "/pools")
            if not data:
                return "No pools found."
            lines = [f"Found {len(data)} pools:"]
            for pool in sorted(data, key=lambda x: x.get("poolid", "")):
                poolid = pool.get("poolid", "unknown")
                comment = pool.get("comment", "")
                lines.append(f"  {poolid}" + (f" - {comment}" if comment else ""))
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing pools: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_templates(
        node: str = Field(..., description="Node name"),
        storage: str = Field(..., description="Storage pool name")
    ) -> str:
        """List available LXC container templates."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/storage/{storage}/content", params={"content": "vztmpl"})
            if not data:
                return f"No templates found on storage '{storage}'."
            lines = [f"Found {len(data)} templates on '{storage}':"]
            for t in sorted(data, key=lambda x: x.get("volid", "")):
                volid = t.get("volid", "unknown")
                size = t.get("size", 0) / (1024**2) if t.get("size") else 0
                lines.append(f"  {volid} | Size: {size:.1f} MB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing templates: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_isos(
        node: str = Field(..., description="Node name"),
        storage: str = Field(..., description="Storage pool name")
    ) -> str:
        """List available ISO images."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/storage/{storage}/content", params={"content": "iso"})
            if not data:
                return f"No ISOs found on storage '{storage}'."
            lines = [f"Found {len(data)} ISOs on '{storage}':"]
            for iso in sorted(data, key=lambda x: x.get("volid", "")):
                volid = iso.get("volid", "unknown")
                size = iso.get("size", 0) / (1024**3) if iso.get("size") else 0
                lines.append(f"  {volid} | Size: {size:.2f} GB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing ISOs: {e}"

    # =========================================================================
    # Firewall & HA Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_firewall_rules(
        node: Optional[str] = Field(None, description="Node name. Omit for cluster-level rules."),
        vmid: Optional[int] = Field(None, description="VM/container ID for VM-level rules"),
        vm_type: str = Field("qemu", description="'qemu' or 'lxc' (only used with vmid)")
    ) -> str:
        """List firewall rules at cluster, node, or VM/container level."""
        err = _check_config()
        if err:
            return err
        try:
            if vmid and node:
                endpoint = f"/nodes/{node}/{vm_type}/{vmid}/firewall/rules"
            elif node:
                endpoint = f"/nodes/{node}/firewall/rules"
            else:
                endpoint = "/cluster/firewall/rules"
            data = await config.do_request("GET", endpoint)
            if not data:
                return "No firewall rules found."
            lines = [f"Found {len(data)} firewall rules:"]
            for rule in data:
                pos = rule.get("pos", "?")
                action = rule.get("action", "?")
                rtype = rule.get("type", "?")
                enabled = "Enabled" if rule.get("enable") else "Disabled"
                source = rule.get("source", "any")
                dest = rule.get("dest", "any")
                proto = rule.get("proto", "any")
                dport = rule.get("dport", "any")
                comment = rule.get("comment", "")
                lines.append(f"  #{pos} [{enabled}] {action} {rtype} | Proto: {proto} | Src: {source} | Dst: {dest} | Port: {dport}" + (f" | {comment}" if comment else ""))
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing firewall rules: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_ha_status() -> str:
        """Get High Availability (HA) status and managed resources."""
        err = _check_config()
        if err:
            return err
        try:
            status = await config.do_request("GET", "/cluster/ha/status/current")
            resources = await config.do_request("GET", "/cluster/ha/resources")
            lines = ["HA Manager Status:"]
            if isinstance(status, list):
                for item in status:
                    lines.append(f"  {item.get('id', 'N/A')}: {item.get('status', 'N/A')} (type: {item.get('type', 'N/A')})")
            lines.append(f"\nHA Resources ({len(resources) if resources else 0}):")
            if resources:
                for r in resources:
                    sid = r.get("sid", "unknown")
                    state = r.get("state", "unknown")
                    group = r.get("group", "none")
                    lines.append(f"  {sid} | State: {state} | Group: {group}")
            else:
                lines.append("  No HA resources configured.")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting HA status: {e}"

    logger.info("Proxmox VE tools registered successfully")
