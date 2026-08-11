from src.core.notifier import TelegramNotifier


def test_notifier_reads_credentials_from_runtime_secret_files(
    monkeypatch,
    tmp_path,
):
    token_file = tmp_path / "telegram-token"
    chat_file = tmp_path / "telegram-chat"
    token_file.write_text("synthetic-file-token\n")
    chat_file.write_text("synthetic-file-chat\n")
    token_file.chmod(0o600)
    chat_file.chmod(0o600)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("TELEGRAM_CHAT_ID_FILE", str(chat_file))

    notifier = TelegramNotifier()

    assert notifier.enabled
    assert notifier.bot_token == "synthetic-file-token"
    assert notifier.chat_id == "synthetic-file-chat"


def test_operational_alert_notification_escapes_untrusted_text():
    notifier = TelegramNotifier.__new__(TelegramNotifier)
    sent = []
    notifier._send = lambda text, **_values: sent.append(text) or True

    delivered = notifier.notify_operational_alert(
        {
            "code": "MARKET_DATA_NOT_READY",
            "severity": "PAGE",
            "summary": "Feed <b>unsafe</b>",
            "runbook": "docs/operations.md",
        }
    )

    assert delivered
    assert "MARKET_DATA_NOT_READY" in sent[0]
    assert "Feed &lt;b&gt;unsafe&lt;/b&gt;" in sent[0]
    assert "docs/operations.md" in sent[0]


def test_telegram_transport_never_logs_token_or_provider_body(
    monkeypatch,
    caplog,
):
    notifier = TelegramNotifier.__new__(TelegramNotifier)
    notifier.enabled = True
    notifier.bot_token = "synthetic-secret-token"
    notifier.chat_id = "synthetic-chat"

    def fail_with_url(*_args, **_kwargs):
        raise RuntimeError(
            "https://api.telegram.org/botsynthetic-secret-token/sendMessage"
        )

    monkeypatch.setattr("src.core.notifier.requests.post", fail_with_url)
    assert not notifier._send("bounded alert")
    assert "synthetic-secret-token" not in caplog.text

    class Response:
        status_code = 500
        text = "synthetic-provider-secret-body"

    monkeypatch.setattr(
        "src.core.notifier.requests.post",
        lambda *_args, **_kwargs: Response(),
    )
    assert not notifier._send("bounded alert")
    assert "synthetic-provider-secret-body" not in caplog.text
