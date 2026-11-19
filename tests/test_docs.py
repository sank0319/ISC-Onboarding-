"""Documentation guard: every directory that needs a README has one, and every relative Markdown link resolves."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
FENCE = re.compile(r"^```.*?^```", re.S | re.M)
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.M)

CODE_AND_DOC_DIRS = [".", ".github/workflows", "onboarding", "onboarding/config", "onboarding/connectors", "onboarding/scripts",
                     "onboarding/scripts/connectors", "onboarding/logs", "docs", "docs/examples", "docs/meeting-notes", "tests"]
CONNECTORS = sorted(p.name for p in (ROOT / "onboarding" / "connectors").iterdir() if p.is_dir())


def markdown_files():
    return sorted(p for p in ROOT.rglob("*.md") if not SKIP_DIRS & set(p.relative_to(ROOT).parts)
                  and p.relative_to(ROOT).parts[0] not in ("final",))


def slug(heading):
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[`*_]", "", heading).lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def anchors(path):
    return {slug(h) for h in HEADING.findall(FENCE.sub("", path.read_text(encoding="utf-8")))}


@pytest.mark.parametrize("folder", CODE_AND_DOC_DIRS)
def test_each_code_and_doc_directory_has_a_readme(folder):
    assert (ROOT / folder / "README.md").is_file(), f"{folder}/README.md is missing"


@pytest.mark.parametrize("connector", CONNECTORS)
@pytest.mark.parametrize("part", ["", "apps", "data"])
def test_each_connector_folder_and_its_apps_and_data_have_a_readme(connector, part):
    path = ROOT / "onboarding" / "connectors" / connector / part / "README.md"
    assert path.is_file(), f"{path.relative_to(ROOT)} is missing"


@pytest.mark.parametrize("connector", CONNECTORS)
def test_connector_readme_names_its_template_and_code_file(connector):
    text = (ROOT / "onboarding" / "connectors" / connector / "README.md").read_text(encoding="utf-8")
    assert f"{connector}-template.yaml" in text
    assert f"scripts/connectors/{connector}.py" in text


def test_main_readme_lists_every_connector_workflow_and_script():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    names = (CONNECTORS
             + [p.name for p in (ROOT / ".github" / "workflows").glob("*.yml")]
             + [p.name for p in (ROOT / "onboarding" / "scripts").glob("*.py")])
    assert [n for n in names if n not in text] == []


@pytest.mark.parametrize("md", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(md):
    bad = []
    for target in LINK.findall(FENCE.sub("", md.read_text(encoding="utf-8"))):
        if re.match(r"^(https?:|mailto:)", target):
            continue
        ref, _, frag = target.partition("#")
        dest = md if not ref else (md.parent / ref).resolve()
        if not dest.exists():
            bad.append(target)
        elif frag and dest.is_file() and dest.suffix == ".md" and frag not in anchors(dest):
            bad.append(target)
    assert bad == [], f"broken links in {md.relative_to(ROOT)}: {bad}"
