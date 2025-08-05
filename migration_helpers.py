#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
import logging
from pathlib import Path
from lxml import etree
import ast
import pprint

NEW_ATTRS = ['invisible', 'required', 'readonly', 'column_invisible']

# --- File Discovery Functions ---

def get_xml_files_in_views_recursive(path):
    """Recursively finds all XML files within 'views' subdirectories."""
    return [str(p) for p in Path(path).glob('**/views/**/*.xml') if p.is_file()]

def get_all_files_recursive(path):
    """Recursively finds all files within the given path."""
    return [str(p) for p in Path(path).rglob('*') if p.is_file()]

def get_manifest_files_recursive(path):
    """Recursively finds all __manifest__.py files."""
    return [str(p) for p in Path(path).glob('**/__manifest__.py') if p.is_file()]

# --- Domain/Attrs Parsing Functions (largely unchanged) ---

def normalize_domain(domain):
    if len(domain) == 1:
        return domain
    result = []
    expected = 1
    op_arity = {'!': 1, '&': 2, '|': 2}
    for token in domain:
        if expected == 0:
            result[0:0] = ['&']
            expected = 1
        if isinstance(token, (list, tuple)):
            expected -= 1
            token = tuple(token)
        else:
            expected += op_arity.get(token, 0) - 1
        result.append(token)
    return result

def stringify_leaf(leaf):
    operator = str(leaf[1])
    left_operand = leaf[0]
    right_operand = leaf[2]
    switcher = False
    case_insensitive = False

    if operator == '=?':
        if isinstance(right_operand, str):
            right_operand = f"'{right_operand}'"
        return f"({right_operand} in [None, False] or {left_operand} == {right_operand})"
    elif operator == '=':
        if right_operand in (False, []): return f"not {left_operand}"
        if right_operand is True: return left_operand
        operator = '=='
    elif operator == '!=':
        if right_operand in (False, []): return left_operand
        if right_operand is True: return f"not {left_operand}"
    elif 'like' in operator:
        case_insensitive = 'ilike' in operator
        if isinstance(right_operand, str) and re.search('[_%]', right_operand):
            raise ValueError("Script doesn't support 'like' domains with wildcards")
        if operator in ['=like', '=ilike']:
            operator = '=='
        else:
            operator = 'not in' if 'not' in operator else 'in'
            switcher = True

    if isinstance(right_operand, str):
        right_operand = f"'{right_operand}'"

    if switcher:
        left_operand, right_operand = right_operand, left_operand

    if not case_insensitive:
        return f"{left_operand} {operator} {right_operand}"
    else:
        return f"{left_operand}.lower() {operator} {right_operand}.lower()"


def stringify_attr(stack):
    if stack in (True, False, 'True', 'False', 1, 0, '1', '0'):
        return str(stack)
    last_parenthesis_index = max(index for index, item in enumerate(stack[::-1]) if item not in ('|', '!'))
    stack = normalize_domain(stack)
    stack = stack[::-1]
    result = []
    for index, leaf_or_operator in enumerate(stack):
        if leaf_or_operator == '!':
            expr = result.pop()
            result.append(f'(not ({expr}))')
        elif leaf_or_operator in ['&', '|']:
            left = result.pop()
            try:
                right = result.pop()
            except IndexError:
                res = left + (' and' if leaf_or_operator == '&' else ' or')
                result.append(res)
                continue
            op_str = 'and' if leaf_or_operator == '&' else 'or'
            form = '(%s %s %s)' if index <= last_parenthesis_index else '%s %s %s'
            result.append(form % (left, op_str, right))
        else:
            result.append(stringify_leaf(leaf_or_operator))
    return result[0]


def get_new_attrs(attrs):
    new_attrs = {}
    escaped_operators = ['=', '!=', '>', '>=', '<', '<=', '=\\?', '=like', 'like', 'not like', 'ilike', 'not ilike', '=ilike', 'in', 'not in', 'child_of', 'parent_of']
    attrs = re.sub("&lt;", "<", attrs)
    attrs = re.sub("&gt;", ">", attrs)
    attrs = re.sub(f"([\"'](?:{'|'.join(escaped_operators)})[\"']\\s*,\\s*)(?!False|True)([\\w\\.]+)(?=\\s*[\\]\\)])", r"\\1'__dynamic_variable__.\\2'", attrs)
    attrs = re.sub(r"(%\([\w\.]+\)d)", r"'__dynamic_variable__.\1'", attrs)
    attrs = attrs.strip()
    if re.search("^{.*}$", attrs, re.DOTALL):
        attrs_dict = ast.literal_eval(attrs)
        for attr, attr_value in attrs_dict.items():
            if attr not in NEW_ATTRS:
                continue
            stringified = stringify_attr(attr_value)
            if isinstance(stringified, str):
                stringified = re.sub(r"'__dynamic_variable__\.([^']+)'", r"\1", stringified)
            new_attrs[attr] = stringified
    return new_attrs

# --- XML Manipulation Functions (largely unchanged, but with logging) ---

def get_parent_etree_node(root_node, target_node):
    for parent_elem in root_node.iter():
        previous_child = None
        for i, child in enumerate(list(parent_elem)):
            if child == target_node:
                indent = previous_child.tail if previous_child is not None else parent_elem.text
                return i, parent_elem, indent
            previous_child = child
    return None, None, None

def get_sibling_attribute_tag_of_type(root_node, target_node, attribute_name):
    _, parent_tag, _ = get_parent_etree_node(root_node, target_node)
    if parent_tag is not None:
        if (node := parent_tag.xpath(f"./attribute[@name='{attribute_name}']")):
            return node[0]
    return None

def get_combined_invisible_condition(invisible_attribute, states_attribute):
    invisible_attribute = invisible_attribute.strip()
    states_attribute = states_attribute.strip()
    if not states_attribute:
        return invisible_attribute
    states_list = re.split(r"\s*,\s*", states_attribute)
    states_to_add = f"state not in {states_list}"
    if invisible_attribute:
        return f"({invisible_attribute}) or ({states_to_add})"
    return states_to_add

# --- Refactored Main Logic ---

def run_attrs_states_conversion(root_dir):
    """
    Finds all XML view files and converts 'attrs' and 'states' attributes.
    Returns a tuple of (succeeded_files, failed_files).
    """
    logging.info("--- Starting ATTRS/STATES Conversion ---")
    xml_files = get_xml_files_in_views_recursive(root_dir)
    succeeded_files, failed_files = [], []

    if not xml_files:
        logging.info("No XML files found in 'views' subdirectories.")
        return [], []

    for xml_file in xml_files:
        try:
            with open(xml_file, 'rb') as f:
                contents = f.read()
                original_encoding = 'utf-8'
                try:
                    decoded_contents = contents.decode('utf-8')
                except UnicodeDecodeError:
                    decoded_contents = contents.decode('latin-1')
                    original_encoding = 'latin-1'

            if 'attrs' not in decoded_contents and 'states' not in decoded_contents:
                continue

            logging.info(f"Processing {xml_file} for attrs/states conversion...")

            # Basic parsing, similar to original script
            has_encoding_declaration = re.search(r"\A.*<\?xml.*?encoding=.*?\?>\s*", decoded_contents, re.DOTALL)
            clean_contents = re.sub(r"\A.*<\?xml.*?encoding=.*?\?>\s*", "", decoded_contents, re.DOTALL)

            doc = etree.fromstring(clean_contents.encode(original_encoding))

            # Find all relevant tags
            tags_with_attrs = doc.xpath("//*[@attrs]")
            tags_with_states = doc.xpath("//*[@states]")

            # This is a simplified version of the original script's logic.
            # A full implementation would require all the detailed logic from the original script.
            # For now, we focus on a simple conversion.

            modified = False
            for tag in tags_with_attrs:
                attrs_str = tag.get('attrs', '')
                new_attrs = get_new_attrs(attrs_str)
                if new_attrs:
                    modified = True
                    for name, value in new_attrs.items():
                        tag.set(name, value)
                    del tag.attrib['attrs']

            for tag in tags_with_states:
                states_str = tag.get('states', '')
                if states_str:
                    modified = True
                    current_invisible = tag.get('invisible', '')
                    new_invisible = get_combined_invisible_condition(current_invisible, states_str)
                    tag.set('invisible', new_invisible)
                    del tag.attrib['states']

            if modified:
                xml_string = etree.tostring(doc, encoding=original_encoding, xml_declaration=bool(has_encoding_declaration))
                if b'\r\n' in contents:
                    xml_string = xml_string.replace(b'\n', b'\r\n')

                with open(xml_file, 'wb') as rf:
                    rf.write(xml_string)
                logging.info(f"  -> Successfully converted attrs/states in {xml_file}")
                succeeded_files.append(xml_file)

        except Exception as e:
            logging.error(f"Failed to process {xml_file}: {e}", exc_info=True)
            failed_files.append((xml_file, str(e)))

    logging.info("--- Finished ATTRS/STATES Conversion ---")
    return succeeded_files, failed_files


def run_tree_to_list_replacement(root_dir):
    """
    Replaces all occurrences of 'tree' with 'list' in all files.
    Returns a list of modified files.
    """
    logging.info("--- Starting 'tree' to 'list' Replacement ---")
    all_files = get_all_files_recursive(root_dir)
    modified_files = []

    for file_path in all_files:
        try:
            with open(file_path, 'rb') as f:
                contents_bytes = f.read()

            try:
                contents = contents_bytes.decode('utf-8')
                encoding = 'utf-8'
            except UnicodeDecodeError:
                contents = contents_bytes.decode('latin-1')
                encoding = 'latin-1'

            new_contents = re.sub(r'\bTree\b', 'List', contents, flags=re.IGNORECASE)
            new_contents = re.sub(r'\btree\b', 'list', new_contents)

            if new_contents != contents:
                logging.info(f"Replacing 'tree' with 'list' in: {file_path}")

                new_contents_bytes = new_contents.encode(encoding)
                if b'\r\n' in contents_bytes:
                    new_contents_bytes = new_contents_bytes.replace(b'\n', b'\r\n')

                with open(file_path, 'wb') as f:
                    f.write(new_contents_bytes)
                modified_files.append(file_path)

        except Exception as e:
            logging.warning(f"Could not process {file_path} for 'tree' replacement: {e}")

    logging.info("--- Finished 'tree' to 'list' Replacement ---")
    return modified_files


def run_odoo18_manifest_update(root_dir, new_author='Joel S. Martinez espinal', new_version='18.0.1.0.0'):
    """
    Updates __manifest__.py files for Odoo 18 compatibility.
    Returns a tuple of (succeeded_files, failed_files).
    """
    logging.info("--- Starting Odoo 18 Manifest Update ---")
    manifest_files = get_manifest_files_recursive(root_dir)
    succeeded_files, failed_files = [], []

    for file_path in manifest_files:
        try:
            with open(file_path, 'rb') as f:
                contents_bytes = f.read()

            try:
                contents = contents_bytes.decode('utf-8')
                encoding = 'utf-8'
            except UnicodeDecodeError:
                contents = contents_bytes.decode('latin-1')
                encoding = 'latin-1'

            manifest_dict = ast.literal_eval(contents)
            if not isinstance(manifest_dict, dict):
                raise ValueError("Manifest content is not a dictionary.")

            changed = False
            if manifest_dict.get('version') != new_version:
                manifest_dict['version'] = new_version
                changed = True
            if manifest_dict.get('author') != new_author:
                manifest_dict['author'] = new_author
                changed = True
            if 'maintainers' not in manifest_dict:
                manifest_dict['maintainers'] = [new_author]
                changed = True
            elif isinstance(manifest_dict.get('maintainers'), list) and new_author not in manifest_dict['maintainers']:
                manifest_dict['maintainers'].append(new_author)
                changed = True

            if changed:
                logging.info(f"Updating manifest for Odoo 18 in: {file_path}")
                new_contents = pprint.pformat(manifest_dict, indent=4, width=80)

                new_contents_bytes = new_contents.encode(encoding)
                if b'\r\n' in contents_bytes:
                    new_contents_bytes = new_contents_bytes.replace(b'\n', b'\r\n')

                with open(file_path, 'wb') as f:
                    f.write(new_contents_bytes)
                succeeded_files.append(file_path)

        except Exception as e:
            logging.error(f"Failed to update manifest {file_path}: {e}", exc_info=True)
            failed_files.append((file_path, str(e)))

    logging.info("--- Finished Odoo 18 Manifest Update ---")
    return succeeded_files, failed_files
