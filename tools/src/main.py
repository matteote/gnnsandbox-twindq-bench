# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may in a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
import asyncio
import uvicorn
from utils.globals import networkagent_mcp


log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

# import all tools
# import tools.logs_fetch
# import tools.logs_query
import tools.tests
import tools.devices

sse_app = networkagent_mcp.http_app(transport="sse", stateless_http=True)

async def main():
    """Starts the server."""
    logger.info("starting network agent tools server...")
    config = uvicorn.Config(app=sse_app, host="0.0.0.0", port=8080, log_level="info", workers=1)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
