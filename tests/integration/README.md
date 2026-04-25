# Integration tests — run in CodeBuild against dev AWS environment only.
# Mark all tests with @pytest.mark.integration
# These are skipped in GitHub Actions (no AWS credentials).
# Run with: pytest -m integration
