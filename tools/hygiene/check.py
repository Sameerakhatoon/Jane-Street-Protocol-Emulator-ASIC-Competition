# SPDX-License-Identifier: Apache-2.0
"""Repository hygiene check.

    python3 tools/hygiene/check.py             check the files
    python3 tools/hygiene/check.py --commits R check the messages of git range R

A file check covers every tracked or untracked (not ignored) file except the
Tiny Tapeout template files the import commit brought in and nobody changed
since. In a changed template file only the lines the template did not have
are checked. Rules:

    ref      references to an outside design document: section IDs (a letter
             A to J, a dot, a number), decision IDs (D and one or two digits),
             backlog IDs, planning folder paths, markdown file names other
             than the template's README and info pages and the project's own
             documents (PROJECT_MD)
    ascii    any byte above 0x7F (em and en dashes and emojis get their own rule)
    dash     an em or en dash
    emoji    an emoji or pictograph
    dhyphen  a free-standing double hyphen in a comment (flags like --lint-only
             and code such as "git diff -- path" are fine)
    markdown a markdown file the template did not have, other than PROJECT_MD

Exit status 0 when clean, 1 with one "path:line: rule: text" per finding.
"""
import argparse
import os
import re
import subprocess
import sys

REF_RES = [
    re.compile(r"(?<![\w.])[A-J]\.[0-9]+(?:\.[0-9]+)*(?![\w.])"),
    re.compile(r"(?<![\w.])D[1-9][0-9]?(?![\w.])"),
    re.compile(r"TSE-[EM][0-9]"),
    re.compile(r"(?<![\w.])plan" "/"),
    re.compile("refs" "-md-deep|proto" "types"),
]
MD_EXT = ".m" "d"
MD_NAME = re.compile(r"[\w./-]*\w\.m" r"d(?!\w)")
TEMPLATE_MD = {"README." "md", "info." "md"}
# the project's own documents, by path; only these markdown files may be added or named
PROJECT_MD = {"VERIFICATION." "md", "docs/architecture." "md", "docs/isa." "md", "docs/toolchain." "md"}
PROJECT_MD_NAMES = {os.path.basename(x) for x in PROJECT_MD}
DASHES = chr(0x2013) + chr(0x2014)
DHYPHEN = re.compile(r"(?:^|\s)--(?:\s|$)")

# comment syntax per file kind; files of other kinds get no double-hyphen check
HASH = {".py", ".sh", ".mk", ".yaml", ".yml", ".toml", ".ini", ".part", ".cfg", ".txt",
        ".mcy", ".sby", ".gtkw"}
SLASH = {".sv", ".v", ".svh", ".vh", ".c", ".h", ".cpp", ".hpp", ".tse", ".json"}
HASH_NAMES = {"Makefile", "Dockerfile", ".gitignore"}


def git(*args, cwd=None):
    return subprocess.run(("git",) + args, cwd=cwd, check=True, capture_output=True).stdout


def is_emoji(ch):
    o = ord(ch)
    return (0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF
            or 0xFE00 <= o <= 0xFE0F or o == 0x200D)


def char_findings(line):
    out = []
    for ch in sorted(set(c for c in line if ord(c) > 0x7F)):
        if ch in DASHES:
            out.append(("dash", "U+%04X" % ord(ch)))
        elif is_emoji(ch):
            out.append(("emoji", "U+%04X" % ord(ch)))
        else:
            out.append(("ascii", "U+%04X" % ord(ch)))
    return out


def ref_findings(line):
    out = [("ref", m.group(0)) for r in REF_RES for m in r.finditer(line)]
    for m in MD_NAME.finditer(line):
        if os.path.basename(m.group(0)) not in TEMPLATE_MD | PROJECT_MD_NAMES:
            out.append(("ref", m.group(0)))
    return out


def hash_comment(line):
    """Text after a '#' that starts a comment (not inside a quote, not '$#' or '#!')."""
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == "\\":
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t") and not line.startswith("#!"):
            return line[i + 1:]
    return None


class SlashState:
    """Comment text of each line of a C-like file, tracking /* */ across lines."""

    def __init__(self):
        self.block = False

    def comment(self, line):
        out, i, quote = [], 0, False
        while i < len(line):
            if self.block:
                j = line.find("*/", i)
                out.append(line[i:] if j < 0 else line[i:j])
                if j < 0:
                    break
                self.block, i = False, j + 2
                continue
            c = line[i]
            if quote:
                if c == "\\":
                    i += 1
                elif c == '"':
                    quote = False
            elif c == '"':
                quote = True
            elif line.startswith("//", i):
                out.append(line[i + 2:])
                break
            elif line.startswith("/*", i):
                self.block, i = True, i + 2
                continue
            i += 1
        return " ".join(out) if out else None


def comment_reader(path):
    base = os.path.basename(path)
    ext = os.path.splitext(base)[1]
    if ext in SLASH:
        return SlashState().comment
    if ext in HASH or base in HASH_NAMES or base.startswith("Makefile"):
        return hash_comment
    if ext == MD_EXT:
        return lambda line: line
    return None


def check_text(path, text, skip_lines=frozenset()):
    findings = []
    comment = comment_reader(path)
    for n, line in enumerate(text.split("\n"), 1):
        if line in skip_lines:
            if comment:
                comment(line)
            continue
        for rule, what in char_findings(line) + ref_findings(line):
            findings.append((path, n, rule, what))
        c = comment(line) if comment else None
        if c is not None and DHYPHEN.search(c):
            findings.append((path, n, "dhyphen", c.strip()[:60]))
    return findings


def template_commit(root):
    return git("rev-list", "--max-parents=0", "HEAD", cwd=root).decode().split()[-1]


def check_tree(root):
    tmpl = template_commit(root)
    tmpl_files = set(git("ls-tree", "-r", "--name-only", tmpl, cwd=root).decode().splitlines())
    changed = set(git("diff", "--name-only", tmpl, cwd=root).decode().splitlines())
    findings = []
    files = git("ls-files", "--cached", "--others", "--exclude-standard", cwd=root)
    for path in files.decode().splitlines():
        if path in tmpl_files and path not in changed:
            continue
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            continue
        if path.endswith(MD_EXT) and path not in tmpl_files and path not in PROJECT_MD:
            findings.append((path, 0, "markdown", "not a template file"))
        data = open(full, "rb").read()
        if b"\0" in data:
            continue
        skip = frozenset()
        if path in tmpl_files:
            old = git("show", "%s:%s" % (tmpl, path), cwd=root).decode("utf-8", "replace")
            skip = frozenset(old.split("\n"))
        findings += check_text(path, data.decode("utf-8", "replace"), skip)
    return findings


def check_commits(root, rng):
    out = git("log", "--format=%H%x00%B%x01", rng, cwd=root).decode("utf-8", "replace")
    findings = []
    for entry in out.split("\x01"):
        entry = entry.strip("\n")
        if not entry:
            continue
        sha, msg = entry.split("\x00", 1)
        for n, line in enumerate(msg.split("\n"), 1):
            for rule, what in char_findings(line) + ref_findings(line):
                findings.append((sha[:10], n, rule, what))
            if DHYPHEN.search(line):
                findings.append((sha[:10], n, "dhyphen", line.strip()[:60]))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description="repository hygiene check")
    ap.add_argument("--commits", metavar="RANGE", help="check commit messages in this git range")
    ap.add_argument("-C", dest="root", default=".", help="repository root")
    a = ap.parse_args(argv)
    root = git("rev-parse", "--show-toplevel", cwd=a.root).decode().strip()
    findings = check_commits(root, a.commits) if a.commits else check_tree(root)
    for where, n, rule, what in findings:
        sys.stdout.write("%s:%d: %s: %s\n" % (where, n, rule, what))
    if findings:
        sys.stdout.write("hygiene: %d finding(s)\n" % len(findings))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
