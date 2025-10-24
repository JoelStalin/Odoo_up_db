#!/bin/bash

# ===============================
# Script de migración PostgreSQL (v19 - Secuencia Completa 12 a 17)
# Autor: OpenAI - ChatGPT
# Versión: Octubre 2025 (Modificado v19)
# ===============================

# 🛠️ CONFIGURACIÓN INICIAL
ODOO_DB="contabilidad"                  # ⚠️ Cambia este nombre si tu base es distinta
DB_USER="odoo-v14"                      # Usuario PostgreSQL para backup
DB_PASSWORD="Y72G2UdW2wIu9FSK"          # ⚠️ Clave PostgreSQL (¡No recomendado en scripts!)
DB_HOST="172.17.0.1"                    # IP donde corre PostgreSQL (usado por Odoo en Docker)
DB_PORT="5432"                          # Puerto PostgreSQL
BACKUP_DIR="/home/ubuntu/backups/"      # Ruta de backups

# Opciones de APT para forzar IPv4 y deshabilitar caché
APT_OPTS="-o Acquire::ForceIPv4=true -o Acquire::http::No-Cache=true -o Acquire::http::Max-Age=0"

# --- LÓGICA DE VERSIONES (Secuencia Completa) ---
initial_version=12                       # Versión actual del sistema
versions_to_upgrade_to=(13 14 15 16 17)     # Versiones a las que se migrará (¡Secuencia completa!)

# Crear carpeta de backups si no existe
mkdir -p "$BACKUP_DIR"

# 💥 Manejo de errores
handle_error() {
    echo "❌ Error en la línea $1"
    sudo rm -f /var/lib/dpkg/lock* /var/cache/apt/archives/lock* /var/lib/apt/lists/lock*
    sudo dpkg --configure -a
    exit 1
}
trap 'handle_error $LINENO' ERR

# --- Funciones de Apt (wait_for_apt_lock, setup_postgresql_repository) ---
# (Se mantienen las de la v15/v17/v18 para asegurar que apt funcione)
wait_for_apt_lock() {
    local max_retries=6; local retry_delay=10; local attempt=0
    echo "⏳ Verificando bloqueos de apt/dpkg..."; while sudo fuser /var/lib/dpkg/lock* /var/cache/apt/archives/lock* /var/lib/apt/lists/lock* >/dev/null 2>&1; do
        attempt=$((attempt + 1)); if [ "$attempt" -gt "$max_retries" ]; then echo "❌ Error: apt/dpkg sigue bloqueado..."; exit 1; fi
        echo "   apt/dpkg bloqueado. Esperando ${retry_delay}s (Intento ${attempt}/${max_retries})..."; sleep "$retry_delay"; done; echo "✅ No hay bloqueos detectados."
}
setup_postgresql_repository() {
    echo "🔧 Configurando el repositorio de PostgreSQL..."; echo "Acquire::ForceIPv4 \"true\";" | sudo tee /etc/apt/apt.conf.d/99force-ipv4
    echo "🔄 Actualizando sistema y dependencias..."; wait_for_apt_lock; sudo apt $APT_OPTS update; wait_for_apt_lock; sudo apt $APT_OPTS upgrade -y
    echo "🔧 Intentando reparar paquetes rotos..."; wait_for_apt_lock; sudo apt --fix-broken install -y || echo "⚠️ 'apt --fix-broken' encontró problemas..."
    echo "🧹 Limpiando configs antiguas..."; wait_for_apt_lock; sudo apt-get $APT_OPTS clean; sudo rm -f /etc/apt/sources.list.d/pgdg*; sudo rm -f /usr/share/keyrings/postgresql*; sudo rm -f /etc/apt/trusted.gpg.d/pgdg*; sudo apt-key del ACCC4CF8 2> /dev/null || true
    echo "🔄 Instalando dependencias (wget, gnupg)..."; wait_for_apt_lock; sudo apt $APT_OPTS install -y wget ca-certificates gnupg lsb-release
    echo "🔑 Añadiendo clave GPG..."; wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
    local OS_CODENAME=$(lsb_release -cs); echo "Sistema detectado: $OS_CODENAME"; sudo sh -c "echo \"deb http://apt.postgresql.org/pub/repos/apt ${OS_CODENAME}-pgdg main\" > /etc/apt/sources.list.d/pgdg.list"
    echo "✅ Repositorio configurado para '${OS_CODENAME}'."; echo "🔄 Actualizando lista de paquetes..."; wait_for_apt_lock; sudo apt $APT_OPTS update; echo "✅ Actualización completada."
}
# --- Fin Funciones Apt ---

# ✅ FUNCIÓN DE BACKUP (CON VALIDACIÓN DIARIA y FORMATO CUSTOM)
make_odoo_backup() {
    local version_tag=$1
    local HOY=$(date +%F)
    local patron_backup_hoy="${BACKUP_DIR}/Backup-v${version_tag}-${ODOO_DB}-${HOY}*.dump"
    local backup_file_base="${BACKUP_DIR}/Backup-v${version_tag}-${ODOO_DB}-$(date +%F_%H%M%S)"
    local backup_file="${backup_file_base}.dump"

    echo "🛡️ Verificando backup para '$ODOO_DB' (v$version_tag) del día $HOY..."
    if ls $patron_backup_hoy 1> /dev/null 2>&1; then
        echo "✅ El backup para v${version_tag} del día ${HOY} ya existe. Omitiendo."
    else
        echo "ℹ️ No se encontró backup para hoy. Creando uno nuevo en formato custom..."
        local host_to_use="$DB_HOST"
        if ! nc -z -w3 "$DB_HOST" "$DB_PORT" 2>/dev/null; then
            echo "⚠️ No se pudo conectar a $DB_HOST:$DB_PORT. Usando localhost como fallback..."
            host_to_use="localhost"
        fi
        export PGPASSWORD="$DB_PASSWORD"
        # Comprobar si el usuario tiene permisos suficientes
        echo "   Verificando permisos de dump para usuario '$DB_USER' en DB '$ODOO_DB'..."
        if ! pg_dump -h "$host_to_use" -p "$DB_PORT" -U "$DB_USER" --schema-only -t non_existent_table "$ODOO_DB" > /dev/null 2>&1; then
             echo "   ⚠️ Advertencia: El usuario '$DB_USER' podría no tener permisos suficientes para hacer pg_dump."
             echo "   Se recomienda usar el superusuario 'postgres' para backups o asegurar permisos."
        fi

        if pg_dump -h "$host_to_use" -p "$DB_PORT" -U "$DB_USER" -Fc -b -v -d "$ODOO_DB" -f "$backup_file"; then
            echo "✅ Backup nuevo creado en: $backup_file (formato custom)"
        else
            echo "❌ Error al crear backup para la base $ODOO_DB (marcado como v$version_tag)"; rm -f "$backup_file"; exit 1
        fi
        # unset PGPASSWORD
    fi
}
# -------------------------------------------------------------------

# 🚀 Iniciar migración

# 0. Reparar repositorios Y ACTUALIZAR SISTEMA
setup_postgresql_repository

# 🗂️ 1. Backup INICIAL (de la versión inicial definida)
echo "-----------------------------------------------"
make_odoo_backup "$initial_version"
echo "-----------------------------------------------"

# Variable para llevar la cuenta de la versión actual *antes* de cada migración
current_active_version=$initial_version

# Iterar sobre cada versión a la que queremos llegar (13, 14, 15, 16, 17)
for target_version in "${versions_to_upgrade_to[@]}"; do
    echo "=== Actualizando de PostgreSQL $current_active_version a $target_version ==="

    # Verificar si el clúster actual existe
    if ! pg_lsclusters | grep -q "$current_active_version.*main"; then
        echo "⚠️ No se encontró el clúster $current_active_version/main. Omitiendo migración a $target_version."
        # Si el clúster anterior no existe, PERO el clúster objetivo SÍ existe
        # (quizás de una ejecución anterior fallida), actualizamos la versión actual y continuamos
        if pg_lsclusters | grep -q "$target_version.*main"; then
             echo "   -> Detectado clúster $target_version/main existente. Asumiendo como versión actual."
             current_active_version=$target_version
        fi
        continue
    fi

    # Instalar la nueva versión de PostgreSQL (Servidor y Cliente) con reintentos
    echo "📦 Intentando instalar PostgreSQL $target_version (Servidor y Cliente)..."
    local install_retries=3; local install_success=false
    for (( i=1; i<=$install_retries; i++ )); do
        wait_for_apt_lock
        # Siempre necesitamos los binarios de ambas versiones para pg_upgradecluster
        # Aseguramos que la versión actual esté instalada (por si se desinstaló en un paso previo fallido)
        sudo apt $APT_OPTS install -y "postgresql-$current_active_version" "postgresql-client-$current_active_version" || echo "   (Ignorando error al reinstalar v$current_active_version)"
        
        # Ahora instalamos la versión objetivo
        if sudo apt $APT_OPTS install -y "postgresql-$target_version" "postgresql-client-$target_version"; then
            install_success=true; echo "✅ Paquetes v$target_version instalados."; break
        else
            echo "⚠️ Falló la instalación de v$target_version (Intento $i/$install_retries). Esperando 15s..."; sleep 15
            wait_for_apt_lock; sudo apt --fix-broken install -y
        fi
    done
    if [ "$install_success" = false ]; then echo "❌ Error: No se pudo instalar postgresql-$target_version."; exit 1; fi

    # Detener y eliminar el clúster predeterminado de la nueva versión si existe
    if pg_lsclusters | grep -q "$target_version.*main"; then
        echo "🧹 Eliminando clúster predeterminado de PostgreSQL $target_version..."
        # Detener primero el nuevo clúster antes de borrarlo
        sudo systemctl stop "postgresql@${target_version}-main.service" || echo "   (El clúster $target_version/main ya estaba detenido o no existía)"
        sudo pg_dropcluster --stop $target_version main
    fi

    # Actualizar el clúster de la versión anterior a la nueva versión
    echo "🔧 Actualizando el clúster de PostgreSQL $current_active_version a $target_version..."
    # Detener el clúster antiguo ANTES de ejecutar pg_upgradecluster
    sudo systemctl stop "postgresql@${current_active_version}-main.service" || echo "   (El clúster $current_active_version/main ya estaba detenido)"
    sudo pg_upgradecluster -v $target_version $current_active_version main

    # Verificar el estado del clúster actualizado
    # pg_upgradecluster normalmente inicia el nuevo clúster, pero verificamos
    sudo systemctl start "postgresql@${target_version}-main.service" || echo "   (Intentando iniciar clúster $target_version/main... puede que ya estuviera online)"
    sleep 5 # Dar tiempo a que arranque completamente
    if sudo pg_lsclusters | grep -q "$target_version.*main.*online"; then
        echo "✅ El clúster de PostgreSQL $target_version está en línea."
    else
        echo "❌ Error al iniciar el clúster de PostgreSQL $target_version. Verifica logs en /var/log/postgresql/."
        exit 1
    fi

    # Eliminar el clúster de la versión anterior (¡NO los paquetes todavía!)
    echo "🗑️ Eliminando clúster PostgreSQL $current_active_version..."
    sudo pg_dropcluster $current_active_version main

    # (Opcional pero recomendado) Eliminar los paquetes de la versión anterior (con comprobación)
    echo "🧽 Eliminando paquetes de PostgreSQL $current_active_version..."
    if dpkg -l | grep -qE "(postgresql-${current_active_version}|postgresql-client-${current_active_version})"; then
        wait_for_apt_lock
        sudo apt $APT_OPTS purge -y "postgresql-${current_active_version}" "postgresql-client-${current_active_version}" || echo "   (Ignorando error al purgar)"
        wait_for_apt_lock
        sudo apt $APT_OPTS autoremove -y
    else
        echo "   Paquetes de $current_active_version no encontrados, probablemente ya eliminados."
    fi

    echo "=== Actualización a PostgreSQL $target_version completada ==="
    # IMPORTANTE: Actualizar la versión activa para la SIGUIENTE iteración
    current_active_version=$target_version
    echo "-----------------------------------------------"
done

# 🗂️ 2. Backup FINAL (de la última versión alcanzada)
make_odoo_backup "$current_active_version"
echo "-----------------------------------------------"

echo "🎉 Todas las actualizaciones se han completado con éxito. Versión final: $current_active_version."