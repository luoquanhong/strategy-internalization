"""
P1 retriever 集成的 TDD 测试。
RED 阶段：定义期望行为，当前实现尚未满足。
"""
import pytest
import time
import os
import yaml

from strategy_internalization.retriever import retrieve, score_card, load_active_cards
from strategy_internalization.models import TaskSignals
from strategy_internalization import experiment

# ---------- shared helpers ----------
def _write_card(cards_dir, card_id, *, scenario_tags=None, trigger_keywords=None,
                actions=None, priority=5, status="active", promoted_at=None, source=None):
    os.makedirs(cards_dir, exist_ok=True)
    data = {
        "id": card_id,
        "title": f"Card {card_id}",
        "scenario_tags": scenario_tags or ["general"],
        "trigger_keywords": trigger_keywords or ["test"],
        "actions": actions or ["action1"],
        "priority": priority,
        "status": status,
        "source": source,
    }
    if promoted_at is not None:
        data["promoted_at"] = promoted_at
    with open(f"{cards_dir}/{card_id}.yaml", "w") as f:
        yaml.dump(data, f, default_flow_style=False)


def _signals(scenario, keywords):
    return TaskSignals(scenario=scenario, keywords=keywords, text=" ".join(keywords))


class TestP1RetrieverIntegration:

    # A -----------------------------------------------------------------
    def test_experiment_db_none_zero_regression(self, tmp_path):
        """experiment_db 不传 vs 显式 None：行为一致且无 DB 副作用。"""
        cards_dir = tmp_path / "cards"
        state_file1 = tmp_path / "state1.json"
        state_file2 = tmp_path / "state2.json"
        db_path = tmp_path / "experiment.db"

        _write_card(cards_dir, "c1", scenario_tags=["math"], trigger_keywords=["calc"], priority=8)
        signals = _signals("math", ["calc"])

        # 不传 experiment_db（默认 None）
        packet_no_arg = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file1), request_id="req-a"
        )
        # 显式传 experiment_db=None
        packet_none = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file2),
            request_id="req-b", experiment_db=None
        )

        assert packet_no_arg.cards_ids == packet_none.cards_ids
        assert packet_no_arg.text == packet_none.text
        # 零副作用：DB 文件不应被创建
        assert not db_path.exists()

    # B -----------------------------------------------------------------
    def test_mature_active_card_injection_and_exposure(self, tmp_path):
        """成熟 active 卡（promoted_at > 7天前）正常注入并记录曝光。"""
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"
        db_path = tmp_path / "experiment.db"

        experiment.init_db(str(db_path))
        now = time.time()
        thirty_days_ago = now - 30 * 86400
        _write_card(cards_dir, "active1", status="active", promoted_at=thirty_days_ago)
        signals = _signals("general", ["test"])
        fixed_rng = lambda: 0.9

        # 首次检索应注入该卡，并记录一次 held_out=0 的曝光
        packet1 = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file),
            request_id="req1", experiment_db=str(db_path), _rng=fixed_rng,
        )
        assert "active1" in packet1.cards_ids

        exposures = experiment.recent_exposures_with_outcome(str(db_path), "active1")
        assert len(exposures) == 1
        assert exposures[0]["held_out"] == 0

        # 相同 request_id 再次检索，命中缓存；曝光数不变
        packet2 = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file),
            request_id="req1", experiment_db=str(db_path), _rng=fixed_rng,
        )
        assert packet2.cards_ids == packet1.cards_ids
        exposures2 = experiment.recent_exposures_with_outcome(str(db_path), "active1")
        assert len(exposures2) == 1

    # C -----------------------------------------------------------------
    def test_watch_card_single_mode_top_watch_injected(self, tmp_path):
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"
        db_path = tmp_path / "experiment.db"

        experiment.init_db(str(db_path))
        _write_card(cards_dir, "watch1", status="watch", trigger_keywords=["watchkw"],
                     scenario_tags=["general"], priority=10)
        _write_card(cards_dir, "active1", status="active", priority=5)
        signals = _signals("general", ["watchkw"])

        packet = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file),
            request_id="req1", experiment_db=str(db_path), _rng=lambda: 0.9,
            high_confidence_threshold=0.5, _now=time.time(),
        )
        # 只注入 watch 卡，不叠加 active
        assert packet.cards_ids == ["watch1"]

    def test_watch_card_below_threshold_not_injected(self, tmp_path):
        """低分 watch 卡不注入——先独立验证分数确实 < threshold。"""
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"
        db_path = tmp_path / "experiment.db"

        experiment.init_db(str(db_path))
        # 低分 watch 卡：scenario 匹配(0.30) + priority(0.02) = 0.32，> degrade 但 < high_confidence
        _write_card(cards_dir, "watch_low", status="watch", priority=1,
                    trigger_keywords=["unmatched_kw"])
        signals = _signals("general", ["rare"])

        # 独立验证分数确实低于 high_confidence_threshold
        cards = load_active_cards(str(cards_dir))
        watch_low = next(c for c in cards if c.id == "watch_low")
        low_score = score_card(watch_low, signals)
        assert low_score < 0.5  # 低于 high_confidence_threshold

        packet = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file),
            request_id="req1", experiment_db=str(db_path), _rng=lambda: 0.9,
            high_confidence_threshold=0.5,
        )
        # 分数不足 high_confidence_threshold 不应注入
        assert "watch_low" not in packet.cards_ids

    # D -----------------------------------------------------------------
    def test_holdout_divert_and_normal_injection(self, tmp_path):
        cards_dir = tmp_path / "cards"
        state_file_held = tmp_path / "state_held.json"
        state_file_normal = tmp_path / "state_normal.json"
        db_path = tmp_path / "experiment.db"

        experiment.init_db(str(db_path))
        now = time.time()
        two_days_ago = now - 2 * 86400
        _write_card(cards_dir, "new_active", status="active", promoted_at=two_days_ago)
        signals = _signals("general", ["test"])

        # rng=0.1 -> holdout 生效
        packet_held = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file_held),
            request_id="req-held", experiment_db=str(db_path),
            _rng=lambda: 0.1, _now=now,
        )
        assert "new_active" not in packet_held.cards_ids
        exposures_held = experiment.recent_exposures_with_outcome(
            str(db_path), "new_active", include_held_out=True
        )
        assert any(e["held_out"] == 1 for e in exposures_held)

        # rng=0.9 -> 正常注入
        packet_normal = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file_normal),
            request_id="req-normal", experiment_db=str(db_path),
            _rng=lambda: 0.9, _now=now,
        )
        assert "new_active" in packet_normal.cards_ids
        exposures_normal = experiment.recent_exposures_with_outcome(
            str(db_path), "new_active", include_held_out=True
        )
        assert any(e["held_out"] == 0 for e in exposures_normal)

    # E -----------------------------------------------------------------
    def test_mature_active_not_held_out(self, tmp_path):
        """成熟 active 卡（promoted_at > 7天）即使 rng < 0.15 也不应被 holdout。"""
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"
        db_path = tmp_path / "experiment.db"

        experiment.init_db(str(db_path))
        now = time.time()
        thirty_days_ago = now - 30 * 86400
        _write_card(cards_dir, "mature1", status="active", promoted_at=thirty_days_ago,
                    priority=8, trigger_keywords=["test"])
        signals = _signals("general", ["test"])

        # rng=0.1 < 0.15，但 mature 卡不应被 holdout
        packet = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file),
            request_id="req1", experiment_db=str(db_path),
            _rng=lambda: 0.1, _now=now,
        )
        assert "mature1" in packet.cards_ids
        exposures = experiment.recent_exposures_with_outcome(
            str(db_path), "mature1", include_held_out=True
        )
        assert all(e["held_out"] == 0 for e in exposures)

    def test_penalty_ranking_change(self, tmp_path):
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"
        db_path = tmp_path / "experiment.db"

        experiment.init_db(str(db_path))
        # cardA 天然高分，但被降权
        _write_card(cards_dir, "cardA", status="active", scenario_tags=["math"],
                     trigger_keywords=["calc"], priority=10)
        # cardB 低分无负面
        _write_card(cards_dir, "cardB", status="active", scenario_tags=["math"],
                     trigger_keywords=["calc"], priority=5)

        # 构造 cardA 负面历史：5 次曝光，3 次 retry → penalty≈0.5
        for i in range(5):
            rid = f"expA_{i}"
            experiment.record_exposure(str(db_path), request_id=rid, card_id="cardA")
            if i < 3:
                experiment.record_outcome(str(db_path), request_id=rid, outcome="retry")
            else:
                experiment.record_outcome(str(db_path), request_id=rid, outcome="success")

        # 独立验证 penalty 值
        penalty_a = experiment.compute_card_penalty(str(db_path), "cardA")
        penalty_b = experiment.compute_card_penalty(str(db_path), "cardB")
        assert penalty_a == 0.5  # 3/5=0.6 >= 0.4 且 >= 5 条 → 降权
        assert penalty_b == 1.0  # 无曝光历史 → 不降权

        signals = _signals("math", ["calc"])
        packet = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file),
            request_id="req1", experiment_db=str(db_path), _rng=lambda: 0.9,
            _now=time.time(), max_cards=2, degrade_threshold=0.3,
        )

        # 降权后 cardB 必须排在 cardA 之前
        assert "cardB" in packet.cards_ids
        assert packet.cards_ids[0] == "cardB"

    # F -----------------------------------------------------------------
    def test_holdout_empty_packet(self, tmp_path):
        cards_dir = tmp_path / "cards"
        state_file = tmp_path / "state.json"
        db_path = tmp_path / "experiment.db"

        experiment.init_db(str(db_path))
        _write_card(cards_dir, "watch1", status="watch", trigger_keywords=["test"])
        signals = _signals("general", ["test"])

        packet = retrieve(
            signals, cards_dir=str(cards_dir), state_file=str(state_file),
            request_id="req1", experiment_db=str(db_path), _rng=lambda: 0.1,
        )
        assert packet.cards_ids == []
        assert "strategy_reference" not in packet.text

    # G -----------------------------------------------------------------
    def test_score_card_penalty_parameter(self, tmp_path):
        cards_dir = tmp_path / "cards"
        _write_card(cards_dir, "card1", scenario_tags=["math"], trigger_keywords=["calc"], priority=8)
        cards = load_active_cards(str(cards_dir))
        card = cards[0]
        signals = _signals("math", ["calc"])

        score_full = score_card(card, signals)            # penalty 默认 1.0
        score_half = score_card(card, signals, penalty=0.5)

        # 乘 0.5 后不超过原值（注意上限 1.0）
        assert score_half <= score_full
        assert score_half == pytest.approx(score_full * 0.5, rel=0.05)

    # H -----------------------------------------------------------------
    def test_load_active_cards_loads_watch_and_promoted_at(self, tmp_path):
        cards_dir = tmp_path / "cards"
        now = time.time()
        _write_card(cards_dir, "active1", status="active")
        _write_card(cards_dir, "watch1", status="watch", promoted_at=now - 1000)
        _write_card(cards_dir, "draft1", status="draft")

        cards = load_active_cards(str(cards_dir))
        ids = {c.id for c in cards}
        assert ids == {"active1", "watch1"}

        watch_card = next(c for c in cards if c.id == "watch1")
        assert watch_card.promoted_at is not None
