"""Job Discovery Agent: children get root/parent/depth set (P5)."""


def test_discovered_children_get_tree_fields(coordinator):
    co = coordinator
    # Parent submitted via the simulator.
    parent_id, _ = co.submit_job("wrapper", "normal", "tcsh wrapper.csh")
    parent = co.db.get_job(parent_id)
    # Force a child into the same submit window via a direct sim submit.
    child_id = co.lsf.submit("child", "normal", "dc_shell -f a.tcl", user="user")
    res = co.discovery.discover(parent)
    registered = res["result"]
    if child_id in registered:
        child = co.db.get_job(child_id)
        assert child["parent_job_id"] == parent_id
        assert child["root_job_id"] == parent_id
        assert child["job_depth"] == 1
        assert child["is_discovered"] == 1
