#!/bin/bash

# ===============================
# Script de migración PostgreSQL
# Backup al inicio y al final
# Autor: OpenAI - ChatGPT
# Versión: Octubre 2025 (Modificado)
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

# ✅ Función para realizar backup de una base específica
make_odoo_backup() {
    local version_tag=$1
    local timestamp=$(date +%F_%H%M%S)
    local backup_file="$BACKUP_DIR/Backup-v${version_tag}-${ODOO_DB}-$timestamp.sql"
    local host_to_use="$DB_HOST"

    echo "🛡️ Creando backup de la base '$ODOO_DB' (marcado como v$version_tag)..."

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
        echo "✅ Backup creado en: $backup_file"
    else
        echo "❌ Error al crear backup para la base $ODOO_DB (marcado como v$version_tag)"
        exit 1
    fi
}

# 🚀 Iniciar migración

# 🗂️ 1. Backup INICIAL (antes de CUALQUIER migración)
# Se usa la versión "actual" antes de la primera migración (13-1 = 12)
first_version=$((versions[0] - 1)) 
echo "-----------------------------------------------"
echo "🛡️ Creando backup INICIAL de PostgreSQL v$first_version..."
make_odoo_backup "$first_version"
echo "-----------------------------------------------"


for target_version in "${versions[@]}"; do
    current_version=$((target_version - 1))
    echo "🔄 Migrando de PostgreSQL $current_version a $target_version..."

    # Verificar si la versión actual está instalada
    if ! dpkg -l | grep -q "postgresql-$current_version"; then
        echo "⚠️ PostgreSQL $current_version no está instalado. Saliendo."
        exit 1
    fi

    # --- La llamada al backup dentro del bucle ha sido eliminada ---

    # Instalar nueva versión
    echo "📦 Instalando PostgreSQL $target_version..."
    sudo apt update
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
# Se usa la última versión de la lista (17)
last_version=${versions[-1]} 
echo "🛡️ Creando backup FINAL de PostgreSQL v$last_version..."
make_odoo_backup "$last_version"
echo "-----------------------------------------------"

echo "🎉 Migración completa. Todos los upgrades han sido aplicados correctamente."