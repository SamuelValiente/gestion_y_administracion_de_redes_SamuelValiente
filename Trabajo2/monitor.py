import libvirt
import sys

# Función que se ejecuta automáticamente cuando hay un evento
def callback_evento(conn, dom, event, detail, opaque):
    # Diccionario para traducir los estados numéricos de Libvirt a texto legible
    estados = {
        0: "Definida (Añadida a Libvirt)",
        1: "Eliminada (Borrada de Libvirt)",
        2: "Iniciada / En ejecución",
        3: "Pausada (Suspendida)",
        4: "Reanudada",
        5: "Detenida / Apagada",
        6: "Apagándose"
    }

    nuevo_estado = estados.get(event, f"Desconocido ({event})")
    vm_name = dom.name()
    vm_id = dom.ID()

    # Si la máquina está apagada, Libvirt le quita el ID y devuelve -1
    id_texto = vm_id if vm_id != -1 else "Sin ID (Apagada)"

    print(f"--EVENTO CAPTURADO -> Máquina: '{vm_name}' | ID Instancia: {id_texto} | Nuevo estado: {nuevo_estado}")

# 1. Registrar el bucle de eventos de Libvirt
libvirt.virEventRegisterDefaultImpl()

# 2. Conectar al hipervisor en modo solo lectura
try:
    conn = libvirt.openReadOnly('qemu:///system')
except libvirt.libvirtError as e:
    print(f"Error al conectar con Libvirt: {e}")
    sys.exit(1)

# 3. Suscribir nuestra función callback a los eventos de ciclo de vida (Lifecycle)
conn.domainEventRegisterAny(None, libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE, callback_evento, None)
conn.setKeepAlive(5, 3)

print("...Sistema de CI Iniciado: Escuchando eventos de máquinas virtuales...")
print("Presiona Ctrl+C para detener.\n")

# 4. Mantener el programa corriendo en bucle infinito
while True:
    try:
        libvirt.virEventRunDefaultImpl()
    except KeyboardInterrupt:
        print("\nMonitor de eventos detenido.")
        break
