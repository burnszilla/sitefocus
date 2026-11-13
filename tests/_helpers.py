"""Find the sitefocus modules whether the tests run from the skill folder
(../scripts) or against the installed package (import sitefocus)."""
import csv
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")

os.environ.setdefault("SITEFOCUS_BACKOFF", "0.05")
os.environ.setdefault("SITEFOCUS_CONTACT", "tests@example.com")


def mod(name):
    if os.path.isdir(SCRIPTS) and os.path.exists(os.path.join(SCRIPTS, f"{name}.py")):
        if SCRIPTS not in sys.path:
            sys.path.insert(0, SCRIPTS)
        return importlib.import_module(name)
    return importlib.import_module(f"sitefocus.{name}")


def run(name, args):
    """Run a module's main() in-process with the given argv."""
    m = mod(name)
    old = sys.argv
    sys.argv = [name] + [str(x) for x in args]
    try:
        return m.main()
    finally:
        sys.argv = old


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rcsv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def rtext(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def wtext(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
