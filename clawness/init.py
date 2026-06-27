#!/usr/bin/env python3
"""
clawness init — scan a project directory and auto-detect which rule
domains are relevant, then report which rules will fire and suggest
project-specific rules to create.

Usage:
    python -m writ_lite.init [project_dir]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# Map from detected file/pattern to rule domains
DETECTORS: list[tuple[str, list[str], str]] = [
    # (glob_pattern, domains_to_enable, human_description)
    ("package.json",           ["typescript", "general", "react"],  "Node.js project"),
    ("tsconfig.json",          ["typescript"],                      "TypeScript"),
    ("next.config.*",          ["nextjs", "react"],                 "Next.js"),
    ("capacitor.config.*",     ["capacitor"],                       "Capacitor (mobile)"),
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
]


def scan_project(project_dir: Path) -> dict:
    """Scan a project directory and return detection results."""
    detected: list[tuple[str, list[str]]] = []
    domains: set[str] = set()

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
        except (json.JSONDecodeError, IOError):
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
            except IOError:
                pass

    # Always include workflows if agents exist
    domains.add("workflows")

    return {
        "detected": detected,
        "domains": sorted(domains),
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
        out_dir = project_dir / ".writ" / "rules" / project_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / rule_filename
        out_path.write_text(rule_content, encoding="utf-8")
        print(f"Written to: {out_path}")
        print()
        print("Project rules directory created at .writ/rules/")
        print("Add more .yml rules here — they layer on top of global rules.")
        print("Add .writ/ to version control so your team shares the same rules.")
    else:
        print("(Run with --write to create .writ/rules/ in this project)")


if __name__ == "__main__":
    main()
