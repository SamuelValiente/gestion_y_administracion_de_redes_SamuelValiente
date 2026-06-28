Aqui voy a subir todo sobre la migracion a libvirt

# Practica: Migración de Infraestructura a Libvirt/KVM

## Descripción de la práctica
En este trabajo he cogido el proyecto de infraestructura proporcionado por Tobías de unos compañeros (https://github.com/alballr/GA-GyAR-2026), que originalmente estaba montado sobre VirtualBox, y lo he migrado para que funcione de forma nativa en Libvirt/KVM.

El objetivo ha sido cambiar el "motor" del script principal sin romper la configuración de las redes, las IPs ni las direcciones MAC originales, para que siga siendo 100% compatible con el resto del proyecto (como el aprovisionamiento PXE y Ansible).

## Cambios principales
He modificado el archivo `scripts/manage_vms.py` para sustituir todas las llamadas a VirtualBox por sus equivalentes en Libvirt.

* **Máquinas y discos:** En lugar de usar `VBoxManage`, el script ahora levanta las máquinas usando `virt-install` y emplea discos en formato ligero `.qcow2`.
* **Redes:** Las redes del laboratorio se han adaptado para que se definan automáticamente como puentes virtuales en Libvirt (`main`, `internal` y `ext`).
* **Gestión y ciclo de vida:** Para ver el estado, apagar o borrar máquinas, el script usa ahora comandos de `virsh`. Para cambiar el orden de arranque (de red a disco), se utiliza `virt-xml`.

## Comandos de uso
El script mantiene la misma estructura que el original. Al crear redes virtuales en el sistema, es necesario ejecutarlo con permisos de administrador (`sudo`).

---------------------------------------------------------------------------------------------------------------
Comandos más importantes:

**Ver el estado de todas las máquinas:**
sudo python3 manage_vms.py status

**Crear las redes y todas las máquinas:**
sudo python3 manage_vms.py create all

**Apagar todo el laboratorio(forzado):**
sudo python3 manage_vms.py stop all --force

**Cambiar el arranque a disco (post-instalación):**
sudo python3 manage_vms.py bootorder disk all

**Borrar todo el entorno y limpiar los discos virtuales:**
sudo python3 manage_vms.py delete all --force
