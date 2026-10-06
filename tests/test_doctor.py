"""doctor.py pure checks — no DB, no network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import doctor  # noqa: E402


def test_parse_env_handles_quotes_and_comments():
    env = doctor.parse_env('# c\nA=1\nB="x, y"\nC=\'z\'\n\nD=\n')
    assert env == {"A": "1", "B": "x, y", "C": "z", "D": ""}


def test_env_checks_flags_missing_required_and_secrets():
    example = {k: "" for k in doctor.REQUIRED + ["EXTRA"]}
    env = {k: "v" for k in doctor.REQUIRED}
    env["LINKEDIN_PASSWORD"] = "hunter2"
    rows = doctor.env_checks(example, env)
    assert any(s == doctor.FAIL and n == "env secrets" for s, n, _ in rows)
    assert any(s == doctor.WARN and "EXTRA" in d for s, _, d in rows)
    assert not any("hunter2" in d for _, _, d in rows)  # values never printed
    rows = doctor.env_checks(example, {})
    assert sum(s == doctor.FAIL for s, _, _ in rows) == len(doctor.REQUIRED)


def test_profile_checks():
    assert doctor.profile_checks(None, {})[0][0] == doctor.FAIL
    rows = doctor.profile_checks("Name: [YOUR_NAME]", {})
    assert rows[0][0] == doctor.FAIL
    text = "github.com/me linkedin.com/in/me [PENDING x]"
    rows = doctor.profile_checks(text, {"GITHUB_URL": "https://github.com/me/",
                                        "LINKEDIN_URL": "https://linkedin.com/in/other"})
    names = [n for s, n, _ in rows if s == doctor.WARN]
    assert "profile vs env LINKEDIN_URL" in names and "profile vs env GITHUB_URL" not in names
