from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_streamlit_systemd_service_uses_home_entrypoint():
    script = (ROOT / "deploy" / "scripts" / "after_install.sh").read_text()
    assert "streamlit run app/Home.py" in script
    assert "streamlit run app/main.py" not in script


def test_setup_guide_referenced_deploy_helpers_exist():
    assert (ROOT / "deploy" / "setup_ec2.sh").exists()
    assert (ROOT / "deploy" / "create_athena_tables.sh").exists()


def test_glue_delete_policy_is_limited_to_nonprod_patterns():
    policy = (ROOT / "deploy" / "iam_policy.json").read_text()
    delete_block = policy.split('"Sid": "GlueCatalogDropTable"', 1)[1].split('"Sid": "SNSPublish"', 1)[0]
    assert "table/*_preprod/*" in delete_block
    assert "table/*_dev/*" in delete_block
    assert "table/*_test/*" in delete_block
    assert '"arn:aws:glue:us-west-2:ACCOUNT_ID:table/*"' not in delete_block
