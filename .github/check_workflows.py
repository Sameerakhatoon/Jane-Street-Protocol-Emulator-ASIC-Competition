"""Structural check of the GitHub workflow and local action files.

    python3 .github/check_workflows.py [repo root]

A stand-in for actionlint, which is not installed everywhere: every file must
parse as YAML and follow the shape GitHub expects (triggers, jobs with
runs-on and steps, steps with exactly one of uses or run, needs naming real
jobs, remote actions pinned to a ref, local actions present, matrix and needs
references defined, balanced expressions). Exit status 0 when clean, 1 with
one "path: problem" line per finding.
"""
import os
import re
import sys

import yaml

EVENTS = {"push", "pull_request", "workflow_dispatch", "schedule", "workflow_call",
          "workflow_run", "release", "pull_request_target"}
JOB_KEYS = {"name", "runs-on", "needs", "if", "steps", "strategy", "timeout-minutes",
            "permissions", "env", "outputs", "concurrency", "continue-on-error",
            "defaults", "environment", "container", "services", "uses", "with", "secrets"}
STEP_KEYS = {"name", "id", "if", "uses", "run", "with", "env", "shell",
             "working-directory", "continue-on-error", "timeout-minutes"}
CRON = re.compile(r"^(\S+\s+){4}\S+$")
EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
MATRIX_REF = re.compile(r"\bmatrix\.([A-Za-z_][\w-]*)")
NEEDS_REF = re.compile(r"\bneeds\.([A-Za-z_][\w-]*)")
STEPS_REF = re.compile(r"\bsteps\.([A-Za-z_][\w-]*)")


class Checker:
    def __init__(self, root):
        self.root = root
        self.findings = []

    def bad(self, path, msg):
        self.findings.append("%s: %s" % (os.path.relpath(path, self.root), msg))

    def load(self, path):
        text = open(path, encoding="utf-8").read()
        if "\t" in text:
            self.bad(path, "tab character")
        if text.count("${{") != text.count("}}"):
            self.bad(path, "unbalanced ${{ }}")
        try:
            return yaml.safe_load(text), text
        except yaml.YAMLError as e:
            self.bad(path, "YAML: %s" % str(e).splitlines()[0])
            return None, text

    def check_steps(self, path, where, steps, matrix_keys, needs, text_of):
        if not isinstance(steps, list) or not steps:
            self.bad(path, "%s: steps must be a non-empty list" % where)
            return
        ids = set()
        for n, st in enumerate(steps, 1):
            at = "%s step %d" % (where, n)
            if not isinstance(st, dict):
                self.bad(path, "%s: not a mapping" % at)
                continue
            for k in set(st) - STEP_KEYS:
                self.bad(path, "%s: unknown key %r" % (at, k))
            if ("uses" in st) == ("run" in st):
                self.bad(path, "%s: needs exactly one of uses and run" % at)
            if "uses" in st:
                self.check_uses(path, at, st["uses"])
            if "with" in st and "uses" not in st:
                self.bad(path, "%s: with without uses" % at)
            for ref in STEPS_REF.findall(text_of(st)):
                if ref not in ids:
                    self.bad(path, "%s: steps.%s is not an earlier step id" % (at, ref))
            for ref in MATRIX_REF.findall(text_of(st)):
                if ref not in matrix_keys:
                    self.bad(path, "%s: matrix.%s is not defined" % (at, ref))
            for ref in NEEDS_REF.findall(text_of(st)):
                if ref not in needs:
                    self.bad(path, "%s: needs.%s is not in needs" % (at, ref))
            if "id" in st:
                ids.add(st["id"])

    def check_uses(self, path, at, uses):
        if uses.startswith("./"):
            d = os.path.join(self.root, uses[2:])
            if not any(os.path.isfile(os.path.join(d, f)) for f in ("action.yml", "action.yaml")):
                self.bad(path, "%s: local action %s not found" % (at, uses))
        elif uses.startswith("docker://"):
            pass
        elif not re.match(r"^[\w.-]+/[\w./-]+@[\w./-]+$", uses):
            self.bad(path, "%s: action %r not pinned to a ref" % (at, uses))

    def workflow(self, path):
        doc, _ = self.load(path)
        if not isinstance(doc, dict):
            if doc is not None:
                self.bad(path, "not a mapping")
            return
        on = doc.get("on", doc.get(True))      # YAML 1.1 reads the key on as true
        if on is None:
            self.bad(path, "no on: triggers")
        events = on if isinstance(on, list) else list(on) if isinstance(on, dict) else [on]
        for ev in events:
            if ev not in EVENTS:
                self.bad(path, "unknown event %r" % ev)
        if isinstance(on, dict):
            for item in on.get("schedule") or []:
                if not CRON.match(str(item.get("cron", ""))):
                    self.bad(path, "bad cron %r" % item)
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            self.bad(path, "no jobs")
            return
        for name, job in jobs.items():
            where = "job %s" % name
            if not re.match(r"^[A-Za-z_][\w-]*$", name):
                self.bad(path, "%s: bad job id" % where)
            if not isinstance(job, dict):
                self.bad(path, "%s: not a mapping" % where)
                continue
            for k in set(job) - JOB_KEYS:
                self.bad(path, "%s: unknown key %r" % (where, k))
            needs = job.get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            for nd in needs:
                if nd not in jobs:
                    self.bad(path, "%s: needs unknown job %r" % (where, nd))
            matrix = (job.get("strategy") or {}).get("matrix") or {}
            keys = set()
            if isinstance(matrix, dict):
                keys = {k for k in matrix if k not in ("include", "exclude")}
                for row in matrix.get("include") or []:
                    if isinstance(row, dict):
                        keys |= set(row)
            elif "fromJSON" in str(matrix):
                keys = None
            if "uses" in job:
                self.check_uses(path, where, job["uses"])
                continue
            if "runs-on" not in job:
                self.bad(path, "%s: no runs-on" % where)
            if keys is None:
                keys = set(MATRIX_REF.findall(yaml.safe_dump(job)))
            text_of = lambda node: yaml.safe_dump(node)
            for field in ("name", "runs-on", "if", "timeout-minutes"):
                for ref in MATRIX_REF.findall(text_of(job.get(field, ""))):
                    if ref not in keys:
                        self.bad(path, "%s: matrix.%s is not defined" % (where, ref))
            self.check_steps(path, where, job.get("steps"), keys, set(needs), text_of)

    def action(self, path):
        doc, _ = self.load(path)
        if not isinstance(doc, dict):
            self.bad(path, "not a mapping")
            return
        for k in ("name", "description", "runs"):
            if k not in doc:
                self.bad(path, "no %s" % k)
        runs = doc.get("runs") or {}
        if runs.get("using") != "composite":
            return
        steps = runs.get("steps")
        for n, st in enumerate(steps or [], 1):
            if isinstance(st, dict) and "run" in st and "shell" not in st:
                self.bad(path, "step %d: composite run steps need shell" % n)
        for ref in re.findall(r"\binputs\.([A-Za-z_][\w-]*)", yaml.safe_dump(runs)):
            if ref not in (doc.get("inputs") or {}):
                self.bad(path, "inputs.%s is not declared" % ref)
        self.check_steps(path, "runs", steps, set(), set(), lambda node: yaml.safe_dump(node))


def main(argv):
    root = os.path.abspath(argv[1] if len(argv) > 1 else ".")
    c = Checker(root)
    wf = os.path.join(root, ".github", "workflows")
    files = sorted(f for f in os.listdir(wf) if f.endswith((".yaml", ".yml")))
    for f in files:
        c.workflow(os.path.join(wf, f))
    adir = os.path.join(root, ".github", "actions")
    for d in sorted(os.listdir(adir)) if os.path.isdir(adir) else []:
        for f in ("action.yml", "action.yaml"):
            p = os.path.join(adir, d, f)
            if os.path.isfile(p):
                c.action(p)
    for line in c.findings:
        print(line)
    print("workflows: %d files, %d finding(s)" % (len(files), len(c.findings)))
    return 1 if c.findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
