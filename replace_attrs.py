#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
from pathlib import Path
from lxml import etree
import ast  # For parsing __manifest__.py as a Python dictionary
import pprint # For writing back the dict nicely

NEW_ATTRS = ['invisible', 'required', 'readonly', 'column_invisible']


def get_xml_files_in_views_recursive(path):
    """
    Recursively finds all XML files within 'views' subdirectories of the given path.
    """
    xml_files = []
    for p in Path(path).glob('**/views/**/*.xml'):
        if p.is_file():
            xml_files.append(str(p))
    return xml_files


def get_all_files_recursive(path):
    """
    Recursively finds all files (not just XML) within the given path.
    """
    all_files = []
    # rglob('*') finds all files and directories recursively
    # filter for p.is_file() to get only files
    for p in Path(path).rglob('*'):
        if p.is_file():
            all_files.append(str(p))
    return all_files


def get_manifest_files_recursive(path):
    """
    Recursively finds all __manifest__.py files within the given path.
    """
    manifest_files = []
    for p in Path(path).glob('**/__manifest__.py'):
        if p.is_file():
            manifest_files.append(str(p))
    return manifest_files


def normalize_domain(domain):
    """
    Normalize Domain, taken from odoo/osv/expression.py -> just the part so that & operators are added where needed.
    After that, we can use a part of the def parse() from the same file to manage parenthesis for and/or

    :rtype: list[str|tuple]
    """
    if len(domain) == 1:
        return domain
    result = []
    expected = 1  # expected number of expressions
    op_arity = {'!': 1, '&': 2, '|': 2}
    for token in domain:
        if expected == 0:  # more than expected, like in [A, B]
            result[0:0] = ['&']  # put an extra '&' in front
            expected = 1
        if isinstance(token, (list, tuple)):  # domain term
            expected -= 1
            token = tuple(token)
        else:
            expected += op_arity.get(token, 0) - 1
        result.append(token)
    return result


def stringify_leaf(leaf):
    """
    :param tuple leaf:
    :rtype: str
    """
    stringify = ''
    switcher = False
    case_insensitive = False
    # Replace operators not supported in python (=, like, ilike)
    operator = str(leaf[1])
    # Take left operand, never to add quotes (should be python object / field)
    left_operand = leaf[0]
    # Take care of right operand, don't add quotes if it's list/tuple/set/boolean/number, check if we have a true/false/1/0 string tho.
    right_operand = leaf[2]

    # Handle '=?'
    if operator == '=?':
        if type(right_operand) is str:
            right_operand = f"'{right_operand}'"
        return f"({right_operand} in [None, False] or {left_operand} == {right_operand})"
    # Handle '='
    elif operator == '=':
        if right_operand in (False, []):  # Check for False or empty list
            return f"not {left_operand}"
        elif right_operand is True:  # Check for True using '==' comparison so only boolean values can evaluate to True
            return left_operand
        operator = '=='
    # Handle '!='
    elif operator == '!=':
        if right_operand in (False, []):  # Check for False or empty list
            return left_operand
        elif right_operand is True:  # Check for True using '==' comparison so only boolean values can evaluate to True
            return f"not {left_operand}"
    # Handle 'like' and other operators
    elif 'like' in operator:
        case_insensitive = 'ilike' in operator
        if type(right_operand) is str and re.search('[_%]', right_operand):
            # Since wildcards won't work/be recognized after conversion we throw an error so we don't end up with
            # expressions that behave differently from their originals
            raise Exception("Script doesn't support 'like' domains with wildcards")
        if operator in ['=like', '=ilike']:
            operator = '=='
        else:
            if 'not' in operator:
                operator = 'not in'
            else:
                operator = 'in'
            switcher = True
    if type(right_operand) is str:
        right_operand = f"'{right_operand}'"
    if switcher:
        temp_operand = left_operand
        left_operand = right_operand
        right_operand = temp_operand
    if not case_insensitive:
        stringify = f"{left_operand} {operator} {right_operand}"
    else:
        stringify = f"{left_operand}.lower() {operator} {right_operand}.lower()"
    return stringify


def stringify_attr(stack):
    """
    :param bool|str|int|list stack:
    :rtype: str
    """
    if stack in (True, False, 'True', 'False', 1, 0, '1', '0'):
        return str(stack)
    last_parenthesis_index = max(index for index, item in enumerate(stack[::-1]) if item not in ('|', '!'))
    stack = normalize_domain(stack)
    stack = stack[::-1]
    result = []
    for index, leaf_or_operator in enumerate(stack):
        if leaf_or_operator == '!':
            expr = result.pop()
            result.append('(not (%s))' % expr)
        elif leaf_or_operator in ['&', '|']:
            left = result.pop()
            # In case of a single | or single & , we expect that it's a tag that have an attribute AND a state
            # the state will be added as OR in states management
            try:
                right = result.pop()
            except IndexError:
                res = left + ('%s' % ' and' if leaf_or_operator == '&' else ' or')
                result.append(res)
                continue
            form = '(%s %s %s)'
            if index > last_parenthesis_index:
                form = '%s %s %s'
            result.append(form % (left, 'and' if leaf_or_operator == '&' else 'or', right))
        else:
            result.append(stringify_leaf(leaf_or_operator))
    result = result[0]
    return result


def get_new_attrs(attrs):
    """
    :param str attrs:
    :rtype: dict[bool|str|int]
    """
    new_attrs = {}
    # Temporarily replace dynamic variables (field reference, context value, %()d) in leafs by strings prefixed with '__dynamic_variable__.'
    # This way the evaluation won't fail on these strings and we can later identify them to convert back to  their original values
    escaped_operators = ['=', '!=', '>', '>=', '<', '<=', '=\?', '=like', 'like', 'not like', 'ilike', 'not ilike', '=ilike', 'in', 'not in', 'child_of', 'parent_of']
    attrs = re.sub("&lt;", "<", attrs)
    attrs = re.sub("&gt;", ">", attrs)
    attrs = re.sub(f"([\"'](?:{'|'.join(escaped_operators)})[\"']\\s*,\\s*)(?!False|True)([\\w\\.]+)(?=\\s*[\\]\\)])", r"\\1'__dynamic_variable__.\\2'", attrs)
    attrs = re.sub(r"(%\([\w\.]+\)d)", r"'__dynamic_variable__.\1'", attrs)
    attrs = attrs.strip()
    if re.search("^{.*}$", attrs, re.DOTALL):
        # attrs can be an empty value, in which case the eval() would fail, so only eval attrs representing dictionaries
        attrs_dict = eval(attrs.strip())
        for attr, attr_value in attrs_dict.items():
            if attr not in NEW_ATTRS:
                # We don't know what to do with attributes not in NEW_ATTR, so the user will have to process those
                # manually when checking the differences post-conversion
                continue
            stringified_attr = stringify_attr(attr_value)
            if type(stringified_attr) is str:
                # Convert dynamic variable strings back to their original form
                stringified_attr = re.sub(r"'__dynamic_variable__\.([^']+)'", r"\1", stringified_attr)
            new_attrs[attr] = stringified_attr
    return new_attrs


def get_parent_etree_node(root_node, target_node):
    """
    Returns the parent node of a given node, and the index and indentation of the target node in the parent node's direct child nodes list

    :param xml.etree.ElementTree.Element root_node:
    :param xml.etree.ElementTree.Element target_node:
    :returns: index, parent_node, indentation
    :rtype: (int, xml.etree.ElementTree.Element, str)
    """
    for parent_elem in root_node.iter():
        previous_child = False
        for i, child in enumerate(list(parent_elem)):
            if child == target_node:
                if previous_child:
                    indent = previous_child.tail
                else:
                    # For the first child element it's the text in between the parent's opening tag and the first child that determines indentation
                    indent = parent_elem.text
                return i, parent_elem, indent
            previous_child = child


def get_child_tag_at_index(parent_node, index):
    """
    Returns the child node of a node with a given index

    :param xml.etree.ElementTree.Element parent_node:
    :param int index:
    :returns: child_node
    :rtype: xml.etree.ElementTree.Element
    """
    for i, child in enumerate(list(parent_node)):
        if i == index:
            return child


def get_sibling_attribute_tag_of_type(root_node, target_node, attribute_name):
    """
    If it exists, returns the attribute tag with the same parent tag for the given name

    :param xml.etree.ElementTree.Element root_node:
    :param xml.etree.ElementTree.Element target_node:
    :param str attribute_name:
    :returns: attribute_tag with name="<attribute_name>"
    :rtype: xml.etree.ElementTree.Element
    """
    _, xpath_node, _ = get_parent_etree_node(root_node, target_node)
    if node := xpath_node.xpath(f"./attribute[@name='{attribute_name}']"):
        return node[0]


def get_inherited_tag_type(root_node, target_node):
    """
    Checks what the type of the tag is that the attribute tag applies to

    :param xml.etree.ElementTree.Element root_node:
    :param xml.etree.ElementTree.Element target_node:
    :rtype: str|None
    """
    _, parent_tag, _ = get_parent_etree_node(root_node, target_node)
    if expr := parent_tag.get('expr'):
        # Checks if the last part of the xpath expression is a tag name and returns it
        # If not (eg. if the pattern is for example expr="//field[@name='...']/.."), return None
        if matches := re.findall("^.*/(\\w+)[^/]*?$", expr):
            return matches[0]
    else:
        return parent_tag.tag


def get_combined_invisible_condition(invisible_attribute, states_attribute):
    """
    :param str invisible_attribute: invisible attribute condition already present on the same tag as the states
    :param str states_attribute: string of the form 'state1,state2,...'
    """
    invisible_attribute = invisible_attribute.strip()
    states_attribute = states_attribute.strip()
    if not states_attribute:
        return invisible_attribute
    states_list = re.split(r"\s*,\s*", states_attribute.strip())
    states_to_add = f"state not in {states_list}"
    if invisible_attribute:
        if invisible_attribute.endswith('or') or invisible_attribute.endswith('and'):
            combined_invisible_condition = f"{invisible_attribute} {states_to_add}"
        else:
            combined_invisible_condition = f"({invisible_attribute}) or ({states_to_add})"
    else:
        combined_invisible_condition = states_to_add
    return combined_invisible_condition


def replace_tree_with_list_in_file(file_path):
    """
    Replaces all occurrences of the word 'tree' with 'list' in the given file.
    Handles different line endings and preserves encoding.
    Returns True if changes were made, False otherwise.
    """
    try:
        with open(file_path, 'rb') as f:
            original_contents_bytes = f.read()

        # Detect encoding and line ending
        try:
            contents = original_contents_bytes.decode('utf-8')
            encoding = 'utf-8'
        except UnicodeDecodeError:
            try:
                contents = original_contents_bytes.decode('latin-1')
                encoding = 'latin-1'
            except UnicodeDecodeError:
                print(f"Warning: Could not decode {file_path} with utf-8 or latin-1. Skipping 'tree' to 'list' replacement for this file.")
                return False

        convert_line_separator_back_to_windows = '\r\n' in contents

        # Use word boundaries to avoid replacing parts of other words (e.g., 'detree' -> 'delist')
        # We also need to consider case-insensitivity for a robust replacement
        new_contents = re.sub(r'\bTree\b', 'List', contents, flags=re.IGNORECASE)
        new_contents = re.sub(r'\btree\b', 'list', new_contents)


        if new_contents != contents:
            print(f"  - Replacing 'tree' with 'list' in: {file_path}")
            if convert_line_separator_back_to_windows:
                new_contents_bytes = new_contents.replace('\n', '\r\n').encode(encoding)
            else:
                new_contents_bytes = new_contents.encode(encoding)

            with open(file_path, 'wb') as f:
                f.write(new_contents_bytes)
            return True
        return False
    except Exception as e:
        print(f"Error replacing 'tree' in {file_path}: {e}")
        return False


def update_manifest_for_odoo18(file_path):
    """
    Updates __manifest__.py file for Odoo 18 compatibility and sets author.
    Returns True if changes were made, False otherwise.
    """
    try:
        with open(file_path, 'rb') as f:
            original_contents_bytes = f.read()

        # Detect encoding
        try:
            contents = original_contents_bytes.decode('utf-8')
            encoding = 'utf-8'
        except UnicodeDecodeError:
            try:
                contents = original_contents_bytes.decode('latin-1')
                encoding = 'latin-1'
            except UnicodeDecodeError:
                print(f"Warning: Could not decode {file_path} with utf-8 or latin-1. Skipping manifest update for this file.")
                return False

        # Attempt to parse as a Python dictionary
        try:
            # __manifest__.py typically contains a single dictionary
            manifest_dict = ast.literal_eval(contents)
            if not isinstance(manifest_dict, dict):
                raise ValueError("Content is not a dictionary.")
        except (SyntaxError, ValueError) as e:
            print(f"Warning: Could not parse {file_path} as a Python dictionary: {e}. Skipping manifest update.")
            return False

        changed = False
        new_author = 'Joel S. Martinez espinal'
        new_version = '18.0.1.0.0'

        # 1. Update version
        if manifest_dict.get('version') != new_version:
            manifest_dict['version'] = new_version
            changed = True

        # 2. Set author
        if manifest_dict.get('author') != new_author:
            manifest_dict['author'] = new_author
            changed = True

        # 3. Add / update maintainers
        if 'maintainers' not in manifest_dict:
            manifest_dict['maintainers'] = [new_author]
            changed = True
        elif isinstance(manifest_dict.get('maintainers'), list):
            if new_author not in manifest_dict['maintainers']:
                manifest_dict['maintainers'].append(new_author)
                changed = True
        else: # maintainers exists but is not a list (e.g., a string)
            print(f"Warning: 'maintainers' in {file_path} is not a list. Skipping update for maintainers.")


        if changed:
            # Use pprint to format the dictionary for writing back
            # pprint.pformat already formats it as {...}
            new_contents = pprint.pformat(manifest_dict, indent=4, width=80, compact=False)

            # Preserve line endings
            convert_line_separator_back_to_windows = '\r\n' in contents
            if convert_line_separator_back_to_windows:
                new_contents_bytes = new_contents.replace('\n', '\r\n').encode(encoding)
            else:
                new_contents_bytes = new_contents.encode(encoding)

            with open(file_path, 'wb') as f:
                f.write(new_contents_bytes)
            print(f"  - Updated manifest for Odoo 18 and author in: {file_path}")
            return True
        return False
    except Exception as e:
        print(f"Error updating manifest {file_path}: {e}")
        return False


# --- Main Script Execution ---

root_dir = input('Enter root directory to check (empty for current directory) : ')
root_dir = root_dir or '.'

# --- ATTRS/STATES CONVERSION IN VIEWS ---
print("\n--- ATTRS/STATES CONVERSION IN VIEWS ---")
all_xml_files_for_attrs_states = get_xml_files_in_views_recursive(root_dir)

autoreplace_attrs_states = input('Do you want to auto-replace attrs/states attributes? (y/n) (empty == no) : ') or 'n'
ok_attrs_states_files = []
nok_attrs_states_files = []
nofilesfound_attrs_states = True


for xml_file in all_xml_files_for_attrs_states:
    try:
        with open(xml_file, 'rb') as f:
            contents = f.read().decode('utf-8')
            f.close()
            if not ('attrs' in contents or 'states' in contents):
                continue
            convert_line_separator_back_to_windows = False
            if '\r\n' in contents:
                convert_line_separator_back_to_windows = True

            has_encoding_declaration = False
            if encoding_declaration := re.search(r"\A.*<\?xml.*?encoding=.*?\?>\s*", contents, re.DOTALL):
                has_encoding_declaration = True
                contents = re.sub(r"\A.*<\?xml.*?encoding=.*?\?>\s*", "", contents, re.DOTALL)

            doc = etree.fromstring(contents)
            tags_with_attrs = doc.xpath("//*[@attrs]")
            attribute_tags_with_attrs = doc.xpath("//attribute[@name='attrs']")
            tags_with_states = doc.xpath("//*[@states]")
            attribute_tags_with_states = doc.xpath("//attribute[@name='states']")
            if not (tags_with_attrs or attribute_tags_with_attrs or tags_with_states or attribute_tags_with_states):
                continue

            nofilesfound_attrs_states = False
            print('\n#############################' + ((6 + len(xml_file)) * '#'))
            print('##### Taking care of file -> %s' % xml_file)
            print('\n##### Current tags found #####\n')
            for t in tags_with_attrs + attribute_tags_with_attrs + tags_with_states + attribute_tags_with_states:
                print(etree.tostring(t, encoding='unicode'))

            # Management of tags that have attrs=""
            for tag in tags_with_attrs:
                all_attributes = []
                attrs = tag.get('attrs', '')
                new_attrs = get_new_attrs(attrs)
                for attr_name, attr_value in list(tag.attrib.items()):
                    if attr_name == 'attrs':
                        for new_attr, new_attr_value in new_attrs.items():
                            if new_attr in tag.attrib:
                                old_attr_value = tag.attrib.get(new_attr)
                                if old_attr_value in [True, 1, 'True', '1']:
                                    new_attr_value = f"True or ({new_attr_value})"
                                elif old_attr_value in [False,  0, 'False', '0']:
                                    new_attr_value = f"False or ({new_attr_value})"
                                else:
                                    new_attr_value = f"({old_attr_value}) or ({new_attr_value})"
                            all_attributes.append((new_attr, new_attr_value))
                    elif attr_name not in new_attrs:
                        all_attributes.append((attr_name, attr_value))
                tag.attrib.clear()
                tag.attrib.update(all_attributes)

            # Management of <attributes name="attrs">... overrides
            attribute_tags_with_attrs_after = []
            for attribute_tag in attribute_tags_with_attrs:
                tag_type = get_inherited_tag_type(doc, attribute_tag)
                tag_index, parent_tag, indent = get_parent_etree_node(doc, attribute_tag)
                tail = attribute_tag.tail or ''
                attrs = attribute_tag.text or ''
                new_attrs = get_new_attrs(attrs)
                attribute_tags_to_remove = []
                for new_attr, new_attr_value in new_attrs.items():
                    if (separate_attr_tag := get_sibling_attribute_tag_of_type(doc, attribute_tag, new_attr)) is not None:
                        attribute_tags_to_remove.append(separate_attr_tag)
                        old_attr_value = separate_attr_tag.text
                        if old_attr_value in [True, 1, 'True', '1']:
                            new_attr_value = f"True or ({new_attr_value})"
                        elif old_attr_value in [False,  0, 'False', '0']:
                            new_attr_value = f"False or ({new_attr_value})"
                        else:
                            new_attr_value = f"({old_attr_value}) or ({new_attr_value})"
                    new_tag = etree.Element('attribute', attrib={
                        'name': new_attr
                    })
                    new_tag.text = str(new_attr_value)
                    new_tag.tail = indent
                    parent_tag.insert(tag_index, new_tag)
                    if new_attr == 'invisible':
                        if get_sibling_attribute_tag_of_type(doc, new_tag, 'states') is None:
                            todo_tag = etree.Comment(
                                f"TODO: Result from 'attrs' -> 'invisible' conversion without also overriding 'states' attribute"
                                f"{indent + (' ' * 5)}Check if this {tag_type + ' ' if tag_type else ''}tag contained a states attribute in any of the parent views, in which case it should be combined into this 'invisible' attribute"
                                f"{indent + (' ' * 5)}(If any states attributes existed in parent views, they'll also be marked with a TODO)")
                            todo_tag.tail = indent
                            parent_tag.insert(tag_index, todo_tag)
                            attribute_tags_with_attrs_after.append(todo_tag)
                            tag_index += 1
                    attribute_tags_with_attrs_after.append(new_tag)
                    tag_index += 1
                missing_attrs = []
                if tag_type == 'field':
                    potentially_missing_attrs = NEW_ATTRS
                else:
                    potentially_missing_attrs = ['invisible']
                for missing_attr in potentially_missing_attrs:
                    if missing_attr not in new_attrs and get_sibling_attribute_tag_of_type(doc, attribute_tag, missing_attr) is None:
                        missing_attrs.append(missing_attr)
                if missing_attrs:
                    if tag_type == 'field':
                        new_tag = etree.Comment(
                            f"TODO: Result from converting 'attrs' attribute override without options for {missing_attrs} to separate attributes"
                            f"{indent + (' ' * 5)}Remove redundant empty tags below for any of those attributes that are not present in the field tag in any of the parent views"
                            f"{indent + (' ' * 5)}If someone later adds one of these attributes in the parent views, they would likely be unaware it's still overridden in this view, resulting in unexpected behaviour, which should be avoided")
                        new_tag.tail = indent
                        parent_tag.insert(tag_index, new_tag)
                        attribute_tags_with_attrs_after.append(new_tag)
                        tag_index += 1
                    else:
                        pass # Only invisible for non-field tags, no extra TODO needed if attrs dict didn't provide it
                    for missing_attr in missing_attrs:
                        new_tag = etree.Element('attribute', attrib={
                            'name': missing_attr
                        })
                        new_tag.tail = indent
                        parent_tag.insert(tag_index, new_tag)
                        if missing_attr == 'invisible':
                            if get_sibling_attribute_tag_of_type(doc, new_tag, 'states') is None:
                                todo_tag = etree.Comment(
                                    f"TODO: Result from 'attrs' -> 'invisible' conversion without also overriding 'states' attribute"
                                    f"{indent + (' ' * 5)}Check if this {tag_type + ' ' if tag_type else ''}tag contained a states attribute in any of the parent views, that should be combined into this 'invisible' attribute"
                                    f"{indent + (' ' * 5)}(If any states attributes existed in parent views, they'll also be marked with a TODO)")
                                todo_tag.tail = indent
                                parent_tag.insert(tag_index, todo_tag)
                                attribute_tags_with_attrs_after.append(todo_tag)
                                tag_index += 1
                        attribute_tags_with_attrs_after.append(new_tag)
                        tag_index += 1
                # This ensures the tail of the last inserted attribute tag is set correctly
                if attribute_tags_with_attrs_after:
                    attribute_tags_with_attrs_after[-1].tail = tail
                parent_tag.remove(attribute_tag)
                for attribute_tag_to_remove in attribute_tags_to_remove:
                    tag_index, parent_tag, indent = get_parent_etree_node(doc, attribute_tag_to_remove)
                    if tag_index > 0:
                        previous_tag = get_child_tag_at_index(parent_tag, tag_index - 1)
                        previous_tag.tail = attribute_tag_to_remove.tail
                    parent_tag.remove(attribute_tag_to_remove)

            # Management of tags that have states=""
            for state_tag in tags_with_states:
                states_attribute = state_tag.get('states', '')
                invisible_attribute = state_tag.get('invisible', '')
                tag_index, parent_tag, indent = get_parent_etree_node(doc, state_tag)
                if invisible_attribute:
                    conversion_action_string = f"Result from merging \"states='{states_attribute}'\" attribute with an 'invisible' attribute"
                else:
                    conversion_action_string = f"Result from converting \"states='{states_attribute}'\" attribute into an 'invisible' attribute"
                todo_tag = etree.Comment(
                    f"TODO: {conversion_action_string}"
                    f"{indent + (' ' * 5)}Manually combine states condition into any 'invisible' overrides in inheriting views as well")
                todo_tag.tail = indent
                parent_tag.insert(tag_index, todo_tag)

                new_invisible_attribute = get_combined_invisible_condition(invisible_attribute, states_attribute)
                all_attributes = []
                for attr_name, attr_value in list(state_tag.attrib.items()):
                    if attr_name == 'invisible' or (attr_name == 'states' and not invisible_attribute):
                        if new_invisible_attribute:
                            all_attributes.append(('invisible', new_invisible_attribute))
                    elif attr_name != 'states':
                        all_attributes.append((attr_name, attr_value))
                state_tag.attrib.clear()
                state_tag.attrib.update(all_attributes)

            # Management of <attribute name="states">... overrides
            attribute_tags_with_states_after = []
            for attribute_tag_states in attribute_tags_with_states:
                tag_type = get_inherited_tag_type(doc, attribute_tag_states)
                tag_index, parent_tag, indent = get_parent_etree_node(doc, attribute_tag_states)
                tail = attribute_tag_states.tail
                attribute_tag_invisible = get_sibling_attribute_tag_of_type(doc, attribute_tag_states, 'invisible')
                if attribute_tag_invisible is not None:
                    if tag_index > 0:
                        previous_tag = get_child_tag_at_index(parent_tag, tag_index - 1)
                        previous_tag.tail = attribute_tag_states.tail
                else:
                    todo_tag = etree.Comment(
                        f"TODO: Result from \"states='{states_attribute}'\" -> 'invisible' conversion without also overriding 'attrs' attribute"
                        f"{indent + (' ' * 5)}Check if this {tag_type + ' ' if tag_type else ''}tag contains an invisible attribute in any of the parent views, in which case it should be combined into this new 'invisible' attribute"
                        f"{indent + (' ' * 5)}(Only applies to invisible attributes in the parent views that were not originally states attributes. Those from converted states attributes will be marked with a TODO)")
                    todo_tag.tail = indent
                    parent_tag.insert(tag_index, todo_tag)
                    attribute_tags_with_states_after.append(todo_tag)
                    tag_index += 1
                    attribute_tag_invisible = etree.Element('attribute', attrib={'name': 'invisible'})
                    attribute_tag_invisible.tail = tail
                    parent_tag.insert(tag_index, attribute_tag_invisible)

                invisible_attribute = attribute_tag_invisible.text or ''
                states_attribute = attribute_tag_states.text or ''
                invisible_condition = get_combined_invisible_condition(invisible_attribute, states_attribute)
                parent_tag.remove(attribute_tag_states)
                attribute_tag_invisible.text = invisible_condition
                attribute_tags_with_states_after.append(attribute_tag_invisible)

            print('\n##### Will be replaced by #####\n')
            for t in tags_with_attrs + attribute_tags_with_attrs_after + tags_with_states + attribute_tags_with_states_after:
                print(etree.tostring(t, encoding='unicode'))
            print('\n###############################\n')
            if autoreplace_attrs_states.lower()[0] == 'n':
                confirm = input('Do you want to replace? (y/n) (empty == no) : ') or 'n'
            else:
                confirm = 'y'
            if confirm.lower()[0] == 'y':
                with open(xml_file, 'wb') as rf:
                    xml_string = etree.tostring(doc, encoding='utf-8', xml_declaration=has_encoding_declaration)
                    if convert_line_separator_back_to_windows:
                        xml_string = xml_string.replace(b"\n", b"\r\n")
                    rf.write(xml_string)
                    ok_attrs_states_files.append(xml_file)
    except Exception as e:
        nok_attrs_states_files.append((xml_file, e))
        print(f"Error processing {xml_file}: {e}") # Print the error for clarity


print('\n################################################')
print('################# ATTRS/STATES Conversion Summary ################')
print('################################################')

if nofilesfound_attrs_states:
    print(f'No XML Files with "attrs" or "states" found in "views" subdirectories under " {root_dir} "')

print('\nSucceeded on files:')
for file in ok_attrs_states_files:
    print(file)
if not ok_attrs_states_files:
    print('No files')
print('\nFailed on files:')
for file in nok_attrs_states_files:
    print(file[0])
    print('Reason: ', file[1])
if not nok_attrs_states_files:
    print('No files')


# --- TREE TO LIST REPLACEMENT ---
print("\n--- 'tree' to 'list' REPLACEMENT ---")
perform_tree_to_list = input("Do you want to replace 'tree' with 'list' in all files? (y/n) (empty == no) : ") or 'n'

ok_tree_list_files = []
nok_tree_list_files = []
files_processed_for_tree_list = False

if perform_tree_to_list.lower()[0] == 'y':
    all_files_for_tree_list = get_all_files_recursive(root_dir)
    if not all_files_for_tree_list:
        print(f"No files found in '{root_dir}' for 'tree' to 'list' replacement.")
    else:
        for file_path in all_files_for_tree_list:
            files_processed_for_tree_list = True
            if replace_tree_with_list_in_file(file_path):
                ok_tree_list_files.append(file_path)
            # else, replacement either didn't occur or an error happened (already printed in function)

print('\n################################################')
print("################# 'tree' to 'list' Replacement Summary ################")
print('################################################')

if not files_processed_for_tree_list and perform_tree_to_list.lower()[0] == 'y':
     print(f"No files were processed for 'tree' to 'list' replacement. Ensure '{root_dir}' contains files.")
elif not files_processed_for_tree_list:
    print("Skipped 'tree' to 'list' replacement.")

print('\nSucceeded on files:')
for file in ok_tree_list_files:
    print(file)
if not ok_tree_list_files:
    print('No files modified.')


# --- ODOO 18 MANIFEST UPDATE ---
print("\n--- ODOO 18 MANIFEST UPDATE ---")
perform_manifest_update = input("Do you want to update __manifest__.py files for Odoo 18 and set author? (y/n) (empty == no) : ") or 'n'

ok_manifest_files = []
nok_manifest_files = []
files_processed_for_manifest = False

if perform_manifest_update.lower()[0] == 'y':
    all_manifest_files = get_manifest_files_recursive(root_dir)
    if not all_manifest_files:
        print(f"No __manifest__.py files found in '{root_dir}'.")
    else:
        for file_path in all_manifest_files:
            files_processed_for_manifest = True
            if update_manifest_for_odoo18(file_path):
                ok_manifest_files.append(file_path)
            # else, replacement either didn't occur or an error happened (already printed in function)

print('\n################################################')
print("################# Odoo 18 Manifest Update Summary ################")
print('################################################')

if not files_processed_for_manifest and perform_manifest_update.lower()[0] == 'y':
     print(f"No __manifest__.py files were processed. Ensure '{root_dir}' contains modules.")
elif not files_processed_for_manifest:
    print("Skipped Odoo 18 manifest update.")

print('\nSucceeded on files:')
for file in ok_manifest_files:
    print(file)
if not ok_manifest_files:
    print('No files modified.')

print('\n################################################')
print('################## Script Finished ##################')
print('################################################')