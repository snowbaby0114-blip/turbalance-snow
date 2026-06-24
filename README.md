# Custom Scheduler — FDE Recruitment Task

This repo contains a working solution for the scheduler task in [DIRIGENT.md](DIRIGENT.md):
a 2-node minikube cluster, a custom Kubernetes scheduler (`scheduler.py`) deployed into it,
and three pods (`pod1`, `pod2`, `pod3`) scheduled through it in order.

## Status of this submission

**The cluster setup and pod scheduling below have not been executed live in this
environment.** The machine used to prepare this submission cannot run Docker Desktop /
minikube: Windows reports `wsl --status` → *"WSL2 is not supported with your current
machine configuration... ensure virtualization is enabled in the BIOS"*, and there is no
admin access in this session to enable the "Virtual Machine Platform" feature or reboot.
A remote sandbox was also attempted and hit the identical restriction.

Everything below — the Dockerfile, RBAC, Deployment manifest, and commands — is the actual
solution and has been written to be applied as-is. The "Expected placement" section traces
the scheduler's own logic by hand against `pod1`/`pod2`/`pod3` so the reasoning can be
checked against real output once run on a machine with virtualization enabled.

## Files

| File | Purpose |
|---|---|
| `scheduler.py` | The custom scheduler (provided, unmodified) |
| `pod1.yaml`, `pod2.yaml`, `pod3.yaml` | The three pods to schedule (provided, unmodified) |
| `Dockerfile` | Packages `scheduler.py` into an image runnable inside the cluster |
| `scheduler-rbac.yaml` | ServiceAccount + ClusterRole + ClusterRoleBinding the scheduler needs to list nodes/pods and create bindings |
| `scheduler-deployment.yaml` | Runs the scheduler as a single-replica Deployment with `NODE_MEM_LIMIT_MB=2048` |

## 1. Cluster setup (2 nodes, ≥2GB each)

```bash
minikube start --nodes 2 --cpus 2 --memory 2300 --driver=docker
kubectl get nodes -o wide
```

`--memory 2300` gives each minikube node node ~2.3GB of VM memory, comfortably over the
2GB floor the task asks for, leaving headroom above the 2048MB the scheduler is told to
assume (`NODE_MEM_LIMIT_MB`) for system pods (kube-proxy, coredns, etc.) so they don't
eat into the budget the scheduler is reasoning about. As called out in the task, minikube's
`--memory` only bounds the VM, not guaranteed per-node allocatable — that's exactly why the
scheduler is told to use a fixed `NODE_MEM_LIMIT_MB=2048` instead of trusting
`node.status.allocatable`.

## 2. Build the scheduler image and load it into the cluster

```bash
docker build -t custom-scheduler:latest .
minikube image load custom-scheduler:latest
```

(`minikube image load` works the same way regardless of driver/OS — no need to fight with
`minikube docker-env`.)

## 3. Deploy the scheduler

```bash
kubectl apply -f scheduler-rbac.yaml
kubectl apply -f scheduler-deployment.yaml
kubectl get pods -l app=custom-scheduler -o wide   # wait for Running
kubectl logs -l app=custom-scheduler -f             # leave this tailing in a second terminal
```

The scheduler Deployment itself intentionally does **not** set `schedulerName:
custom-scheduler` — it has to start via the default scheduler, otherwise nothing would ever
bind it.

## 4. Schedule pod1, pod2, pod3 in order

```bash
kubectl apply -f pod1.yaml
kubectl apply -f pod2.yaml
kubectl apply -f pod3.yaml

kubectl get pods -o wide
```

Each `pod*.yaml` sets `schedulerName: custom-scheduler`, so the default scheduler ignores
them; they sit `Pending` until `scheduler.py`'s watch loop picks them up and binds them.

To confirm the custom scheduler — not the default one — did the binding, check its logs
(per the task's note) for one `Assigning pod ... / Optimal node for pod ...` pair per pod,
and cross-check with:

```bash
kubectl get pods -o=jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\n"}{end}'
```

## How the scheduler works

`scheduler.py` implements a minimal **least-loaded-node** (memory-balancing) scheduler:

1. It watches `Pending` pods in the `default` namespace and filters to only those with
   `spec.schedulerName == "custom-scheduler"` — pods using the default scheduler are
   ignored entirely.
2. For each such pod, it computes the pod's total memory **request** (sum across
   containers, parsed from values like `600Mi`).
3. It lists every `Ready` node, and for each one sums the memory requests of all pods
   *already bound* to that node (`get_nodes_requested_memory`) — this is the node's current
   load as the scheduler sees it.
4. Because minikube doesn't reliably expose real per-node allocatable memory across OSes/
   drivers, the scheduler doesn't trust `node.status.allocatable["memory"]`. Instead it uses
   a fixed ceiling per node, `NODE_MEM_LIMIT_MB` (defaulting to 2048 MB), as the artificial
   capacity it balances against.
5. It picks the `Ready` node with the **lowest current requested memory**, among nodes
   where `current load + new pod's request ≤ NODE_MEM_LIMIT_MB`. If no node has room, the
   pod is left `Pending` (skipped, logged, and revisited on the next watch event).
6. It binds the pod to the chosen node directly via the Kubernetes `Binding` subresource
   (the same primitive the default scheduler uses), which is why RBAC needs explicit
   `create` on `pods/binding`.

This is a greedy, stateless-per-event bin-packing strategy: it always pushes the next pod
onto whichever node is currently emptiest, which is what keeps both nodes' memory close to
balanced rather than stacking everything onto one node — directly addressing the task's
goal of minimizing the risk of any single node being pushed into memory pressure and having
the kubelet kill pods.

(One thing worth flagging from reading the code: `get_nodes_requested_memory()` re-lists
*all* pods in the namespace once per node inside the per-pod placement loop — the code
itself flags this as `# slow!!!`. It's a real inefficiency at scale, but not a correctness
bug, and the task asks to deploy/explain `scheduler.py` as given rather than rewrite it.)

## Expected placement of pod1, pod2, pod3

Given `NODE_MEM_LIMIT_MB=2048` and two nodes (call them by minikube's default node names,
`minikube` and `minikube-m02`), starting empty:

| Step | Pod | Request | Node loads before | Chosen node | Why |
|---|---|---|---|---|---|
| 1 | pod1 | 600Mi | minikube: 0, minikube-m02: 0 | `minikube` | Tied at 0 — first node returned by `available_nodes()` (API list order) wins |
| 2 | pod2 | 800Mi | minikube: 600Mi, minikube-m02: 0 | `minikube-m02` | `minikube-m02` is strictly less loaded |
| 3 | pod3 | 600Mi | minikube: 600Mi, minikube-m02: 800Mi | `minikube` | `minikube` (600Mi) is less loaded than `minikube-m02` (800Mi); 600+600=1200Mi still ≤ 2048Mi |

Final expected state: `minikube` holds `pod1` + `pod3` (1200Mi total), `minikube-m02` holds
`pod2` (800Mi total). Neither node approaches the 2048Mi ceiling, and the algorithm has
actively spread load across both nodes instead of greedily filling the first one — this is
the "load balancing" behavior the task is testing for. (The exact node names/order can
differ from this if the Kubernetes API happens to return nodes in a different order than
creation order; the *relative* reasoning — always fill the currently-emptiest eligible
node — holds regardless of which physical node ends up labeled first.)
