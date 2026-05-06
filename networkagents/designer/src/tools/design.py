# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import re
import utils.git as git
import yaml

logger = logging.getLogger(__name__)

# Custom YAML representer to preserve quotes for numeric strings
def represent_str(dumper, data):
    """
    Custom string representer that preserves quotes for numeric strings
    that should remain as strings (e.g., phone numbers, IDs starting with 0)
    """
    # Preserve quotes for numeric strings that start with 0 or other patterns
    # that should remain as strings
    if isinstance(data, str) and data.isdigit() and data.startswith('0'):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

# Register the custom representer
yaml.add_representer(str, represent_str)

def getDesignDoc()-> str :
    """
    Retrieve the Vyos network design documentation.

    Args:
        None

    Returns:
        str: The network design documentation as a string
    """
    logger.info("Getting network design from git")
    filename = "designdoc.md"
    result = git.get_git_file(git.DESIGN_REPO, filename)
    if result is not None:
        return result
    else:
        logger.error(f"{filename} could not be found")
        return None

def deployDescriptor(descriptor: str):
    """
    write a new/updated custom resource descriptor to git

    Args:
        descriptor: kubernetes custom resource descriptor as a YAML string
    Returns:
        success or failure message
    """
    logger.info("Deploying descriptor: %s", descriptor)

    # Strip markdown code fences if the LLM wrapped the YAML (e.g. ```yaml ... ```)
    descriptor = descriptor.strip()
    descriptor = re.sub(r'^```[^\n]*\n', '', descriptor)
    descriptor = re.sub(r'\n```\s*$', '', descriptor)

    parsed = yaml.safe_load(descriptor)
    if not parsed or not isinstance(parsed, dict):
        return "Error: could not parse descriptor YAML"

    name = parsed.get("metadata", {}).get("name")
    if not name:
        return "Error: descriptor is missing metadata.name"

    cr_yaml = yaml.dump(parsed, indent=2, allow_unicode=True, default_flow_style=False)
    filename = name+".yaml"
    result = git.commit_git_file(filename,
                                f"Deployment of {name} custom resource",
                                cr_yaml)
    if result:
        logger.info(f"resource {filename} successfully submitted for deployment")
        return f"resource {filename} successfully submitted for deployment"
    else:
        logger.error(f"resource {filename} error deploying:\n```yaml\n{cr_yaml}\n```")
        return f"resource {filename} error deploying"

def deleteDescriptor(name: str) -> str:
    """
    Delete a VyOS Custom Resource from the cluster.

    Args:
        name (str): The name of the resource

    Returns:
        str: Success or error message
    """
    logger.info(f"Deleting resource: {name}")

    filename = f"{name}.yaml"
    result = git.delete_git_file(filename, f"{name} deletion")
    if result:
        return f"service {name} successfully submitted for deletion"
    else:
        return f"service {name} could not be deleted"
