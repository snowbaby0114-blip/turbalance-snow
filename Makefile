IMAGE := custom-scheduler:latest

.PHONY: venv test cluster build load deploy pods logs nodes clean

venv:
	python -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

test: venv
	.venv/bin/python -m pytest tests/ -v

cluster:
	minikube start --nodes 2 --cpus 2 --memory 2300 --driver=docker

build:
	docker build -t $(IMAGE) .

load: build
	minikube image load $(IMAGE)

deploy: load
	kubectl apply -f scheduler-rbac.yaml
	kubectl apply -f scheduler-deployment.yaml
	kubectl rollout status deployment/custom-scheduler

pods:
	kubectl apply -f pod1.yaml
	kubectl apply -f pod2.yaml
	kubectl apply -f pod3.yaml
	kubectl get pods -o wide

logs:
	kubectl logs -l app=custom-scheduler -f

nodes:
	kubectl get nodes -o wide

clean:
	kubectl delete -f pod1.yaml -f pod2.yaml -f pod3.yaml --ignore-not-found
	kubectl delete -f scheduler-deployment.yaml -f scheduler-rbac.yaml --ignore-not-found
	minikube delete
