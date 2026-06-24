import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with patch("kubernetes.config.load_incluster_config"), patch("kubernetes.client.CoreV1Api"):
    import scheduler


def make_node(name, ready=True):
    node = MagicMock()
    node.metadata.name = name
    condition = MagicMock()
    condition.type = "Ready"
    condition.status = "True" if ready else "False"
    node.status.conditions = [condition]
    return node


def make_pod(name, node_name, memory_request=None):
    pod = MagicMock()
    pod.metadata.name = name
    pod.spec.node_name = node_name
    container = MagicMock()
    if memory_request is None:
        container.resources = None
    else:
        container.resources.requests = {"memory": memory_request}
    pod.spec.containers = [container]
    return pod


class TestParseMemoryQuantityToBytes:
    def test_mi(self):
        assert scheduler.parse_memory_quantity_to_bytes("600Mi") == 600 * 1024**2

    def test_gi(self):
        assert scheduler.parse_memory_quantity_to_bytes("2Gi") == 2 * 1024**3

    def test_ki(self):
        assert scheduler.parse_memory_quantity_to_bytes("512Ki") == 512 * 1024

    def test_plain_bytes_no_suffix(self):
        assert scheduler.parse_memory_quantity_to_bytes("1024") == 1024.0


class TestPodMemoryRequest:
    def test_single_container(self):
        pod = make_pod("p", None, memory_request="600Mi")
        assert scheduler.pod_memory_request(pod) == 600 * 1024**2

    def test_multiple_containers_are_summed(self):
        pod = make_pod("p", None)
        c1, c2 = MagicMock(), MagicMock()
        c1.resources.requests = {"memory": "100Mi"}
        c2.resources.requests = {"memory": "200Mi"}
        pod.spec.containers = [c1, c2]
        assert scheduler.pod_memory_request(pod) == 300 * 1024**2

    def test_missing_resources_defaults_to_zero(self):
        pod = make_pod("p", None)
        assert scheduler.pod_memory_request(pod) == 0.0


class TestAvailableNodes:
    def test_filters_out_not_ready_nodes(self, monkeypatch):
        ready = make_node("ready-node")
        not_ready = make_node("not-ready-node", ready=False)
        monkeypatch.setattr(
            scheduler.v1, "list_node", lambda: MagicMock(items=[ready, not_ready])
        )
        result = scheduler.available_nodes()
        assert [n.metadata.name for n in result] == ["ready-node"]


class TestLoadBalancingAssignment:
    def setup_method(self):
        os.environ["NODE_MEM_LIMIT_MB"] = "2048"

    def test_picks_least_loaded_node(self, monkeypatch):
        node_a, node_b = make_node("a"), make_node("b")
        bound = [make_pod("existing", "a", memory_request="1000Mi")]
        monkeypatch.setattr(
            scheduler.v1, "list_namespaced_pod", lambda ns: MagicMock(items=bound)
        )
        pod = make_pod("new", None, memory_request="100Mi")
        chosen = scheduler.load_balancing_assignment(pod, [node_a, node_b])
        assert chosen.metadata.name == "b"

    def test_skips_node_without_room(self, monkeypatch):
        node_a = make_node("a")
        bound = [make_pod("existing", "a", memory_request="2000Mi")]
        monkeypatch.setattr(
            scheduler.v1, "list_namespaced_pod", lambda ns: MagicMock(items=bound)
        )
        pod = make_pod("new", None, memory_request="500Mi")
        assert scheduler.load_balancing_assignment(pod, [node_a]) is None

    def test_pod1_pod2_pod3_placement_matches_readme_trace(self, monkeypatch):
        """Replays the exact pod1/pod2/pod3 scenario from the task and asserts
        the placement matches the hand-traced table in README.md."""
        node_a, node_b = make_node("minikube"), make_node("minikube-m02")
        nodes = [node_a, node_b]
        bound_pods = []

        monkeypatch.setattr(
            scheduler.v1,
            "list_namespaced_pod",
            lambda ns: MagicMock(items=bound_pods),
        )

        pod1 = make_pod("pod1", None, memory_request="600Mi")
        chosen = scheduler.load_balancing_assignment(pod1, nodes)
        assert chosen.metadata.name == "minikube"
        bound_pods.append(make_pod("pod1", "minikube", memory_request="600Mi"))

        pod2 = make_pod("pod2", None, memory_request="800Mi")
        chosen = scheduler.load_balancing_assignment(pod2, nodes)
        assert chosen.metadata.name == "minikube-m02"
        bound_pods.append(make_pod("pod2", "minikube-m02", memory_request="800Mi"))

        pod3 = make_pod("pod3", None, memory_request="600Mi")
        chosen = scheduler.load_balancing_assignment(pod3, nodes)
        assert chosen.metadata.name == "minikube"
        bound_pods.append(make_pod("pod3", "minikube", memory_request="600Mi"))

        final_load = scheduler.get_nodes_requested_memory()
        assert final_load["minikube"] == 1200 * 1024**2
        assert final_load["minikube-m02"] == 800 * 1024**2
