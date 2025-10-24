#!/bin/bash

# ==================================
# Script para Restaurar Backup en Docker (Versión Funcional)
# - Crea el usuario y la base de datos antes de restaurar.
# Autor: OpenAI - ChatGPT
# Versión: Octubre 2025 (Modificado)
# ==================================

# 🛠️ CONFIGURACIÓN EXTRAÍDA DEL SCRIPT ANTERIOR
# Estas variables se usan para crear el entorno exacto en Docker.
ODOO_DB_NAME="contabilidad"              # 💾 Nombre de la BD de Odoo a crear
ODOO_DB_USER="odoo-v14"                  # 👤 Usuario de Odoo a crear
ODOO_DB_PASSWORD="Y72G2UdW2wIu9FSK"      # 🔑 Clave para el nuevo usuario

# --- Configuración del Backup ---
BACKUP_DIR="/home/ubuntu/backups/"       # 📂 Ruta donde están los backups .sql

# --- Configuración de Docker (Modifica según tu contenedor) ---
DOCKER_CONTAINER_NAME="postgres_odoo_17" # 🐳 Nombre de tu contenedor Docker de PostgreSQL
DOCKER_SUPER_USER="postgres"             # 👤 Usuario ADMIN de PostgreSQL DENTRO del contenedor

# --- Nombre del archivo de backup dentro de Docker ---
BACKUP_FILE_IN_CONTAINER="/tmp/latest_backup.sql"

# 💥 Manejo de errores
handle_error() {
    echo "❌ Error en la línea $1"
    echo " rollback no automático. Por favor, revisa el contenedor."
    exit 1
}
trap 'handle_error $LINENO' ERR

echo "🚀 Iniciando restauración funcional a Docker..."

# 1. Encontrar el último backup creado
echo "🔍 Buscando el último backup en $BACKUP_DIR..."
LATEST_BACKUP_FILE=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "*.sql" -printf "%T@ %p\n" | sort -n | tail -n 1 | cut -d' ' -f2-)

if [ -z "$LATEST_BACKUP_FILE" ]; then
    echo "❌ No se encontró ningún archivo .sql en $BACKUP_DIR"
    exit 1
fi

echo "✅ Backup más reciente encontrado: $LATEST_BACKUP_FILE"

# 2. Copiar el backup al contenedor Docker
echo "COPY"
echo "Copiando '$LATEST_BACKUP_FILE' a '$DOCKER_CONTAINER_NAME:$BACKUP_FILE_IN_CONTAINER'..."
docker cp "$LATEST_BACKUP_FILE" "$DOCKER_CONTAINER_NAME:$BACKUP_FILE_IN_CONTAINER"

# 3. Crear el usuario de Odoo en Docker
echo " CREATE USER "
echo "Creando usuario '$ODOO_DB_USER' en el contenedor..."
# Se usa PGPASSWORD para el superusuario si es necesario, pero psql -U postgres no suele pedirla
docker exec -u "$DOCKER_SUPER_USER" "$DOCKER_CONTAINER_NAME" psql -c "CREATE USER $ODOO_DB_USER WITH PASSWORD '$ODOO_DB_PASSWORD';"

# 4. Crear la nueva base de datos en Docker
echo " CREATE DATABASE "
echo "Creando nueva base de datos '$ODOO_DB_NAME' con propietario '$ODOO_DB_USER'..."
docker exec -u "$DOCKER_SUPER_USER" "$DOCKER_CONTAINER_NAME" psql -c "CREATE DATABASE $ODOO_DB_NAME OWNER $ODOO_DB_USER;"

# 5. Restaurar el backup en la nueva base de datos
echo " RESTORE "
echo "Restaurando el backup en '$ODOO_DB_NAME'... (Esto puede tardar)"
# Ejecutamos la restauración como el superusuario, ya que el backup puede contener comandos
# que el usuario de Odoo no tiene permiso para ejecutar (ej. CREATE EXTENSION).
docker exec -u "$DOCKER_SUPER_USER" "$DOCKER_CONTAINER_NAME" psql -d "$ODOO_DB_NAME" -f "$BACKUP_FILE_IN_CONTAINER"

# 6. Limpiar el archivo de backup del contenedor
echo "🧹 Limpiando archivo de backup del contenedor..."
docker exec "$DOCKER_CONTAINER_NAME" rm "$BACKUP_FILE_IN_CONTAINER"

echo "-----------------------------------------------"
echo "🎉 ¡Restauración completada!"
echo "El usuario '$ODOO_DB_USER' y la base de datos '$ODOO_DB_NAME' están listos en el contenedor '$DOCKER_CONTAINER_NAME'."