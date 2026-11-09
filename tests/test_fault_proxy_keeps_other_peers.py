"""F131: a proxied fault replaces only the chosen upstream's access point, never the whole peer list.

The docker routing rewrote the target's local roots to the proxy alone, so node1 lost node2: after
it dropped the corrupting node3 connection it had no honest peer, and the honest nodes split
(live eb17602e: node1 slot 896, node2 slot 931, node3 slot 900, frozen for 10 s).
"""
from scripts import runtime_chainsync_blockfetch_fault as fault

TEMPLATE = {"localRoots": [{"accessPoints": [{"address": "node2", "port": 3001}, {"address": "node3", "port": 3001}],
                            "trustable": True, "hotValency": 2, "warmValency": 2}]}
PROXY = {"address": "10.9.0.99", "port": 40000}


def test_only_the_upstream_is_swapped_for_the_proxy():
    points = fault.access_points_with_proxy(TEMPLATE, selected_upstream="node3:3001", proxy_point=PROXY)
    assert points == [{"address": "node2", "port": 3001}, PROXY]


def test_an_upstream_not_in_the_topology_adds_the_proxy_and_keeps_the_rest():
    points = fault.access_points_with_proxy(TEMPLATE, selected_upstream="node9:3001", proxy_point=PROXY)
    assert points == [{"address": "node2", "port": 3001}, {"address": "node3", "port": 3001}, PROXY]


def test_container_peer_list_keeps_the_other_peers():
    assert fault.peer_addresses_with_proxy(["node2:3001", "node3:3001"], selected_upstream="node3:3001",
                                           replacement="10.9.0.99:40000") == ["node2:3001", "10.9.0.99:40000"]
