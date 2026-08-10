#!/usr/bin/env python3
"""
clawness init — scan a project directory and auto-detect which rule
domains are relevant, then report which rules will fire and suggest
project-specific rules to create.

Usage:
    python -m clawness.init [project_dir]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# Map from detected file/pattern to rule domains
DETECTORS: list[tuple[str, list[str], str]] = [
    # (glob_pattern, domains_to_enable, human_description)
    # Bare package.json → Node/TS only. React/Next/etc. come from the deep
    # dependency scan below, so a plain Express or CLI project isn't mislabelled.
    ("package.json",           ["typescript", "general"],            "Node.js project"),
    ("tsconfig.json",          ["typescript"],                      "TypeScript"),
    ("next.config.*",          ["nextjs", "react"],                 "Next.js"),
    ("capacitor.config.*",     ["capacitor"],                       "Capacitor (mobile)"),
    # Bare *.py matters: without it a repo holding paper.tex + analysis.py and no
    # packaging file does NOT detect Python, so Python rules face the off-stack
    # floor in exactly the mixed science+code case.
    ("*.py",                   ["python"],                          "Python scripts"),
    ("requirements.txt",       ["python"],                          "Python (requirements.txt)"),
    ("pyproject.toml",         ["python"],                          "Python (pyproject.toml)"),
    ("Pipfile",                ["python"],                          "Python (Pipfile)"),
    ("main.py",                ["python", "fastapi"],               "Python app"),
    ("app.py",                 ["python", "fastapi"],               "Python app"),
    ("go.mod",                 ["go"],                              "Go module"),
    ("go.sum",                 ["go"],                              "Go module"),
    ("Cargo.toml",             ["rust"],                            "Rust crate"),
    ("pom.xml",                ["java"],                            "Maven (Java)"),
    ("build.gradle",           ["java"],                            "Gradle (Java)"),
    ("build.gradle.kts",       ["java"],                            "Gradle Kotlin DSL"),
    ("*.sh",                   ["bash"],                            "Shell scripts"),
    ("*.sql",                  ["sql"],                             "SQL files"),
    ("*.css",                  ["css"],                             "CSS"),
    ("*.scss",                 ["css"],                             "Sass/SCSS"),
    ("alembic.ini",            ["sql", "python"],                   "Alembic migrations"),
    ("Dockerfile",             ["docker", "general"],               "Docker"),
    ("docker-compose.yml",     ["docker"],                          "Docker Compose"),
    ("docker-compose.yaml",    ["docker"],                          "Docker Compose"),
    ("compose.yaml",           ["docker"],                          "Docker Compose"),
    (".github/workflows/*.yml",["general"],                         "GitHub Actions CI"),
    (".eslintrc*",             ["typescript", "general"],           "ESLint"),
    ("tailwind.config.*",      ["react", "css", "general"],         "Tailwind CSS"),
    ("prisma/schema.prisma",   ["nextjs", "sql", "general"],        "Prisma ORM"),
    ("drizzle.config.*",       ["nextjs", "sql", "general"],        "Drizzle ORM"),
    ("jest.config.*",          ["react", "typescript"],             "Jest testing"),
    ("vitest.config.*",        ["react", "typescript"],             "Vitest testing"),
    ("pytest.ini",             ["python"],                          "Pytest"),
    (".env.example",           ["general"],                         "Environment config"),
    # Scientific / research work. science/ is cross-cutting (never off-stack
    # penalized), so these drive `clawness init` reporting and compose with the
    # language domains — a numpy+LaTeX repo detects {science, python, general}.
    ("*.tex",                  ["science"],                         "LaTeX manuscript"),
    ("*.ipynb",                ["science", "python"],               "Jupyter notebook"),
    ("Project.toml",           ["julia", "science"],                "Julia project"),
    ("*.jl",                   ["julia", "science"],                "Julia source"),
    ("DESCRIPTION",            ["r", "science"],                    "R package"),
    ("*.R",                    ["r", "science"],                    "R scripts"),
    ("*.Rproj",                ["r", "science"],                    "RStudio project"),
    ("*.f90",                  ["fortran", "science"],              "Fortran source"),
    ("*.F90",                  ["fortran", "science"],              "Fortran source"),
    ("*.f",                    ["fortran", "science"],              "Fortran (fixed form)"),
    # MATLAB detects on its UNAMBIGUOUS extensions only. `*.m` is deliberately
    # absent: it is also Objective-C, and mislabelling an iOS project as MATLAB
    # would suppress its real rules via the off-stack floor. Missing a plain-.m
    # MATLAB project costs a note; a wrong detection costs correct advice.
    ("*.mlx",                  ["matlab", "science"],               "MATLAB live script"),
    ("*.slx",                  ["matlab", "science"],               "Simulink model"),
    # CFD cases. OpenFOAM's layout is the reliable tell (a case directory holds
    # system/controlDict); the mesh/case extensions cover the commercial codes.
    ("system/controlDict",     ["cfd", "science"],                  "OpenFOAM case"),
    ("*.foam",                 ["cfd", "science"],                  "OpenFOAM case"),
    ("Allrun",                 ["cfd", "science"],                  "OpenFOAM run script"),
    ("*.msh",                  ["cfd", "science"],                  "Mesh file"),
    ("*.cas",                  ["cfd", "science"],                  "Fluent case"),
]

# Deep scan: look inside package.json for specific dependencies
PACKAGE_JSON_DEPS: list[tuple[str, list[str], str]] = [
    ("next",                   ["nextjs", "react"],                 "Next.js"),
    ("react",                  ["react"],                           "React"),
    ("@capacitor/core",        ["capacitor"],                       "Capacitor"),
    ("fastapi",                ["fastapi"],                         "FastAPI"),
    ("express",                ["general"],                         "Express.js"),
    ("zod",                    ["typescript"],                      "Zod validation"),
    ("prisma",                 ["nextjs", "sql"],                   "Prisma"),
    ("drizzle-orm",            ["nextjs", "sql"],                   "Drizzle"),
    ("pg",                     ["sql"],                             "node-postgres"),
    ("mysql2",                 ["sql"],                             "MySQL driver"),
    ("better-sqlite3",         ["sql"],                             "SQLite driver"),
    ("knex",                   ["sql"],                             "Knex query builder"),
    ("react-hook-form",        ["react"],                           "React Hook Form"),
    ("@tanstack/react-query",  ["react"],                           "TanStack Query"),
    ("zustand",                ["react"],                           "Zustand state"),
    ("tailwindcss",            ["react", "general"],                "Tailwind CSS"),
    ("@anthropic-ai/sdk",      ["llm"],                             "Anthropic SDK"),
    ("openai",                 ["llm"],                             "OpenAI SDK"),
    ("ai",                     ["llm"],                             "Vercel AI SDK"),
    ("langchain",              ["llm"],                             "LangChain"),
]

# Deep scan: look inside requirements.txt / pyproject.toml for deps
PYTHON_DEPS: list[tuple[str, list[str], str]] = [
    ("fastapi",                ["fastapi"],                         "FastAPI"),
    ("django",                 ["python"],                          "Django"),
    ("flask",                  ["python"],                          "Flask"),
    ("sqlalchemy",             ["fastapi", "python", "sql"],        "SQLAlchemy"),
    ("pydantic",               ["fastapi"],                         "Pydantic"),
    ("alembic",                ["fastapi", "sql"],                  "Alembic migrations"),
    ("celery",                 ["fastapi"],                         "Celery tasks"),
    ("psycopg",                ["sql"],                             "PostgreSQL driver"),
    ("asyncpg",                ["sql"],                             "Async PostgreSQL driver"),
    ("pytest",                 ["python"],                          "Pytest"),
    ("anthropic",              ["llm"],                             "Anthropic SDK"),
    ("openai",                 ["llm"],                             "OpenAI SDK"),
    ("langchain",              ["llm"],                             "LangChain"),
    ("llama-index",            ["llm"],                             "LlamaIndex"),
    ("litellm",                ["llm"],                             "LiteLLM"),
    ("numpy",                  ["science", "python"],               "NumPy"),
    ("scipy",                  ["science", "python"],               "SciPy"),
    ("sympy",                  ["science", "python"],               "SymPy"),
    ("matplotlib",             ["science", "python"],               "Matplotlib"),
    ("astropy",                ["science", "python"],               "Astropy"),
    ("pandas",                 ["science", "python"],               "pandas"),
    # Machine-learning: TRAINING/evaluating your own models, distinct from the
    # `llm` domain (building on hosted models). Gated on the modelling library,
    # not on "is this science" — a fintech fraud model and a physics classifier
    # get the same discipline rules, a plain CRUD app gets none. Substring match,
    # so "torch" also catches pytorch/torchvision and "jax" catches jaxlib.
    ("scikit-learn",           ["ml", "python"],                    "scikit-learn"),
    ("xgboost",                ["ml", "python"],                    "XGBoost"),
    ("lightgbm",               ["ml", "python"],                    "LightGBM"),
    ("statsmodels",            ["ml", "python"],                    "statsmodels"),
    ("torch",                  ["ml", "python"],                    "PyTorch"),
    ("tensorflow",             ["ml", "python"],                    "TensorFlow"),
    ("keras",                  ["ml", "python"],                    "Keras"),
    ("jax",                    ["ml", "python"],                    "JAX"),
]


# Frameworks whose major version changes the code you should write. Deliberately
# short: the point is not an inventory (the manifest is right there) but the handful
# where writing for the wrong major produces confidently wrong code — App Router vs
# Pages, Pydantic v1 vs v2, SQLAlchemy 1.4 vs 2.0, Tailwind 3 vs 4. Adding a package
# here costs a line in the SessionStart note, so add one only when the majors differ
# enough to matter. Display label first, so the note reads in the ecosystem's own
# spelling rather than the package name.
VERSION_WATCH_JS: list[tuple[str, str]] = [
    ("next", "Next.js"),
    ("react", "React"),
    ("vue", "Vue"),
    ("svelte", "Svelte"),
    ("typescript", "TypeScript"),
    ("tailwindcss", "Tailwind"),
    ("express", "Express"),
    ("@capacitor/core", "Capacitor"),
]

VERSION_WATCH_PY: list[tuple[str, str]] = [
    ("django", "Django"),
    ("fastapi", "FastAPI"),
    ("pydantic", "Pydantic"),
    ("sqlalchemy", "SQLAlchemy"),
    ("numpy", "NumPy"),
    ("pandas", "pandas"),
]

# "^14.2.0" / ">=2.0,<3" / "~1.4.52" -> "14" / "2" / "1.4". Two components at most:
# a major alone is what matters for most, but SQLAlchemy 1.4-vs-2.0 and Tailwind
# 3.4-vs-4 are minor-sensitive, and a bare major would erase that.
_VERSION_LEAD = re.compile(r"(\d+(?:\.\d+)?)")


def _clean_version(spec: str) -> str:
    """Leading numeric version out of a range spec, or "" if there isn't one.

    Best-effort by design: a git URL, a workspace protocol, `*` or `latest` yields
    "" and the package is simply omitted from the note. A wrong version is worse
    than no version — it would have Claude writing for an API that isn't there.
    """
    m = _VERSION_LEAD.search(spec or "")
    return m.group(1) if m else ""


def _python_version(content: str, dep: str) -> str:
    """Pinned version of `dep` from requirements/pyproject text, or ""."""
    # Anchored on the dependency name at a word boundary so "openai" doesn't match
    # inside "langchain-openai". Accepts ==, >=, ~=, and PEP 621 quoted specs.
    m = re.search(
        r"(?<![\w.-])" + re.escape(dep) + r"\s*(?:\[[^\]]*\])?\s*[<>=~!^]{1,2}\s*v?(\d+(?:\.\d+)?)",
        content,
    )
    return m.group(1) if m else ""


def scan_project(project_dir: Path) -> dict:
    """Scan a project directory and return detection results."""
    detected: list[tuple[str, list[str]]] = []
    domains: set[str] = set()
    # Label -> version major, for the frameworks in VERSION_WATCH_*. Only ever
    # populated from what the project actually declares; an unreadable or absent
    # manifest leaves it empty and every consumer treats that as "say nothing".
    versions: dict[str, str] = {}

    # Always include mandatory and general
    domains.add("general")

    # File-based detection
    for pattern, rule_domains, desc in DETECTORS:
        matches = list(project_dir.glob(pattern))
        if matches:
            detected.append((desc, rule_domains))
            domains.update(rule_domains)

    # Deep scan package.json
    pkg_json = project_dir / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            all_deps = {}
            all_deps.update(pkg.get("dependencies", {}))
            all_deps.update(pkg.get("devDependencies", {}))
            for dep_name, rule_domains, desc in PACKAGE_JSON_DEPS:
                if dep_name in all_deps:
                    detected.append((f"{desc} (package.json)", rule_domains))
                    domains.update(rule_domains)
            for dep_name, label in VERSION_WATCH_JS:
                v = _clean_version(str(all_deps.get(dep_name, "")))
                if v:
                    versions[label] = v
        except (json.JSONDecodeError, IOError, AttributeError):
            pass

    # Deep scan Python deps
    for req_file in ["requirements.txt", "pyproject.toml", "Pipfile"]:
        req_path = project_dir / req_file
        if req_path.exists():
            try:
                content = req_path.read_text(encoding="utf-8").lower()
                for dep_name, rule_domains, desc in PYTHON_DEPS:
                    if dep_name in content:
                        detected.append((f"{desc} ({req_file})", rule_domains))
                        domains.update(rule_domains)
                for dep_name, label in VERSION_WATCH_PY:
                    # First file that pins it wins — requirements.txt is scanned
                    # before pyproject.toml, which is the right precedence when a
                    # project has both (the lockfile-ish one is the truth).
                    if label not in versions:
                        v = _python_version(content, dep_name)
                        if v:
                            versions[label] = v
            except (IOError, UnicodeError):
                pass

    # Always include workflows if agents exist
    domains.add("workflows")

    return {
        "detected": detected,
        "domains": sorted(domains),
        "versions": versions,
        "project_dir": str(project_dir),
    }


def generate_starter_rule(project_dir: Path, domains: list[str]) -> str:
    """Generate a starter project-specific rule based on detected stack."""
    stack_parts = []
    if "nextjs" in domains:
        stack_parts.append("Next.js App Router")
    if "react" in domains and "nextjs" not in domains:
        stack_parts.append("React")
    if "capacitor" in domains:
        stack_parts.append("Capacitor (iOS/Android)")
    if "fastapi" in domains:
        stack_parts.append("FastAPI")
    if "python" in domains and "fastapi" not in domains:
        stack_parts.append("Python")
    if "go" in domains:
        stack_parts.append("Go")
    if "rust" in domains:
        stack_parts.append("Rust")
    if "java" in domains:
        stack_parts.append("Java")
    if "typescript" in domains:
        stack_parts.append("TypeScript")

    stack_str = " + ".join(stack_parts) if stack_parts else "this project"
    project_name = project_dir.name

    # Slugify each stack part individually so separators don't get mangled
    # (a naive ', '.join(...).replace(' ', '-') turns ", " into ",-").
    tag_list = [
        re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-")
        for part in stack_parts
    ]
    tags_str = ", ".join(t for t in tag_list if t)

    return f"""id: {project_name.upper().replace('-', '_')}-STACK-001
domain: {project_name}
severity: info
tags: [{tags_str}]
triggers: [architecture, stack, setup, convention, project]
when: Making decisions about project architecture or conventions.
rule: >
  This project uses {stack_str}. Follow the established patterns
  in the existing codebase. Check existing files for naming conventions,
  directory structure, and import patterns before creating new files.
  When in doubt, match the style of adjacent files.
violation: "Introducing a new pattern that conflicts with the existing codebase"
correct: "Following established project conventions and asking if unsure"
"""


def main() -> None:
    project_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

    if not project_dir.is_dir():
        print(f"ERROR: {project_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    results = scan_project(project_dir)

    print(f"Project: {results['project_dir']}")
    print()

    if results["detected"]:
        print("Detected stack:")
        seen = set()
        for desc, _ in results["detected"]:
            if desc not in seen:
                print(f"  + {desc}")
                seen.add(desc)
    else:
        print("  No known frameworks detected.")
        print("  (Run this from your project root, not the clawness directory)")

    # Same versions the SessionStart stack note reports — shown here so `init` is a
    # way to check what Clawness thinks you're on without starting a session.
    if results.get("versions"):
        print()
        print("Declared versions:")
        for label, version in sorted(results["versions"].items()):
            print(f"  {label} {version}")

    print()
    print(f"Recommended rule domains: {', '.join(results['domains'])}")
    print()

    # Generate starter rule
    rule_content = generate_starter_rule(project_dir, results["domains"])
    project_name = project_dir.name
    rule_filename = f"{project_name.upper().replace('-', '_')}-STACK-001.yml"

    print("Starter project rule:")
    print()
    print(rule_content)

    # Check if we should write it
    if "--write" in sys.argv:
        out_dir = project_dir / ".clawness" / "rules" / project_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / rule_filename
        out_path.write_text(rule_content, encoding="utf-8")
        print(f"Written to: {out_path}")

        # Seed an empty lessons-learned log. Clawness injects this file every
        # turn, so the team accumulates per-codebase gotchas over time (see
        # WF-LESSONS-001). Don't clobber an existing one.
        memory_path = project_dir / ".clawness" / "memory.md"
        if not memory_path.exists():
            from .core import MEMORY_TEMPLATE
            memory_path.write_text(MEMORY_TEMPLATE, encoding="utf-8")
            print(f"Written to: {memory_path}")

        print()
        print("Project rules directory created at .clawness/rules/")
        print("Add more .yml rules here — they layer on top of global rules.")
        print("Lessons log created at .clawness/memory.md — append gotchas as you hit them.")
        print("Add .clawness/ to version control so your team shares the same rules.")
    else:
        print("(Run with --write to create .clawness/rules/ in this project)")


if __name__ == "__main__":
    main()
