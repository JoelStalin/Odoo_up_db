#!/bin/bash

# Función para manejar errores
handle_error() {
    echo "❌ Error en la línea $1"
    exit 1
}

# Atrapar errores
trap 'handle_error $LINENO' ERR

# Leer versiones desde el usuario
read -p "¿Cuál es la versión ACTUAL de PostgreSQL (por ejemplo, 12)? " source_version
read -p "¿A qué versión deseas actualizar PostgreSQL (por ejemplo, 17)? " target_version

# Validar que ambas sean números enteros
if ! [[ "$source_version" =~ ^[0-9]+$ && "$target_version" =~ ^[0-9]+$ ]]; then
    echo "❌ Ambas versiones deben ser números enteros."
    exit 1
fi

# Validar que la versión destino sea mayor que la actual
if (( target_version <= source_version )); then
    echo "❌ La versión destino ($target_version) debe ser mayor que la actual ($source_version)."
    exit 1
fi

# ========================
# Paso 1: Configurar repositorio oficial de PostgreSQL (si no está presente)
# ========================
echo "🔍 Verificando repositorio oficial de PostgreSQL..."
if ! grep -q "apt.postgresql.org" /etc/apt/sources.list /etc/apt/sources.list.d/*; then
    echo "➕ Agregando repositorio oficial de PostgreSQL..."

    sudo apt install -y curl ca-certificates gnupg lsb-release

    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | \
        sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg

    echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | \
        sudo tee /etc/apt/sources.list.d/pgdg.list

    sudo apt update
else
    echo "✅ Repositorio de PostgreSQL ya está configurado."
    sudo apt update
fi

# ========================
# Paso 2: Iterar versiones e ir actualizando paso a paso
# ========================
for (( version = source_version + 1; version <= target_version; version++ )); do
    current_version=$((version - 1))
    echo "🚀 === Actualizando de PostgreSQL $current_version a $version ==="

    # Verificar que la versión de origen está instalada
    if ! dpkg -l | grep -q "postgresql-$current_version"; then
        echo "❌ PostgreSQL $current_version no está instalado. Abortando."
        exit 1
    fi

    # Instalar la nueva versión
    echo "📦 Instalando PostgreSQL $version..."
    sudo apt install -y "postgresql-$version"

    # Eliminar el clúster predeterminado (si existe)
    if pg_lsclusters | grep -q "$version.*main"; then
        echo "🗑️ Eliminando clúster por defecto de $version..."
        sudo pg_dropcluster --stop "$version" main
    fi

    # Migrar el clúster
    echo "🔄 Migrando clúster de $current_version a $version..."
    sudo pg_upgradecluster -v "$version" "$current_version" main

    # Verificar que el nuevo clúster esté activo
    if pg_lsclusters | grep -q "$version.*main.*online"; then
        echo "✅ Clúster $version está en línea."
    else
        echo "❌ Fallo al activar clúster $version. Revisa los logs."
        exit 1
    fi

    # Eliminar clúster anterior
    echo "🧹 Eliminando clúster de $current_version..."
    sudo pg_dropcluster "$current_version" main

    # (Opcional) Eliminar paquetes anteriores
    echo "🧼 Eliminando paquetes de PostgreSQL $current_version..."
    sudo apt purge -y "postgresql-$current_version" "postgresql-client-$current_version"

    echo "✅ Actualización a PostgreSQL $version completada."
done

echo "🎉 Todas las actualizaciones desde PostgreSQL $source_version hasta $target_version se completaron con éxito."
