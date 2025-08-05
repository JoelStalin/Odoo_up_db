#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import shutil
from datetime import datetime
import migration_helpers

LOG_FILE = 'update.log'

def setup_logging():
    """Sets up logging to file and console."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout)
        ]
    )

def display_menu():
    """Displays the main menu to the user."""
    print("\n--- Odoo Update Menu ---")
    print("1. Update from Odoo 15 to Odoo 16")
    print("2. Update from Odoo 16 to Odoo 17")
    print("3. Update from Odoo 17 to Odoo 18")
    print("4. Exit")
    print("------------------------")

def get_user_choice():
    """Gets and validates the user's menu choice."""
    while True:
        choice = input("Enter your choice (1-4): ")
        if choice in ['1', '2', '3', '4']:
            return choice
        else:
            print("Invalid choice. Please enter a number between 1 and 4.")

def create_backup(source_dir):
    """Creates a zip backup of the specified directory."""
    if not os.path.isdir(source_dir):
        logging.error(f"Source directory '{source_dir}' does not exist. Cannot create backup.")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{os.path.basename(source_dir)}_{timestamp}.zip"

    try:
        logging.info(f"Creating backup of '{source_dir}' to '{backup_filename}'...")
        shutil.make_archive(backup_filename.replace('.zip', ''), 'zip', source_dir)
        logging.info("Backup created successfully.")
        return backup_filename
    except Exception as e:
        logging.error(f"Failed to create backup: {e}")
        return None

def get_odoo_version(path):
    """
    Detects the Odoo version from __manifest__.py files.
    This is a simplified detector and might not be 100% accurate.
    It returns the major version number as a string (e.g., '15', '16') or None.
    """
    logging.info(f"Attempting to detect Odoo version in '{path}'...")
    # In a real scenario, we would look for odoo/conf/odoo.conf or check the core odoo version
    # For now, we'll scan for a manifest file.
    for root, _, files in os.walk(path):
        if '__manifest__.py' in files:
            manifest_path = os.path.join(root, '__manifest__.py')
            try:
                with open(manifest_path, 'r') as f:
                    contents = f.read()
                    # A simple regex to find 'version': '15.0.1.0.0'
                    # This is a basic approach. A better one would use ast.literal_eval
                    import re
                    match = re.search(r"['\"]version['\"]\s*:\s*['\"](\d+)\.", contents)
                    if match:
                        version = match.group(1)
                        logging.info(f"Detected Odoo version '{version}' from {manifest_path}")
                        return version
            except Exception as e:
                logging.warning(f"Could not read or parse {manifest_path}: {e}")

    logging.warning("Could not determine Odoo version. No __manifest__.py file with a version key found.")
    return None


# --- Migration Workflow Functions ---

def run_migration_15_to_16(addons_path):
    """Placeholder for Odoo 15 to 16 migration."""
    logging.info("--- Starting Migration: Odoo 15 to 16 ---")
    logging.warning("This migration path is not fully implemented.")
    logging.info("Code migration would involve running OpenUpgrade scripts and other custom refactoring.")
    logging.info("Database migration requires running Odoo with the --update=all flag against the migrated database.")
    logging.info("--- Finished Migration: Odoo 15 to 16 ---")
    return True

def run_migration_16_to_17(addons_path):
    """Runs the migration from Odoo 16 to 17."""
    logging.info("--- Starting Migration: Odoo 16 to 17 ---")

    # Step 1: Code migration for attrs/states
    succeeded, failed = migration_helpers.run_attrs_states_conversion(addons_path)
    logging.info(f"Attrs/States conversion complete. Succeeded: {len(succeeded)}, Failed: {len(failed)}")
    if failed:
        logging.error(f"The following files failed attrs/states conversion: {failed}")

    # Step 2: Code migration for tree/list
    modified_files = migration_helpers.run_tree_to_list_replacement(addons_path)
    logging.info(f"'tree' to 'list' replacement complete. Modified files: {len(modified_files)}")

    # Step 3: Placeholder for database migration
    logging.info("Code migration steps complete.")
    logging.warning("Manual step required: Run Odoo with '--update=all' to complete the database migration.")

    logging.info("--- Finished Migration: Odoo 16 to 17 ---")
    return not bool(failed) # Return False if any files failed conversion

def run_migration_17_to_18(addons_path):
    """Runs the migration from Odoo 17 to 18."""
    logging.info("--- Starting Migration: Odoo 17 to 18 ---")

    # Step 1: Update manifest files
    succeeded, failed = migration_helpers.run_odoo18_manifest_update(addons_path)
    logging.info(f"Manifest update complete. Succeeded: {len(succeeded)}, Failed: {len(failed)}")
    if failed:
        logging.error(f"The following manifests failed to update: {failed}")

    # Step 2: Placeholder for other code/DB migrations
    logging.info("Code migration steps complete.")
    logging.warning("Manual step required: Run Odoo with '--update=all' to complete the database migration.")

    logging.info("--- Finished Migration: Odoo 17 to 18 ---")
    return not bool(failed)


def main():
    """Main function to orchestrate the Odoo update process."""
    setup_logging()
    logging.info("Starting Odoo Update Script")

    addons_path = input("Enter the path to your custom addons directory (e.g., ./custom_addons): ")
    if not os.path.isdir(addons_path):
        logging.error(f"The directory '{addons_path}' does not exist. Exiting.")
        return

    current_version = get_odoo_version(addons_path)
    if not current_version:
        if input("Could not detect version. Continue anyway? (y/n): ").lower() != 'y':
            return

    while True:
        display_menu()
        choice = get_user_choice()

        if choice == '4':
            logging.info("Exiting script.")
            break

        # Validation
        if current_version:
            if choice == '1' and current_version != '15':
                logging.error(f"Invalid option. Detected version is {current_version}, expected 15.")
                continue
            if choice == '2' and current_version != '16':
                logging.error(f"Invalid option. Detected version is {current_version}, expected 16.")
                continue
            if choice == '3' and current_version != '17':
                logging.error(f"Invalid option. Detected version is {current_version}, expected 17.")
                continue

        # Confirmation
        print("\n--- Pre-update summary ---")
        if choice == '1': print("You are about to update from Odoo 15 to 16.")
        elif choice == '2': print("You are about to update from Odoo 16 to 17.")
        elif choice == '3': print("You are about to update from Odoo 17 to 18.")

        print(f"A backup of '{addons_path}' will be created.")
        print("IMPORTANT: Please ensure you have a separate backup of your database.")

        if input("Do you want to continue? (y/n): ").lower() != 'y':
            logging.info("Update process aborted by user.")
            continue

        # Execution
        logging.info("Starting update process...")

        backup_file = create_backup(addons_path)
        if not backup_file:
            logging.error("Backup failed. Aborting update.")
            continue
        logging.info(f"Backup created at: {backup_file}")

        success = False
        if choice == '1':
            success = run_migration_15_to_16(addons_path)
        elif choice == '2':
            success = run_migration_16_to_17(addons_path)
        elif choice == '3':
            success = run_migration_17_to_18(addons_path)

        if success:
            logging.info("Update process completed successfully.")
            print("\nUpdate finished. Please check 'update.log' for details.")
        else:
            logging.error("Update process finished with errors.")
            print("\nUpdate failed. Please check 'update.log' for details.")


if __name__ == "__main__":
    main()
