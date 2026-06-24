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

## Architecture notes: what changes for production

`scheduler.py` is correct for the demo (single instance, one slow watch loop, low pod
volume) but makes a handful of simplifications that a senior reviewer would expect called
out explicitly rather than discovered later in an incident. Deployed as-is, single replica,
at low scale — this is fine. Below is what breaks first as you scale it up or run it for
real, and the fix for each.

| # | Current behavior | Why it breaks at scale / in production | Production fix |
|---|---|---|---|
| 1 | **List-then-bind race.** `load_balancing_assignment` reads cluster state (`get_nodes_requested_memory`), decides a node, *then* binds — two separate, non-atomic API calls with no lock between them. | Fine with exactly one scheduler replica processing one event at a time (true today). Scale to >1 replica for HA, or add any other writer of pod-to-node state, and two decisions can both see the same "node has room" snapshot and both bind, over-committing the node — the exact `OOMKilled`/eviction risk this scheduler exists to prevent. | Either keep strictly one active replica (see leader election below), or move to optimistic concurrency: re-check the target node's resourceVersion/load immediately before bind and retry on conflict, the way the real `kube-scheduler` does with its scheduling cache + assume cache. |
| 2 | **No leader election.** The Deployment runs `replicas: 1` with no election protocol; if you bumped replicas for availability, every replica would independently watch and try to bind the same pods. | A second replica isn't just redundant capacity, it actively causes the race in row 1. | Use `client-go`'s `leaderelection` package (the same mechanism `kube-scheduler`, `kube-controller-manager` use) so only one replica is active; others stand by and take over on failover. |
| 3 | **Full re-list per node, per pod (`# slow!!!` in the code).** `get_nodes_requested_memory()` calls `list_namespaced_pod` fresh for every node inside the per-pod placement loop — O(pods × nodes) API calls per scheduling decision, hammering the API server. | At 3 pods / 2 nodes this is free. At hundreds of pods and a handful of nodes, this is the kind of thing that quietly takes down an API server under load — a classic "worked in the demo" trap. | Use a `SharedInformer` with a local indexed cache (pod → node, aggregated to per-node memory) updated incrementally on Add/Update/Delete events, instead of re-listing. This is also what makes watch resilience (row 4) come for free. |
| 4 | **Bare `watch.Watch()` with no resync/reconnect handling.** If the watch stream drops (API server restart, network blip, watch timeout), the `for event in w.stream(...)` loop simply ends and `main()` returns — the container exits and Kubernetes restarts it, but any pod events that arrived during the gap and aren't still `Pending` are missed silently. | Watches *will* disconnect periodically in any long-lived cluster; this isn't a corner case. | Use a `SharedInformer`, which handles reconnect + periodic full resync automatically, instead of a raw watch loop. |
| 5 | **Failed binds are dropped, not retried.** `bind_pod_to_node` failures are caught and logged (`except client.ApiException`) but never requeued — a transient conflict leaves the pod `Pending` forever unless an unrelated watch event happens to trigger another look. | Production schedulers need a workqueue-with-backoff pattern (`client-go`'s `workqueue.RateLimitingInterface`) so failures get retried a bounded number of times instead of silently stalling. | Push failed pod names onto a rate-limited requeue instead of relying on incidental re-triggers. |
| 6 | **No scheduling events recorded.** The default scheduler emits `Scheduled`/`FailedScheduling` Kubernetes Events so `kubectl describe pod` explains *why* a pod is Pending. This scheduler only logs to stdout. | Without events, the only way to debug a stuck pod is to find and read this scheduler's logs — fine for an interview demo, a real operability gap at 3am. | Use the `EventRecorder` from `client-go` to emit the same Event objects, so this integrates with normal `kubectl describe` / dashboards / alerting. |
| 7 | **Single global `NODE_MEM_LIMIT_MB` for all nodes.** One constant is applied uniformly; there's no support for heterogeneous node sizes. | Real clusters mix instance types; a fixed ceiling either wastes capacity on big nodes or still risks pressure on small ones. | Source actual capacity per node (a label/annotation set at provisioning time, or a more trustworthy capacity signal than `allocatable` if the driver issue applies) and compare against each node's own ceiling rather than one shared constant. |
| 8 | **Bespoke binary instead of a scheduler framework plugin.** This is a standalone process duplicating just the "bind" half of scheduling, with none of the default scheduler's filtering (taints/tolerations, affinity, resource quotas, `PodDisruptionBudget` awareness, etc.) — it's all-or-nothing instead of additive. | Fine to prove out the load-balancing idea in isolation, as this task asks. Risky to actually run instead of the default scheduler in a cluster that also relies on those other features. | Implement this as a `Score` plugin in the [Kubernetes Scheduling Framework](https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/) extending `kube-scheduler` (e.g. similar to the built-in `NodeResourcesFit`/`NodeResourcesBalancedAllocation` plugins), so it composes with everything else instead of replacing it. |

None of this changes the verdict for *this* task — `scheduler.py` does what it's described
to do, deployed as specified, and the placement trace above is the expected, correct
behavior of the code as written. The table above is what I'd raise in a design review before
trusting this pattern with real production traffic.
