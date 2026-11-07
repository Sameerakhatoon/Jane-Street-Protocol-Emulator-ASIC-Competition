# SPDX-License-Identifier: Apache-2.0
"""Hygiene rules on crafted text, a scratch repository and commit messages.

Bad inputs are assembled at run time so this file passes the check itself.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import check as H  # noqa: E402

EM, EN = chr(0x2014), chr(0x2013)
SMILE = chr(0x1F600)


def rules(path, text):
    return [f[2] for f in H.check_text(path, text)]


# 1: outside-document references
@pytest.mark.parametrize("text", [
    "# see " + "C" + ".6", "x = 1  # " + "D" + "12 says so", "TSE-" + "E3-01", "TSE-" + "M2",
    "plan" + "/04", "refs" + "-md-deep", "old proto" + "types", "read " + "notes." + "md",
])
def test_01_refs(text):
    assert rules("a.py", text) == ["ref"]


# 2: things that look similar but are not references
@pytest.mark.parametrize("text", [
    "version 1.2", "x = a.b0", "0xD12", "DD12 = 3", "D_1 = 2", "obj.C.6x", "my_plan" "/x",
    "prototype only", "README." + "md", "docs/info." + "md", "E3 = 1", "rev v4.0",
])
def test_02_not_refs(text):
    assert rules("a.py", text) == []


# 3: characters
def test_03_characters():
    assert rules("a.sv", "// a " + EM + " b") == ["dash"]
    assert rules("a.sv", "// a " + EN + " b") == ["dash"]
    assert rules("a.py", "# ok " + SMILE) == ["emoji"]
    assert rules("a.py", "s = 'caf" + chr(0xE9) + "'") == ["ascii"]


# 4: a free-standing double hyphen counts in comments only
@pytest.mark.parametrize("path,text,bad", [
    ("a.py", "x = 1  # wait -- then go", True),
    ("a.py", "# run with --lint-only", False),
    ("a.py", "# ---- section ----", False),
    ("a.py", "cmd = 'a -- b'  # fine", False),
    ("a.py", "print('#  -- not a comment')", False),
    ("a.sh", "git diff --exit-code -- $FILES", False),
    ("a.sh", "echo $# -- x", False),
    ("a.sh", "# end of options: use -- here", True),
    ("Makefile", "\tgit diff -- $(X)  # keep -- it", True),
    ("a.sv", "assign a = b; // one -- two", True),
    ("a.sv", "x = y--; // fine", False),
    ("a.sv", "/* a\n   b -- c */", True),
    ("a.c", 'puts("// -- inside a string");', False),
    ("a.tse", "// trailing --", True),
    ("a.bin", "# x -- y", False),
])
def test_04_double_hyphen(path, text, bad):
    assert ("dhyphen" in rules(path, text)) == bad


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ("README." "md")).write_text("# Template " + EM + " readme\n")
    (tmp_path / "info.yaml").write_text("title: x  # " + EM + " template\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "import template")
    return tmp_path


# 5: unmodified template files and template lines are exempt; new lines are not
def test_05_tree(repo):
    assert H.check_tree(str(repo)) == []
    (repo / "info.yaml").write_text("title: x  # " + EM + " template\nauthor: y  # " + EM + "\n")
    (repo / ("notes." + "md")).write_text("hello\n")
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", ".")
    got = sorted((p, n, r) for p, n, r, _ in H.check_tree(str(repo)))
    assert got == [("info.yaml", 2, "dash"), ("notes." + "md", 0, "markdown")]


# 6: commit messages in a range
def test_06_commits(repo):
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "tools: fine\n\nbody " + "D" + "7 -- and " + EN)
    got = sorted(r for _, _, r, _ in H.check_commits(str(repo), "HEAD~1..HEAD"))
    assert got == ["dash", "dhyphen", "ref"]
    assert H.check_commits(str(repo), "HEAD~1") == []
    assert H.main(["-C", str(repo), "--commits", "HEAD~1..HEAD"]) == 1
    assert H.main(["-C", str(repo)]) == 0
