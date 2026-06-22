"""F(P10): shadow 池配额检查器 TDD 测试。

目标：防止 cron 同步无限产出 shadow 卡导致池膨胀。
配额规则（GPT-5.5 定稿）：
- 每个场景（scenario_tags[0]）shadow 卡上限 10 张
- 全局 shadow 卡总数上限 50 张
- 超限的场景列入 over_scenarios；全局超限则 can_add_more=False
- cron 同步前调用 check_quota，超限的场景/全局跳过新增
"""
from strategy_internalization.quota import check_quota, QuotaReport


def _shadow_card(cid, scenario, status="shadow"):
    """造一张最小 shadow 卡 yaml 文本。"""
    return (
        f"id: {cid}\n"
        f"title: test {cid}\n"
        f"scenario_tags:\n- {scenario}\n"
        f"trigger_keywords:\n- kw1\n"
        f"actions:\n- do something\n"
        f"priority: 5\n"
        f"status: {status}\n"
    )


def test_empty_dir_zero_quota(tmp_path):
    """空 shadow 目录：total=0，can_add_more=True。"""
    r = check_quota(tmp_path)
    assert r.total == 0
    assert r.can_add_more is True
    assert r.over_global is False


def test_counts_per_scenario(tmp_path):
    """3 张同场景 + 2 张另一场景：per_scenario 正确计数。"""
    (tmp_path / "a.yaml").write_text(_shadow_card("a", "system_design"))
    (tmp_path / "b.yaml").write_text(_shadow_card("b", "system_design"))
    (tmp_path / "c.yaml").write_text(_shadow_card("c", "system_design"))
    (tmp_path / "d.yaml").write_text(_shadow_card("d", "bug_fix"))
    (tmp_path / "e.yaml").write_text(_shadow_card("e", "bug_fix"))
    r = check_quota(tmp_path)
    assert r.per_scenario == {"system_design": 3, "bug_fix": 2}
    assert r.total == 5


def test_only_counts_shadow_status(tmp_path):
    """active 卡（晋升后残留在这）不计入 shadow 配额——只数 status=shadow。"""
    (tmp_path / "a.yaml").write_text(_shadow_card("a", "system_design", status="shadow"))
    (tmp_path / "b.yaml").write_text(_shadow_card("b", "system_design", status="active"))
    r = check_quota(tmp_path)
    assert r.per_scenario == {"system_design": 1}
    assert r.total == 1


def test_scenario_at_limit_blocks_add(tmp_path):
    """某场景达上限：can_add_to(该场景)=False，但全局未满 can_add_more 仍 True。"""
    for i in range(10):
        (tmp_path / f"c{i}.yaml").write_text(_shadow_card(f"c{i}", "system_design"))
    r = check_quota(tmp_path, per_scenario_limit=10)
    assert "system_design" in r.over_scenarios
    assert r.can_add_to("system_design") is False
    assert r.can_add_more is True


def test_scenario_below_limit_allows_add(tmp_path):
    """场景未达上限：can_add_to=True。"""
    (tmp_path / "a.yaml").write_text(_shadow_card("a", "system_design"))
    r = check_quota(tmp_path, per_scenario_limit=10)
    assert r.can_add_to("system_design") is True


def test_global_over_limit_blocks_all(tmp_path):
    """全局达 50：over_global=True，can_add_more=False，任何场景都不能加。"""
    for i in range(50):
        sc = "system_design" if i % 2 == 0 else "bug_fix"
        (tmp_path / f"c{i}.yaml").write_text(_shadow_card(f"c{i}", sc))
    r = check_quota(tmp_path, global_limit=50)
    assert r.over_global is True
    assert r.can_add_more is False
    assert r.can_add_to("bug_fix") is False


def test_scenario_with_no_tags_uses_unknown(tmp_path):
    """缺 scenario_tags 的卡归入 unknown 场景，不崩溃。"""
    (tmp_path / "a.yaml").write_text(
        "id: a\ntitle: a\nstatus: shadow\nactions: []\n"
    )
    r = check_quota(tmp_path)
    assert r.total == 1
    assert "unknown" in r.per_scenario


def test_non_yaml_files_ignored(tmp_path):
    """目录里非 .yaml 文件忽略不数。"""
    (tmp_path / "a.yaml").write_text(_shadow_card("a", "system_design"))
    (tmp_path / "notes.txt").write_text("noise")
    (tmp_path / ".DS_Store").write_text("x")
    r = check_quota(tmp_path)
    assert r.total == 1
