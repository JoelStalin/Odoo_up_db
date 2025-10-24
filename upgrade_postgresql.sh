#!/bin/bash

# Función para manejar errores
handle_error() {
    echo "Error en la línea $1"
    exit 1
}

# Atrapar errores
trap 'handle_error $LINENO' ERR

# Leer versiones desde el usuario
read -p "¿Cuál es la versión ACTUAL de PostgreSQL (por ejemplo, 12)? " source_version
read -p "¿A qué versión deseas actualizar PostgreSQL (por ejemplo, 17)? " target_version

# Validar que ambas sean números enteros
if ! [[ "$source_version" =~ ^[0-9]+$ && "$target_version" =~ ^[0-9]+$ ]]; then
    echo "Ambas versiones deben ser números enteros."
    exit 1
fi

# Validar que la versión destino sea mayor que la versión actual
if (( target_version <= source_version )); then
    echo "La versión destino ($target_version) debe ser mayor que la actual ($source_version)."
    exit 1
fi

# Iterar desde source_version+1 hasta target_version
for (( version = source_version + 1; version <= target_version; version++ )); do
    current_version=$((version - 1))
    echo "=== Actualizando de PostgreSQL $current_version a $version ==="

    # Verificar si la versión de origen está instalada
    if ! dpkg -l | grep -q "postgresql-$current_version"; then
        echo "PostgreSQL $current_version no está instalado. Saliendo."
        exit 1
    fi

    # Instalar la nueva versión de PostgreSQL
    echo "Instalando PostgreSQL $version..."
    sudo apt update
    sudo apt install -y "postgresql-$version"

    # Eliminar el clúster predeterminado de la nueva versión si existe
    if pg_lsclusters | grep -q "$version.*main"; then
        echo "Eliminando clúster predeterminado de PostgreSQL $version..."
        sudo pg_dropcluster --stop $version main
    fi

    # Realizar la migración del clúster
    echo "Actualizando el clúster de PostgreSQL $current_version a $version..."
    sudo pg_upgradecluster -v $version $current_version main

    # Verificar que el nuevo clúster esté online
    if sudo pg_lsclusters | grep -q "$version.*main.*online"; then
        echo "El clúster de PostgreSQL $version está en línea."
    else
        echo "Error al iniciar el clúster de PostgreSQL $version. Verifica los registros."
        exit 1
    fi

    # Eliminar el clúster de la versión anterior
    echo "Eliminando el clúster de PostgreSQL $current_version..."
    sudo pg_dropcluster $current_version main

    # (Opcional) Eliminar los paquetes de la versión anterior
    echo "Eliminando paquetes de PostgreSQL $current_version..."
    sudo apt purge -y "postgresql-$current_version" "postgresql-client-$current_version"

    echo "=== Actualización a PostgreSQL $version completada ==="
done

echo "Todas las actualizaciones desde PostgreSQL $source_version hasta $target_version se completaron con éxito."
