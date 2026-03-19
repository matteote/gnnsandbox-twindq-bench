DOCKER_IMAGE_OWNER = {{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/networkagent
DOCKER_IMAGE_TAG = latest

.PHONY: base
all: ueransim base amf ausf nrf nssf pcf smf udm udr n3iwf upf webconsole

base:
	docker build -t free5gc/base:latest ./base
	docker image ls free5gc/base:latest
smf: base
	docker build --build-arg F5GC_MODULE=smf -t free5gc/smf-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/smf:${DOCKER_IMAGE_TAG} ./nf_smf
amf: base
	docker build --build-arg F5GC_MODULE=amf -t free5gc/amf-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/amf:${DOCKER_IMAGE_TAG} ./nf_amf
upf: base
	docker build --build-arg F5GC_MODULE=upf -t free5gc/upf-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/upf:${DOCKER_IMAGE_TAG} ./nf_upf
udr: base
	docker build --build-arg F5GC_MODULE=udr -t free5gc/udr-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/udr:${DOCKER_IMAGE_TAG} ./nf_udr
udm: base
	docker build --build-arg F5GC_MODULE=udm -t free5gc/udm-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/udm:${DOCKER_IMAGE_TAG} ./nf_udm
nrf: base
	docker build --build-arg F5GC_MODULE=nrf -t free5gc/nrf-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/nrf:${DOCKER_IMAGE_TAG} ./nf_nrf
nssf: base
	docker build --build-arg F5GC_MODULE=nssf -t free5gc/nssf-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/nssf:${DOCKER_IMAGE_TAG} ./nf_nssf
n3iwf: base
	docker build --build-arg F5GC_MODULE=n3iwf -t free5gc/n3iwf-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/n3iwf:${DOCKER_IMAGE_TAG} ./nf_n3iwf
pcf: base
	docker build --build-arg F5GC_MODULE=pcf -t free5gc/pcf-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/pcf:${DOCKER_IMAGE_TAG} ./nf_pcf/
ausf: base
	docker build --build-arg F5GC_MODULE=ausf -t free5gc/ausf-base:latest -f ./base/Dockerfile.nf ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/ausf:${DOCKER_IMAGE_TAG}  ./nf_ausf
webconsole: base
	docker build -t free5gc/webconsole-base:latest -f ./base/Dockerfile.nf.webconsole ./base
	docker build -t ${DOCKER_IMAGE_OWNER}/webconsole:${DOCKER_IMAGE_TAG} ./webui
ueransim: base
	docker build -t ${DOCKER_IMAGE_OWNER}/ueransim:${DOCKER_IMAGE_TAG} ./ueransim
