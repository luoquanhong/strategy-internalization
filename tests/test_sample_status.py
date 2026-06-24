"""retriever v2 — sample 卡支持测试。

sample 状态 = shadow 和 active 之间的试水状态。
- load_active_cards 也加载 sample 卡
- sample 卡跳过 holdout（需稳定曝光积累数据）
- sample 卡参与评分但不占 max_cards 名额
- 只有 score >= high_confidence_threshold(0.5) 时才注入
- 每次请求最多注入 1 张 sample 卡
- sample 卡注入后也写 exposure 记录
"""
import pytest, tempfile, os, yaml, json
import sys
sys.path.insert(0, '/root/strategy-internalization')
from strategy_internalization.retriever import load_active_cards, retrieve, compile_packet
from strategy_internalization.models import TaskSignals


def make_card_yaml(cards_dir, cid, title, status="active", scenario_tags=None, keywords=None, actions=None, priority=5):
    os.makedirs(cards_dir, exist_ok=True)
    data = {
        "id": cid, "title": title,
        "scenario_tags": scenario_tags or ["bug_fix"],
        "trigger_keywords": keywords or ["debug", "fix"],
        "actions": actions or ["action a", "action b"],
        "priority": priority,
        "status": status,
    }
    with open(f"{cards_dir}/{cid}.yaml", "w") as f:
        yaml.dump(data, f, allow_unicode=True)


class TestSampleStatus:
    """sample 卡支持测试"""

    def test_load_active_cards_includes_sample(self, tmp_path):
        """load_active_cards 加载 sample 状态的卡"""
        cards_dir = tmp_path / "cards"
        make_card_yaml(cards_dir, "card-a", "Active Card", status="active",
                       keywords=["debug"])
        make_card_yaml(cards_dir, "card-s", "Sample Card", status="sample",
                       keywords=["sample"])

        cards = load_active_cards(str(cards_dir))
        ids = {c.id for c in cards}
        assert "card-a" in ids, "active卡应该加载"
        assert "card-s" in ids, "sample卡应该被加载"
        assert len(cards) == 2

    def test_load_active_cards_excludes_shadow(self, tmp_path):
        """shadow 卡依然不加载"""
        cards_dir = tmp_path / "cards"
        make_card_yaml(cards_dir, "card-active", "Active", status="active")
        os.makedirs(tmp_path / "cards" / "shadow", exist_ok=True)
        make_card_yaml(tmp_path / "cards" / "shadow", "card-shadow", "Shadow",
                       status="shadow")

        cards = load_active_cards(str(cards_dir))
        ids = {c.id for c in cards}
        assert "card-active" in ids
        assert "card-shadow" not in ids, "shadow卡不应该被加载"

    def test_sample_card_injected_when_high_score(self, tmp_path):
        """sample 卡评分高时能被注入"""
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"

        # active 卡：一般匹配
        make_card_yaml(cards_dir, "card-a", "Active Card", status="active",
                       keywords=["task", "work"])
        # sample 卡：强匹配当前信号
        make_card_yaml(cards_dir, "card-s", "Sample Card", status="sample",
                       keywords=["debug", "fix", "error"],
                       priority=8)

        signals = TaskSignals(text="", scenario="bug_fix", keywords=["debug", "fix"])
        packet = retrieve(signals, cards_dir=str(cards_dir),
                          state_file=str(state_file),
                          request_id="test1",
                          high_confidence_threshold=0.5)

        injected_ids = {c.id for c in packet.cards}
        # sample卡关键词命中3个(debug/fix/error)，priority=8，
        # 基础分: 0.3(scenario命中) + 0(无tag命中) + 0.2*3(3个kw命中) + 0.2*0.8(priority/10*0.2)
        # = 0.3 + 0 + 0.6 + 0.16 = 1.06 capped at 1.0, 远>0.5 → 应该被注入
        assert "card-s" in injected_ids, \
            f"sample卡评分高应该被注入，实际注入: {injected_ids}"

    def test_sample_card_not_injected_when_low_score(self, tmp_path):
        """sample 卡评分低于阈值时不注入"""
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"

        make_card_yaml(cards_dir, "card-a", "Active Card", status="active",
                       keywords=["task", "work"],
                       priority=8)
        # sample 卡：跟当前任务完全不匹配
        make_card_yaml(cards_dir, "card-s", "Sample Card", status="sample",
                       keywords=["unrelated", "irrelevant"],
                       priority=1)

        signals = TaskSignals(text="", scenario="bug_fix", keywords=["debug", "fix"])
        packet = retrieve(signals, cards_dir=str(cards_dir),
                          state_file=str(state_file),
                          request_id="test2",
                          high_confidence_threshold=0.5)

        injected_ids = {c.id for c in packet.cards}
        assert "card-s" not in injected_ids, \
            f"低分sample卡不应该注入，实际注入: {injected_ids}"

    def test_at_most_one_sample_per_request(self, tmp_path):
        """每次请求最多注入1张sample卡"""
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"

        make_card_yaml(cards_dir, "card-a", "Active Card", status="active",
                       keywords=["task"])
        # 两张高分sample卡
        make_card_yaml(cards_dir, "card-s1", "Sample 1", status="sample",
                       keywords=["debug", "fix", "error"],
                       priority=8)
        make_card_yaml(cards_dir, "card-s2", "Sample 2", status="sample",
                       keywords=["debug", "fix"],
                       priority=9)

        signals = TaskSignals(text="", scenario="bug_fix", keywords=["debug", "fix"])
        packet = retrieve(signals, cards_dir=str(cards_dir),
                          state_file=str(state_file),
                          request_id="test3",
                          high_confidence_threshold=0.5)

        sample_count = sum(1 for c in packet.cards if c.status == "sample")
        assert sample_count <= 1, \
            f"最多1张sample卡，实际注入{sample_count}张"

    def test_sample_card_skips_holdout(self, tmp_path):
        """sample 卡跳过 holdout，总会被评分"""
        import sqlite3
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"
        exp_db = str(tmp_path / "experiment.db")
        # 初始化 experiment.db
        from strategy_internalization.experiment import init_db
        init_db(exp_db)

        make_card_yaml(cards_dir, "card-a", "Active Card", status="active",
                       keywords=["task"])
        make_card_yaml(cards_dir, "card-s", "Sample Card", status="sample",
                       keywords=["debug", "fix"],
                       priority=8)

        signals = TaskSignals(scenario="bug_fix", keywords=["debug", "fix"], text="")
        packet = retrieve(signals, cards_dir=str(cards_dir),
                          state_file=str(state_file),
                          request_id="test4",
                          high_confidence_threshold=0.5,
                          experiment_db=exp_db)

        injected_ids = {c.id for c in packet.cards}
        # sample卡评分高应该被注入，说明它跳过了 holdout
        assert "card-s" in injected_ids, \
            f"sample卡应跳过holdout被注入，实际: {injected_ids}"

    def test_sample_card_exposure_recorded(self, tmp_path):
        """sample 卡注入后也记录曝光"""
        import sqlite3
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"
        exp_db = str(tmp_path / "experiment.db")
        from strategy_internalization.experiment import init_db
        init_db(exp_db)

        make_card_yaml(cards_dir, "card-a", "Active Card", status="active",
                       keywords=["task"])
        make_card_yaml(cards_dir, "card-s", "Sample Card", status="sample",
                       keywords=["debug", "fix", "error"],
                       priority=8)

        signals = TaskSignals(text="", scenario="bug_fix", keywords=["debug", "fix"])
        packet = retrieve(signals, cards_dir=str(cards_dir),
                          state_file=str(state_file),
                          request_id="test5",
                          high_confidence_threshold=0.5,
                          experiment_db=exp_db)

        # 检查 experiment.db 里有 card-s 的曝光记录
        import sqlite3
        conn = sqlite3.connect(exp_db)
        rows = conn.execute(
            'SELECT card_id, held_out FROM exposure WHERE card_id = "card-s"'
        ).fetchall()
        conn.close()
        assert len(rows) > 0, f"sample卡注入后应有曝光记录，实际: {rows}"
        assert all(r[1] == 0 for r in rows), f"sample卡held_out应该=0，实际: {rows}"

    def test_backward_compat_no_sample_cards(self, tmp_path):
        """没有sample卡时行为不变"""
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"

        make_card_yaml(cards_dir, "card-a", "Active Card", status="active",
                       keywords=["debug", "fix"],
                       priority=8)

        signals = TaskSignals(text="", scenario="bug_fix", keywords=["debug", "fix"])
        packet = retrieve(signals, cards_dir=str(cards_dir),
                          state_file=str(state_file),
                          request_id="test6",
                          high_confidence_threshold=0.5)

        assert len(packet.cards) >= 1
        assert all(c.status != "sample" for c in packet.cards)
