#!/usr/bin/bash
#
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

TAG=${1-"latest"}

NF_LIST="nrf amf smf udr pcf udm nssf ausf n3iwf upf"

cd base

if [ 'xlatest' == "x$TAG" ]; then
    git clone --recursive -j `nproc` https://github.com/free5gc/free5gc.git
else
    TAG=`echo "$TAG" | sed -e "s/refs\/tags\///g"`
    git clone --recursive -b ${TAG} -j `nproc` https://github.com/free5gc/free5gc.git
fi;

cd -

make all
docker compose -f docker-compose-build.yaml build

for NF in ${NF_LIST}; do
    docker tag free5gc-compose_free5gc-${NF}:latest free5gc/${NF}:${TAG}
    docker push free5gc/${NF}:${TAG}
done


docker tag free5gc-compose_free5gc-webui:latest free5gc/webui:${TAG}
docker tag free5gc-compose_ueransim:latest free5gc/ueransim:${TAG}

docker push free5gc/webui:${TAG}
docker push free5gc/ueransim:${TAG}