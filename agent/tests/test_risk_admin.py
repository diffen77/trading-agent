from src.risk_admin import build_parser


def test_risk_admin_halt_requires_operator_and_reason():
    args = build_parser().parse_args([
        "halt",
        "--reason",
        "Unexpected market-data incident",
        "--operator",
        "operator:diffen",
    ])

    assert args.command == "halt"
    assert args.reason == "Unexpected market-data incident"
    assert args.operator == "operator:diffen"


def test_risk_admin_daily_limit_is_explicit():
    args = build_parser().parse_args([
        "set-limit",
        "2.5",
        "--reason",
        "Approved lower paper-trading risk",
        "--operator",
        "operator:diffen",
    ])

    assert args.command == "set-limit"
    assert args.max_daily_loss_pct == 2.5
