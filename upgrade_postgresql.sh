#!/bin/bash

# ===============================
# Script de migración PostgreSQL
# Backup al inicio y al final (con validación diaria)
# Autor: OpenAI - ChatGPT
# Versión: Octubre 2025 (Modificado v3)
# ===============================

# 🛠️ CONFIGURACIÓN INICIAL
ODOO_DB="contabilidad"                  # ⚠️ Cambia este nombre si tu base es distinta
DB_USER="odoo-v14"                      # Usuario PostgreSQL
DB_PASSWORD="Y72G2UdW2wIu9FSK"          # ⚠️ Clave PostgreSQL (¡No recomendado en scripts!)
DB_HOST="172.17.0.1"                    # IP donde corre PostgreSQL (usado por Odoo en Docker)
DB_PORT="5432"                          # Puerto PostgreSQL
BACKUP_DIR="/home/ubuntu/backups/"      # Ruta de backups

# Versiones a migrar (ordenadas)
versions=(13 14 15 16 17)

# Crear carpeta de backups si no existe
mkdir -p "$BACKUP_DIR"

# 💥 Manejo de errores
handle_error() {
    echo "❌ Error en la línea $1"
    exit 1
}
trap 'handle_error $LINENO' ERR

# -------------------------------------------------------------------
# FUNCIÓN PARA CORREGIR EL REPOSITORIO DE POSTGRESQL
# -------------------------------------------------------------------
setup_postgresql_repository() {
    echo "🔧 Verificando la configuración del repositorio de PostgreSQL..."
    local OS_CODENAME=$(lsb_release -cs)
    local PGRM_SOURCE_FILE="/etc/apt/sources.list.d/pgdg.list"
    local CORRECT_SOURCE_LINE="deb http://apt.postgresql.org/pub/repos/apt ${OS_CODENAME}-pgdg main"
    local NEEDS_FIX=false

    if [ ! -f "$PGRM_SOURCE_FILE" ]; then
        echo "⚠️ No se encontró el archivo del repositorio. Creándolo..."
        NEEDS_FIX=true
    elif ! grep -q "^${CORRECT_SOURCE_LINE}$" "$PGRM_SOURCE_FILE"; then
        echo "Detected"
        echo "Detectado un repositorio de PostgreSQL incorrecto. Reparando a '${OS_CODENAME}-pgdg'..."
        NEEDS_FIX=true
    fi

    if [ "$NEEDS_FIX" = true ]; then
        echo "$CORRECT_SOURCE_LINE" | sudo tee "$PGRM_SOURCE_FILE"
        wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
        echo "Actualizando listas de paquetes (apt update)..."
        sudo apt update
        echo "✅ Repositorio de PostgreSQL corregido."
    else
        echo "✅ El repositorio de PostgreSQL ya está configurado correctamente para '$OS_CODENAME'."
    fi
}

# -------------------------------------------------------------------
# ✅ FUNCIÓN DE BACKUP (ACTUALIZADA CON VALIDACIÓN DIARIA)
# -------------------------------------------------------------------
make_odoo_backup() {
    local version_tag=$1
    local HOY=$(date +%F) # Formato YYYY-MM-DD
    
    # Patrón de búsqueda para los backups del día de hoy para esta versión
    local patron_backup_hoy="${BACKUP_DIR}/Backup-v${version_tag}-${ODOO_DB}-${HOY}*.sql"

    echo "🛡️ Verificando backup para '$ODOO_DB' (v$version_tag) del día $HOY..."

    # Comprobar si ya existe un backup que coincida con el patrón
    # ls ... 1>/dev/null 2>&1 suprime la salida de ls, solo nos interesa el código de éxito
    if ls $patron_backup_hoy 1> /dev/null 2>&1; then
        echo "✅ El backup para v${version_tag} del día ${HOY} ya existe. Omitiendo."
    else
        echo "ℹ️ No se encontró backup para hoy. Creando uno nuevo..."
        local timestamp=$(date +%F_%H%M%S)
        local backup_file="$BACKUP_DIR/Backup-v${version_tag}-${ODOO_DB}-$timestamp.sql"
        local host_to_use="$DB_HOST"

        # Verificar conexión al host actual
        if ! nc -z -w3 "$DB_HOST" "$DB_PORT" 2>/dev/null; then
            echo "⚠️ No se pudo conectar a $DB_HOST:$DB_PORT. Usando localhost como fallback..."
            host_to_use="localhost"
        fi

        # Exportar password para pg_dump
        export PGPASSWORD="$DB_PASSWORD"

        # Ejecutar pg_dump solo de la base de Odoo
        pg_dump \
            -h "$host_to_use" \
            -p "$DB_PORT" \
            -U "$DB_USER" \
            -F p \
            -d "$ODOO_DB" \
            -f "$backup_file"

        if [[ $? -eq 0 ]]; then
            echo "✅ Backup nuevo creado en: $backup_file"
        else
            echo "❌ Error al crear backup para la base $ODOO_DB (marcado como v$version_tag)"
            exit 1
        fi
    fi
}

# 🚀 Iniciar migración

# 0. Reparar repositorios ANTES de empezar
setup_postgresql_repository

# 🗂️ 1. Backup INICIAL (antes de CUALQUIER migración)
first_version=$((versions[0] - 1)) 
echo "-----------------------------------------------"
make_odoo_backup "$first_version"
echo "-----------------------------------------------"


for target_version in "${versions[@]}"; do
    current_version=$((target_version - 1))
    echo "🔄 Migrando de PostgreSQL $current_version a $target_version..."

    # Instalar nueva versión
    echo "📦 Instalando PostgreSQL $target_version..."
    sudo apt install -y "postgresql-$target_version"

    # Eliminar clúster por defecto (si existe)
    if pg_lsclusters | grep -q "$target_version.*main"; then
        echo "🧹 Eliminando clúster por defecto de $target_version..."
        sudo pg_dropcluster --stop $target_version main
    fi

    # Realizar upgrade del clúster
    echo "🔧 Ejecutando pg_upgradecluster..."
    sudo pg_upgradecluster -v $target_version $current_version main

    # Verificar si el clúster subió correctamente
    if pg_lsclusters | grep -q "$target_version.*main.*online"; then
        echo "✅ PostgreSQL $target_version está activo y funcionando."
    else
        echo "❌ Error al iniciar clúster de $target_version. Verifica logs."
        exit 1
    fi

    # Eliminar clúster anterior
    echo "🗑️ Eliminando clúster PostgreSQL $current_version..."
    sudo pg_dropcluster $current_version main

    # Opcional: eliminar paquete viejo
    echo "🧽 Eliminando paquetes PostgreSQL $current_version..."
    sudo apt purge -y "postgresql-$current_version" "postgresql-client-$current_version"

    echo "✅ Migración a PostgreSQL $target_version completada."
    echo "-----------------------------------------------"
done

# 🗂️ 2. Backup FINAL (después de TODAS las migraciones)
last_version=${versions[-1]} 
make_odoo_backup "$last_version"
echo "-----------------------------------------------"

echo "🎉 Migración completa. Todos los upgrades han sido aplicados correctamente."