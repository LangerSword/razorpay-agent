from pathlib import Path

from razorpay_agent.checkout.payments import RazorpayTestProvider, ScriptedPaymentProvider
from razorpay_agent.server import build_live_app, build_payment_provider, load_env_file


class TestLoadEnvFile:
    def test_parses_simple_pairs(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "RAZORPAY_KEY_ID=rzp_test_123\n"
            "RAZORPAY_KEY_SECRET='abc def'\n"
            '# a comment\n'
            "\n"
            "BAD LINE WITHOUT EQUALS\n"
            'EMPTY=\n'
        )
        values = load_env_file(env_file)
        assert values == {
            "RAZORPAY_KEY_ID": "rzp_test_123",
            "RAZORPAY_KEY_SECRET": "abc def",
            "EMPTY": "",
        }

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_env_file(tmp_path / "nope.env") == {}


class TestProviderSelection:
    def test_both_credentials_select_live_razorpay(self, monkeypatch):
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        provider, is_live = build_payment_provider(
            {"RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": "s3cr3t"}
        )
        assert is_live is True
        assert isinstance(provider, RazorpayTestProvider)

    def test_missing_secret_falls_back_to_scripted(self, capsys):
        provider, is_live = build_payment_provider({"RAZORPAY_KEY_ID": "rzp_test_x"})
        assert is_live is False
        assert isinstance(provider, ScriptedPaymentProvider)
        assert "SCRIPTED" in capsys.readouterr().err

    def test_no_credentials_falls_back_to_scripted(self):
        provider, is_live = build_payment_provider({})
        assert is_live is False
        assert isinstance(provider, ScriptedPaymentProvider)

    def test_environment_used_when_file_lacks_key(self, monkeypatch):
        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_env")
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", "env-secret")
        provider, is_live = build_payment_provider(file_values={})
        assert is_live is True
        assert isinstance(provider, RazorpayTestProvider)

    def test_blank_secret_does_not_go_live(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("RAZORPAY_KEY_ID=rzp_test_x\nRAZORPAY_KEY_SECRET=\n")
        provider, is_live = build_payment_provider(load_env_file(env_file))
        assert is_live is False


class TestLiveAppAssembly:
    def test_build_live_app_wires_everything(self, tmp_path, monkeypatch):
        import razorpay_agent.server as server_module

        monkeypatch.setattr(server_module, "load_env_file", lambda path=None: {})
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        db = tmp_path / "live.sqlite3"
        app, audit_store, is_live = build_live_app(db)
        from fastapi.testclient import TestClient

        response = TestClient(app).get("/products")
        assert response.status_code == 200
        assert audit_store.count() == 0
        assert is_live is False
        assert Path(db).exists()
