
import razorpay_agent.server as server_module
from razorpay_agent.decision import LinUCBPolicy


def _arms():
    from razorpay_agent.decision import BundleArm, DiscountArm

    return (
        DiscountArm("d5", 5.0),
        DiscountArm("d10", 10.0),
        DiscountArm("d15", 15.0),
        DiscountArm("d20", 20.0),
        BundleArm("b_sku-socks", "sku-socks", 499.0),
        BundleArm("b_sku-mug", "sku-mug", 599.0),
    )


class TestBuildPolicy:
    def test_missing_file_starts_cold_loudly(self, tmp_path, capsys):
        policy, is_warm = server_module.build_policy(tmp_path / "nope.json")
        assert is_warm is False
        assert "COLD" in capsys.readouterr().err

    def test_existing_file_starts_warm(self, tmp_path, capsys):
        from razorpay_agent.decision import ContextEncoder

        LinUCBPolicy(_arms(), ContextEncoder(("apparel", "home", "kitchen", "personal_care", "stationery")), alpha=0.5).save(
            tmp_path / "bandit.json"
        )
        policy, is_warm = server_module.build_policy(tmp_path / "bandit.json")
        assert is_warm is True
        assert isinstance(policy, LinUCBPolicy)
        assert "warm-starting" in capsys.readouterr().out

    def test_category_mismatch_falls_back_cold(self, tmp_path, capsys):
        from razorpay_agent.decision import ContextEncoder

        LinUCBPolicy(
            _arms(), ContextEncoder(("grocery",)), alpha=0.5
        ).save(tmp_path / "bandit.json")
        policy, is_warm = server_module.build_policy(tmp_path / "bandit.json")
        assert is_warm is False
        assert "do not match" in capsys.readouterr().err


class TestWarmAppAssembly:
    def test_build_live_app_reports_warm_flag(self, tmp_path, monkeypatch):
        from razorpay_agent.decision import ContextEncoder

        monkeypatch.setattr(server_module, "load_env_file", lambda path=None: {})
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        pretrained = tmp_path / "bandit.json"
        LinUCBPolicy(_arms(), ContextEncoder(("apparel", "home", "kitchen", "personal_care", "stationery")), alpha=0.5).save(pretrained)

        _, _, is_live = server_module.build_live_app(
            tmp_path / "live.sqlite3", pretrained_bandit_path=pretrained
        )
        assert is_live is False
