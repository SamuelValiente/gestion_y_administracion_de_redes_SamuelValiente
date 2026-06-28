#!/usr/bin/env python3
"""
manage_vms.py — Gestor de Infraestructura Libvirt/KVM (PXE-Ready / Cloud-Init / Ansible)
========================================================================================
Autor : Infraestructura Automatizada (Migrado a Libvirt por Samuel Valiente)
Versión: 3.0.0
Python : 3.8+

Descripción
-----------
Script CLI modular para gestionar el ciclo de vida completo de una
infraestructura virtual en Libvirt/KVM diseñada para arranque PXE,
instalación desatendida con Cloud-Init y gestión posterior con Ansible.
"""

import argparse
import logging
import subprocess
import sys
import time
import os
import re
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# COLORES ANSI PARA LOGGING
# ─────────────────────────────────────────────────────────────────────────────

class AnsiColor:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    GREY    = "\033[90m"


class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG:    AnsiColor.GREY,
        logging.INFO:     AnsiColor.GREEN,
        logging.WARNING:  AnsiColor.YELLOW,
        logging.ERROR:    AnsiColor.RED,
        logging.CRITICAL: AnsiColor.MAGENTA,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, AnsiColor.RESET)
        level_tag = f"{color}{record.levelname:<8}{AnsiColor.RESET}"
        msg = super().format(record)
        return msg.replace(record.levelname, level_tag, 1)


def setup_logger(name: str = "manage_vms", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        fmt = ColorFormatter(fmt="%(levelname)s [%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


log = setup_logger()

# ─────────────────────────────────────────────────────────────────────────────
# DEFINICIÓN DE DATOS: REDES Y NODOS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NetworkConfig:
    name: str
    cidr: str
    description: str = ""


@dataclass
class NodeConfig:
    name: str
    networks: list
    ram_mb: int = 1024
    cpus: int = 1
    disk_gb: int = 20
    mac_addresses: dict = field(default_factory=dict)
    promiscuous: bool = False
    boot_order: str = "net"
    description: str = ""


NETWORKS: dict[str, NetworkConfig] = {
    "main": NetworkConfig(name="main", cidr="192.168.1.0/24", description="Red principal: jumpstart, router, lvs01, web0x, hd0x"),
    "internal": NetworkConfig(name="internal", cidr="10.0.0.0/24", description="Red interna: router, mst0x, wrk0x, mon01"),
    "ext": NetworkConfig(name="ext", cidr="10.0.2.0/24", description="Red externa/semi-pública: lvs01, salida a internet vía NAT"),
}

NODES: dict[str, NodeConfig] = {
    "router": NodeConfig(name="router", networks=["main", "internal"], ram_mb=2048, cpus=2, disk_gb=20, mac_addresses={"main": "08:00:27:00:00:01", "internal": "08:00:27:00:00:02"}, promiscuous=True, boot_order="net", description="Router dual-homed entre red main e internal"),
    "lvs01": NodeConfig(name="lvs01", networks=["main", "ext"], ram_mb=2048, cpus=2, disk_gb=20, mac_addresses={"main": "08:00:27:00:01:0A", "ext": "08:00:27:00:01:0F"}, boot_order="net", description="Balanceador de carga LVS – main + ext"),
    "web01": NodeConfig(name="web01", networks=["main"], ram_mb=2048, cpus=1, disk_gb=20, mac_addresses={"main": "08:00:27:00:01:0B"}, boot_order="net", description="Servidor web 01 – red main"),
    "web02": NodeConfig(name="web02", networks=["main"], ram_mb=2048, cpus=1, disk_gb=20, mac_addresses={"main": "08:00:27:00:01:0C"}, boot_order="net", description="Servidor web 02 – red main"),
    "hd01": NodeConfig(name="hd01", networks=["main"], ram_mb=2048, cpus=2, disk_gb=30, mac_addresses={}, boot_order="net", description="Puesto de trabajo 01 – MAC dinámica – red main"),
    "hd02": NodeConfig(name="hd02", networks=["main"], ram_mb=2048, cpus=2, disk_gb=30, mac_addresses={}, boot_order="net", description="Puesto de trabajo 02 – MAC dinámica – red main"),
    "mst01": NodeConfig(name="mst01", networks=["internal"], ram_mb=2048, cpus=2, disk_gb=40, mac_addresses={"internal": "08:00:27:00:02:0A"}, boot_order="net", description="Nodo master 01 – red internal"),
    "mst02": NodeConfig(name="mst02", networks=["internal"], ram_mb=2048, cpus=2, disk_gb=40, mac_addresses={"internal": "08:00:27:00:02:0B"}, boot_order="net", description="Nodo master 02 – red internal"),
    "wrk01": NodeConfig(name="wrk01", networks=["internal"], ram_mb=2048, cpus=2, disk_gb=40, mac_addresses={"internal": "08:00:27:00:02:0C"}, boot_order="net", description="Worker 01 – red internal"),
    "wrk02": NodeConfig(name="wrk02", networks=["internal"], ram_mb=2048, cpus=2, disk_gb=40, mac_addresses={"internal": "08:00:27:00:02:0D"}, boot_order="net", description="Worker 02 – red internal"),
    "mon01": NodeConfig(name="mon01", networks=["internal"], ram_mb=2048, cpus=2, disk_gb=40, mac_addresses={"internal": "08:00:27:00:02:0E"}, boot_order="net", description="Nodo de monitorización – red internal"),
}

ALL_NODES = list(NODES.keys())

NODE_TEMPLATES: dict[str, NodeConfig] = {
    "web": NodeConfig(name="__template_web__", networks=["main"], ram_mb=2048, cpus=1, disk_gb=20, description="Frontal web – red main"),
    "worker": NodeConfig(name="__template_worker__", networks=["internal"], ram_mb=2048, cpus=2, disk_gb=40, description="Worker del clúster – red internal"),
    "master": NodeConfig(name="__template_master__", networks=["internal"], ram_mb=2048, cpus=2, disk_gb=40, description="Master del clúster – red internal"),
    "hotdesk": NodeConfig(name="__template_hotdesk__", networks=["main"], ram_mb=2048, cpus=2, disk_gb=30, description="Puesto hot-desk – MAC dinámica – red main"),
}

# ─────────────────────────────────────────────────────────────────────────────
# CLASE PRINCIPAL REFACTORIZADA: LibvirtManager
# ─────────────────────────────────────────────────────────────────────────────

class LibvirtManager:
    """Gestor de ciclo de vida para la infraestructura Libvirt/KVM PXE-Ready."""

    BOOT_TIMEOUT = 120
    SHUTDOWN_TIMEOUT = 60

    def __init__(self, headless: bool = True, dry_run: bool = False):
        self.dry_run = dry_run
        if dry_run:
            log.warning("Modo DRY-RUN activo: ningún comando será ejecutado realmente.")

    def _run(self, cmd: list[str], check: bool = True, capture: bool = True, input_data: str = None) -> subprocess.CompletedProcess:
        """Ejecuta un comando del sistema de forma segura forzando locale en inglés."""
        if self.dry_run:
            log.debug(f"[DRY-RUN] {' '.join(cmd)}")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        log.debug(f"Ejecutando: {' '.join(cmd)}")
        env = os.environ.copy()
        env['LANG'] = 'C'  # Forzar salida en inglés para consistencia
        try:
            return subprocess.run(cmd, check=check, capture_output=capture, text=True, input=input_data, env=env)
        except subprocess.CalledProcessError as exc:
            log.error(f"Error en comando: {' '.join(cmd)}\nstdout: {exc.stdout}\nstderr: {exc.stderr}")
            raise

    def _vm_exists(self, name: str) -> bool:
        res = self._run(["virsh", "dominfo", name], check=False)
        return res.returncode == 0

    def _network_exists(self, net_name: str) -> bool:
        res = self._run(["virsh", "net-info", net_name], check=False)
        return res.returncode == 0

    def _get_vm_state(self, name: str) -> str:
        res = self._run(["virsh", "domstate", name], check=False)
        if res.returncode != 0:
            return "unknown"
        return res.stdout.strip().lower()

    def _wait_for_state(self, name: str, target_state: str, timeout: int = 60) -> bool:
        elapsed = 0
        interval = 3
        log.info(f"  Esperando estado '{target_state}' para '{name}' (máx. {timeout}s)…")
        while elapsed < timeout:
            state = self._get_vm_state(name)
            if target_state in state:
                return True
            time.sleep(interval)
            elapsed += interval
        log.warning(f"  Timeout: '{name}' no alcanzó el estado '{target_state}'")
        return False

    # ─────────────────────────────────────────────────────────────────────
    # Gestión de Redes
    # ─────────────────────────────────────────────────────────────────────

    def create_networks(self) -> None:
        log.info("─" * 60)
        log.info("Verificando y definiendo redes en Libvirt…")
        
        for net_name, net_cfg in NETWORKS.items():
            if self._network_exists(net_name):
                log.info(f"  ✓ Red '{net_name}' ya existe en Libvirt.")
                continue

            # Generar XML dinámico según el tipo de red
            if net_name == "ext":
                xml_net = f"""<network>
                  <name>{net_name}</name>
                  <forward mode='nat'/>
                  <bridge name='virbr_{net_name}' stp='on' delay='0'/>
                  <ip address='10.0.2.1' netmask='255.255.255.0'>
                    <dhcp>
                      <range start='10.0.2.2' end='10.0.2.254'/>
                    </dhcp>
                  </ip>
                </network>"""
            else:
                # Redes aisladas (main e internal) sin DHCP (lo provee jumpstart)
                xml_net = f"""<network>
                  <name>{net_name}</name>
                  <bridge name='virbr_{net_name}' stp='on' delay='0'/>
                </network>"""

            log.info(f"  -> Definiendo red '{net_name}' ({net_cfg.cidr})…")
            self._run(["virsh", "net-define", "/dev/stdin"], input_data=xml_net)
            self._run(["virsh", "net-start", net_name])
            self._run(["virsh", "net-autostart", net_name])

    # ─────────────────────────────────────────────────────────────────────
    # Creación de Nodos
    # ─────────────────────────────────────────────────────────────────────

    def create_node(self, name: str) -> bool:
        if name not in NODES:
            log.error(f"Nodo desconocido: '{name}'. Nodos válidos: {ALL_NODES}")
            return False

        node = NODES[name]
        if self._vm_exists(name):
            log.warning(f"  [{name}] Ya existe en Libvirt. Omitiendo.")
            return True

        log.info(f"  [{name}] Creando máquina virtual con virt-install…")

        try:
            # Construcción de comando virt-install directo para PXE
            cmd = [
                "virt-install",
                "--name", name,
                "--memory", str(node.ram_mb),
                "--vcpus", str(node.cpus),
                "--disk", f"size={node.disk_gb},format=qcow2,bus=virtio",
                "--os-variant", "generic",
                "--boot", "network,hd",  # Intentar red (PXE) primero, luego disco
                "--pxe",
                "--noautoconsole"
            ]

            # Inyectar interfaces de red configuradas
            for net_name in node.networks:
                net_arg = f"network={net_name},model=virtio"
                if net_name in node.mac_addresses and node.mac_addresses[net_name]:
                    net_arg += f",mac={node.mac_addresses[net_name]}"
                cmd.extend(["--network", net_arg])

            self._run(cmd)
            log.info(f"  {AnsiColor.GREEN}✓{AnsiColor.RESET} [{name}] Creada y lista para arranque PXE.")
            return True
        except Exception:
            log.error(f"  [{name}] Falló el despliegue.")
            return False

    # ─────────────────────────────────────────────────────────────────────
    # Orden de Arranque (Usa virt-xml para edición limpia)
    # ─────────────────────────────────────────────────────────────────────

    def set_boot_order(self, vm_name: str, mode: str) -> bool:
        if vm_name not in NODES or not self._vm_exists(vm_name):
            log.error(f"La máquina '{vm_name}' no es válida o no existe.")
            return False

        state = self._get_vm_state(vm_name)
        if "shut off" not in state:
            log.warning(f"  [{vm_name}] Está '{state}'. Debe estar apagada para cambiar el arranque.")
            return False

        boot_order = "network,hd" if mode == "net" else "hd,network"
        log.info(f"  [{vm_name}] Modificando boot order a '{mode}' mediante virt-xml…")
        try:
            self._run(["virt-xml", vm_name, "--edit", f"--boot={boot_order}"])
            log.info(f"  {AnsiColor.GREEN}✓{AnsiColor.RESET} [{vm_name}] Orden de arranque actualizado.")
            return True
        except Exception:
            return False

    # ─────────────────────────────────────────────────────────────────────
    # Ciclo de Vida
    # ─────────────────────────────────────────────────────────────────────

    def start(self, name: str) -> bool:
        if not self._vm_exists(name):
            log.error(f"  [{name}] No existe.")
            return False
        if "running" in self._get_vm_state(name):
            log.warning(f"  [{name}] Ya está corriendo.")
            return True
        
        log.info(f"  [{name}] Levantando máquina en Libvirt…")
        self._run(["virsh", "start", name])
        return self._wait_for_state(name, "running", self.BOOT_TIMEOUT)

    def stop(self, name: str, force: bool = False) -> bool:
        if not self._vm_exists(name):
            return False
        state = self._get_vm_state(name)
        if "shut off" in state:
            return True

        if force:
            log.info(f"  [{name}] Destruyendo instancia (poweroff forzado)…")
            self._run(["virsh", "destroy", name], check=False)
        else:
            log.info(f"  [{name}] Enviando señal ACPI de apagado…")
            self._run(["virsh", "shutdown", name], check=False)

        return self._wait_for_state(name, "shut off", self.SHUTDOWN_TIMEOUT)

    def delete(self, name: str, force: bool = False) -> bool:
        if not self._vm_exists(name):
            return True
        if "running" in self._get_vm_state(name):
            if not force:
                log.error(f"  [{name}] Está corriendo. Usa --force.")
                return False
            self.stop(name, force=True)

        log.info(f"  [{name}] Eliminando definición y borrando almacenamiento asociado…")
        self._run(["virsh", "undefine", name, "--remove-all-storage"])
        return True

    def add_node(self, node_type: str, name: str, mac: Optional[str] = None) -> bool:
        if node_type not in NODE_TEMPLATES:
            return False
        template = NODE_TEMPLATES[node_type]
        mac_addresses = {template.networks[0]: mac} if mac else {}
        
        new_node = NodeConfig(
            name=name, networks=list(template.networks), ram_mb=template.ram_mb,
            cpus=template.cpus, disk_gb=template.disk_gb, mac_addresses=mac_addresses,
            promiscuous=template.promiscuous, boot_order="net", description=f"{template.description} (dinámico)"
        )
        NODES[name] = new_node
        if name not in ALL_NODES:
            ALL_NODES.append(name)
        return self.create_node(name)

    def status(self, name: Optional[str] = None) -> None:
        targets = [name] if name else list(ALL_NODES)
        log.info("─" * 60)
        log.info(f"  {'NODO':<12} {'ESTADO':<14} {'REDES':<22} {'BOOT':>6}")
        log.info("─" * 60)

        for n in targets:
            if not self._vm_exists(n):
                state_str = f"{AnsiColor.GREY}no creada{AnsiColor.RESET}"
                boot_str = "─"
                nets_str = "+".join(NODES[n].networks) if n in NODES else "unknown"
            else:
                raw_state = self._get_vm_state(n)
                state_str = f"{AnsiColor.GREEN}running{AnsiColor.RESET}" if "running" in raw_state else f"{AnsiColor.RED}shut off{AnsiColor.RESET}"
                
                # Detectar orden de arranque leyendo XML
                xml = self._run(["virsh", "dumpxml", n]).stdout
                boot_devices = re.findall(r"<boot dev='(.*?)'/>", xml)
                boot_str = boot_devices[0] if boot_devices else "hd"
                nets_str = "+".join(re.findall(r"<source network='([^']*)'", xml))

            print(f"  {n:<12} {state_str:<23} {nets_str:<22} {boot_str:>6}")
        log.info("─" * 60)

    # Métodos en masa heredados de la lógica original
    def create_all(self) -> None:
        self.create_networks()
        for n in ALL_NODES: self.create_node(n)
    def start_all(self) -> None:
        for n in ALL_NODES: self.start(n)
    def stop_all(self, force: bool = False) -> None:
        for n in reversed(ALL_NODES): self.stop(n, force=force)
    def delete_all(self, force: bool = False) -> None:
        for n in reversed(ALL_NODES): self.delete(n, force=force)
    def set_boot_order_all(self, mode: str) -> None:
        for n in ALL_NODES: self.set_boot_order(n, mode)

# ─────────────────────────────────────────────────────────────────────────────
# CLI MANTENIDO EXACTAMENTE IGUAL
# ─────────────────────────────────────────────────────────────────────────────

def resolve_targets(target_list: list[str]) -> list[str]:
    if "all" in target_list: return ALL_NODES
    return target_list

def main() -> None:
    # Ajustamos el inicializador para usar nuestra clase Libvirt
    parser = argparse.ArgumentParser(prog="manage_vms.py", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--debug", action="store_true")
    
    subparsers = parser.add_subparsers(dest="command", metavar="COMANDO")
    subparsers.required = True

    for cmd_name in ["create", "start", "stop", "delete", "bootorder"]:
        sp = subparsers.add_parser(cmd_name)
        if cmd_name == "bootorder": sp.add_argument("mode", choices=["net", "disk"])
        sp.add_argument("target", nargs="+")
        if cmd_name in ["stop", "delete"]: sp.add_argument("--force", action="store_true")
        
    sp_status = subparsers.add_parser("status")
    sp_status.add_argument("target", nargs="*")
    
    sp_add = subparsers.add_parser("add-node")
    sp_add.add_argument("type", choices=list(NODE_TEMPLATES.keys()))
    sp_add.add_argument("name")
    sp_add.add_argument("--mac", default=None)

    args = parser.parse_args()
    mgr = LibvirtManager(dry_run=args.dry_run)
    cmd = args.command

    if cmd == "create":
        targets = resolve_targets(args.target)
        if "all" in args.target: mgr.create_all()
        else:
            mgr.create_networks()
            for t in targets: mgr.create_node(t)
    elif cmd == "start":
        targets = resolve_targets(args.target)
        if "all" in args.target: mgr.start_all()
        else:
            for t in targets: mgr.start(t)
    elif cmd == "stop":
        targets = resolve_targets(args.target)
        if "all" in args.target: mgr.stop_all(force=args.force)
        else:
            for t in targets: mgr.stop(t, force=args.force)
    elif cmd == "delete":
        targets = resolve_targets(args.target)
        if "all" in args.target: mgr.delete_all(force=args.force)
        else:
            for t in targets: mgr.delete(t, force=args.force)
    elif cmd == "status":
        if not args.target: mgr.status()
        else:
            for t in resolve_targets(args.target): mgr.status(t)
    elif cmd == "bootorder":
        targets = resolve_targets(args.target)
        if "all" in args.target: mgr.set_boot_order_all(args.mode)
        else:
            for t in targets: mgr.set_boot_order(t, args.mode)
    elif cmd == "add-node":
        mgr.add_node(args.type, args.name, mac=args.mac)

if __name__ == "__main__":
    main()
