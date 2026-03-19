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

FROM ubuntu:22.04 AS builder

LABEL maintainer="Free5GC <support@free5gc.org>"

ENV DEBIAN_FRONTEND noninteractive

ARG TARGET_ARCH=x86_64
# TARGET_ARCH support : x86_64, aarch64(arm64)

# Install dependencies
RUN apt-get update

RUN apt-get install python3-pip wget git gcc make g++ libsctp-dev lksctp-tools iproute2 libssl-dev -y
RUN pip install --upgrade cmake

RUN git clone -b master -j `nproc` https://github.com/aligungr/UERANSIM \
    && cd ./UERANSIM \
    && git checkout 3a96298 \
    && make

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND noninteractive

# Install runtime dependencies + ping
RUN apt-get update \
    && apt-get install libsctp-dev lksctp-tools iproute2 iputils-ping libstdc++6 -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ueransim

RUN mkdir -p config/ binder/

COPY --from=builder /UERANSIM/build/nr-gnb .
COPY --from=builder /UERANSIM/build/nr-ue .
COPY --from=builder /UERANSIM/build/nr-cli .
COPY --from=builder /UERANSIM/build/nr-binder binder/
COPY --from=builder /UERANSIM/build/libdevbnd.so binder/

VOLUME [ "/ueransim/config" ]
