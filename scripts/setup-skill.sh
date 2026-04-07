#!/usr/bin/env bash
# Install dependencies for one or more octobots skills.
#
# Reads three dependency sources:
#   1. PEP 723 inline blocks in scripts/*.py  → pip install
#   2. setup.yaml dependencies.pip            → pip install
#   3. setup.yaml dependencies.npm            → npm install -g
#   4. setup.yaml dependencies.mcp            → merged into .mcp.json
#
# Usage:
#   octobots/scripts/setup-skill.sh <skill>             # by name
#   octobots/scripts/setup-skill.sh --role <role>       # all skills for a role
#   octobots/scripts/setup-skill.sh --dry-run <skill>   # show, don't install
#   octobots/scripts/setup-skill.sh --check <skill>     # exit 0 = satisfied

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OCTOBOTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(pwd)"

DRY_RUN=0
CHECK_MODE=0
ROLE=""
SKILL_ARG=""

# ── Argument parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)  DRY_RUN=1; shift ;;
        --check)    CHECK_MODE=1; shift ;;
        --role)     ROLE="${2:?--role requires a value}"; shift 2 ;;
        -*)         echo "Unknown flag: $1" >&2; exit 1 ;;
        *)          SKILL_ARG="$1"; shift ;;
    esac
done

if [[ -z "$ROLE" && -z "$SKILL_ARG" ]]; then
    echo "Usage: setup-skill.sh [--dry-run|--check] <skill>" >&2
    echo "       setup-skill.sh [--dry-run|--check] --role <role>" >&2
    exit 1
fi

# ── Resolve skill directories ─────────────────────────────────────────────────

# find_skill_dir <name> → prints the skill directory path (or empty)
find_skill_dir() {
    local skill="$1"
    # 1. Installed via npx skills add (real dir, not symlink from octobots)
    local installed="$PROJECT_DIR/.claude/skills/$skill"
    if [[ -d "$installed" ]]; then
        echo "$installed"
        return
    fi
    # 2. Bundled in octobots/skills/
    local bundled="$OCTOBOTS_DIR/skills/$skill"
    if [[ -d "$bundled" ]]; then
        echo "$bundled"
        return
    fi
    echo ""
}

# resolve_skills_for_role <role> → prints skill names, one per line
resolve_skills_for_role() {
    local role="$1"
    local agent_md="$PROJECT_DIR/.octobots/roles/$role/AGENT.md"
    [[ -f "$agent_md" ]] || agent_md="$PROJECT_DIR/.claude/agents/$role/AGENT.md"
    if [[ ! -f "$agent_md" ]]; then
        echo "  [setup-skill] role '$role' not found" >&2
        return 1
    fi
    local skills_line; skills_line=$(grep -m1 '^skills:' "$agent_md" 2>/dev/null || true)
    if [[ -z "$skills_line" ]]; then
        # No skills: key → no-op
        return 0
    fi
    local skills_val="${skills_line#skills:}"
    skills_val="${skills_val//[/ }"
    skills_val="${skills_val//]/ }"
    # Print one skill per line
    tr ', ' '\n' <<< "$skills_val" | grep -v '^[[:space:]]*$' || true
}

# ── Dependency installers ─────────────────────────────────────────────────────

# Parse PEP 723 inline dependency blocks from Python files
# Returns unique deps, one per line
parse_pep723_deps() {
    local skill_dir="$1"
    local scripts_dir="$skill_dir/scripts"
    [[ -d "$scripts_dir" ]] || return 0
    grep -h -A100 '^# ///' "$scripts_dir"/*.py 2>/dev/null \
        | awk '
            /^# \/\/\/ script/ { in_block=1; next }
            /^# \/\/\/$/       { in_block=0; next }
            in_block && /^# dependencies/ { found_deps=1; next }
            found_deps && /^# \[/ { next }
            found_deps && /^# \]/ { found_deps=0; next }
            found_deps && /^# / { gsub(/^# +"?|"?,?$/, ""); if (length($0)) print; next }
            found_deps { found_deps=0 }
        ' \
        | sort -u || true
}

# Parse setup.yaml with Python stdlib (no PyYAML required)
# Usage: parse_setup_yaml <skill_dir> <section>   (section: pip|npm|mcp_names)
parse_setup_yaml() {
    local skill_dir="$1"
    local section="$2"
    local yaml="$skill_dir/setup.yaml"
    [[ -f "$yaml" ]] || return 0
    python3 - "$yaml" "$section" << 'PYEOF'
import sys, re

yaml_path = sys.argv[1]
section = sys.argv[2]
text = open(yaml_path).read()

def extract_list(text, key):
    """Extract a simple list under dependencies.<key>:"""
    pattern = rf'^\s*{re.escape(key)}:\s*$'
    lines = text.splitlines()
    in_section = False
    depth = None
    results = []
    for line in lines:
        if re.match(pattern, line):
            in_section = True
            depth = len(line) - len(line.lstrip())
            continue
        if in_section:
            stripped = line.lstrip()
            if not stripped or stripped.startswith('#'):
                continue
            indent = len(line) - len(stripped)
            if indent <= depth and stripped and not stripped.startswith('-'):
                break
            m = re.match(r'^\s*-\s+(.+)', line)
            if m:
                val = m.group(1).strip().strip('"').strip("'")
                # For mcp_names: only take the 'name:' field from list items
                if section == 'mcp_names':
                    results.append(val.split(':')[0].strip() if ':' in val else val)
                else:
                    results.append(val)
    return results

# Navigate to dependencies block first
dep_start = text.find('dependencies:')
if dep_start == -1:
    sys.exit(0)
dep_text = text[dep_start:]

key = 'mcp_names' if section == 'mcp_names' else section
items = extract_list(dep_text, 'pip' if section == 'pip' else
                                'npm' if section == 'npm' else 'mcp')

if section == 'mcp_names':
    # Re-parse to get just the name: field from mcp list items
    in_mcp = False
    depth = None
    items = []
    for line in dep_text.splitlines():
        if re.match(r'^\s*mcp:\s*$', line):
            in_mcp = True
            depth = len(line) - len(line.lstrip())
            continue
        if in_mcp:
            stripped = line.lstrip()
            if not stripped:
                continue
            indent = len(line) - len(stripped)
            if indent <= depth and stripped and not stripped.startswith('-'):
                break
            m = re.match(r'^\s*name:\s*(.+)', line)
            if m:
                items.append(m.group(1).strip().strip('"').strip("'"))

for item in items:
    print(item)
PYEOF
}

# Install pip packages
install_pip() {
    local skill="$1"; shift
    local deps=("$@")
    [[ ${#deps[@]} -eq 0 ]] && return 0
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  [dry-run] [$skill] pip: ${deps[*]}"
        return 0
    fi
    if [[ $CHECK_MODE -eq 1 ]]; then
        local missing=()
        for dep in "${deps[@]}"; do
            # Strip version specifiers for the check
            local pkg; pkg=$(echo "$dep" | sed 's/[><=!].*//')
            python3 -c "import importlib; importlib.import_module('${pkg//-/_}')" 2>/dev/null || missing+=("$dep")
        done
        if [[ ${#missing[@]} -gt 0 ]]; then
            echo "  [$skill] pip missing: ${missing[*]}"
            return 1
        fi
        return 0
    fi
    local pip_cmd
    if command -v pip3 &>/dev/null; then pip_cmd=pip3
    elif command -v pip &>/dev/null; then pip_cmd=pip
    else echo "  [$skill] pip not found — skipping: ${deps[*]}" >&2; return 0
    fi
    echo "  [$skill] pip: ${deps[*]}"
    $pip_cmd install -q "${deps[@]}"
}

# Install npm globals
install_npm() {
    local skill="$1"; shift
    local pkgs=("$@")
    [[ ${#pkgs[@]} -eq 0 ]] && return 0
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  [dry-run] [$skill] npm -g: ${pkgs[*]}"
        return 0
    fi
    if [[ $CHECK_MODE -eq 1 ]]; then
        local missing=()
        for pkg in "${pkgs[@]}"; do
            local name; name=$(echo "$pkg" | sed 's/@[^@].*//; s|.*/||')
            command -v "$name" &>/dev/null || missing+=("$pkg")
        done
        if [[ ${#missing[@]} -gt 0 ]]; then
            echo "  [$skill] npm missing: ${missing[*]}"
            return 1
        fi
        return 0
    fi
    if ! command -v npm &>/dev/null; then
        echo "  [$skill] npm not found — skipping: ${pkgs[*]}" >&2
        return 0
    fi
    echo "  [$skill] npm -g: ${pkgs[*]}"
    npm install -g -q "${pkgs[@]}"
}

# Merge MCP server entries into .mcp.json (delegates to apply-skill-deps.py logic)
install_mcp() {
    local skill="$1"
    local skill_dir="$2"
    local yaml="$skill_dir/setup.yaml"
    [[ -f "$yaml" ]] || return 0
    local names
    names=$(parse_setup_yaml "$skill_dir" mcp_names) || return 0
    [[ -z "$names" ]] && return 0

    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  [dry-run] [$skill] mcp: $(echo "$names" | tr '\n' ' ')"
        return 0
    fi
    if [[ $CHECK_MODE -eq 1 ]]; then
        local mcp_json="$PROJECT_DIR/.mcp.json"
        local missing=()
        while IFS= read -r name; do
            [[ -z "$name" ]] && continue
            if [[ -f "$mcp_json" ]]; then
                python3 -c "import json,sys; d=json.load(open('$mcp_json')); sys.exit(0 if '$name' in d.get('mcpServers',{}) else 1)" 2>/dev/null || missing+=("$name")
            else
                missing+=("$name")
            fi
        done <<< "$names"
        if [[ ${#missing[@]} -gt 0 ]]; then
            echo "  [$skill] mcp missing: ${missing[*]}"
            return 1
        fi
        return 0
    fi

    # Delegate full merge to apply-skill-deps.py for correctness
    DEST="$OCTOBOTS_DIR" python3 "$SCRIPT_DIR/apply-skill-deps.py" 2>/dev/null || true
}

# ── Process one skill ─────────────────────────────────────────────────────────

process_skill() {
    local skill="$1"
    local skill_dir; skill_dir=$(find_skill_dir "$skill")

    if [[ -z "$skill_dir" ]]; then
        echo "  [$skill] not found in .claude/skills/ or octobots/skills/ — skipping" >&2
        return 0
    fi

    local any_work=0

    # 1. PEP 723 deps
    local pep_deps=()
    while IFS= read -r dep; do
        [[ -n "$dep" ]] && pep_deps+=("$dep")
    done < <(parse_pep723_deps "$skill_dir")

    # 2. setup.yaml pip deps
    local yaml_pip=()
    while IFS= read -r dep; do
        [[ -n "$dep" ]] && yaml_pip+=("$dep")
    done < <(parse_setup_yaml "$skill_dir" pip)

    # Merge and deduplicate pip deps
    local all_pip=()
    declare -A _seen_pip
    for dep in "${pep_deps[@]}" "${yaml_pip[@]}"; do
        local key; key=$(echo "$dep" | sed 's/[><=!].*//')
        [[ -n "${_seen_pip[$key]:-}" ]] && continue
        _seen_pip[$key]=1
        all_pip+=("$dep")
    done

    if [[ ${#all_pip[@]} -gt 0 ]]; then
        install_pip "$skill" "${all_pip[@]}" || return 1
        any_work=1
    fi

    # 3. npm globals
    local npm_pkgs=()
    while IFS= read -r pkg; do
        [[ -n "$pkg" ]] && npm_pkgs+=("$pkg")
    done < <(parse_setup_yaml "$skill_dir" npm)

    if [[ ${#npm_pkgs[@]} -gt 0 ]]; then
        install_npm "$skill" "${npm_pkgs[@]}" || return 1
        any_work=1
    fi

    # 4. MCP servers
    install_mcp "$skill" "$skill_dir" || return 1

    if [[ $any_work -eq 0 ]] && [[ $DRY_RUN -eq 0 ]] && [[ $CHECK_MODE -eq 0 ]]; then
        echo "  [$skill] no dependencies"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

SKILLS_TO_PROCESS=()

if [[ -n "$ROLE" ]]; then
    while IFS= read -r skill; do
        [[ -n "$skill" ]] && SKILLS_TO_PROCESS+=("$skill")
    done < <(resolve_skills_for_role "$ROLE")
fi

if [[ -n "$SKILL_ARG" ]]; then
    SKILLS_TO_PROCESS+=("$SKILL_ARG")
fi

if [[ ${#SKILLS_TO_PROCESS[@]} -eq 0 ]]; then
    exit 0
fi

overall_exit=0
for skill in "${SKILLS_TO_PROCESS[@]}"; do
    process_skill "$skill" || overall_exit=1
done

exit $overall_exit
