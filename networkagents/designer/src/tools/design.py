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

import os

def getDesignDoc()-> str :
    """
    Retrieve the Vyos network design documentation.

    Args:
        None

    Returns:
        str: The network design documentation as a string
    """
    doc_path = os.path.join(os.path.dirname(__file__), "data", "designdoc.md")
    with open(os.path.abspath(doc_path), "r") as f:
        return f.read()

    