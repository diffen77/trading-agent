from src.candidate_policy_admin import build_parser


def test_candidate_policy_admin_has_separate_review_and_activation_steps():
    parser = build_parser()

    approve = parser.parse_args([
        "approve",
        "xsto-challenger-7",
        "--reviewed-by",
        "operator:test",
    ])
    activate = parser.parse_args([
        "activate",
        "xsto-challenger-7",
        "--activated-by",
        "operator:test",
    ])

    assert approve.command == "approve"
    assert activate.command == "activate"
    assert approve.version == activate.version
