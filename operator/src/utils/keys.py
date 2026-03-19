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

from base64 import b64encode

from nacl.public import PrivateKey as _PrivateKey

class WgKey:
    """Wireguard key pair"""

    def __init__(self):
        self._key = _PrivateKey.generate()
        self._name = None

    def __str__(self) -> str:
        return self.pubkey

    @property
    def pubkey(self) -> str:
        """The base 64 encoded public key"""
        return b64encode(bytes(self._key.public_key)).decode("ascii")

    @property
    def privkey(self) -> str:
        """The base 64 encoded private key"""
        return b64encode(bytes(self._key)).decode("ascii")

    @property
    def name(self):
        """The name of the key

        Based on the string searched in the public key.
        """
        return self._name

    @name.setter
    def name(self, value) -> None:
        self._name = value

    def to_dict(self):
        return {
            'private': self.privkey,
            'public': self.pubkey
        }
