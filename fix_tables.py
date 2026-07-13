#!/usr/bin/env python3
"""fix_tables.py — Transform pandoc tables for RM2 xltabular pipeline.

Pandoc generates fixed-width p{} columns that waste space on the narrow
RM2 display (1404px). xltabular provides native page-breaking + X columns
without any tabularx patching.

Transforms:
  longtable    → xltabular  (p{} columns → X; keep \\endhead/\\endlastfoot)
  tabularx     → xltabular  (when \\endhead present — pandoc RM2 template output)
  p{...} cols  → X columns
  Strips \\endfirsthead (redundant when \\endhead covers all pages)
"""

import re
import sys


def _find_matching(s: str, start: int, open_ch: str, close_ch: str) -> int:
    """Find the matching close char for the open char at position start.
    Handles nesting. Returns position of matching close char."""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == open_ch:
            depth += 1
        elif s[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"Unmatched {open_ch} at position {start}")


def _find_matching_brace(s: str, start: int) -> int:
    """Find the matching '}' for the '{' at position start."""
    return _find_matching(s, start, '{', '}')


def _replace_p_columns(colspec: str) -> str:
    """Replace >{...}p{...} with X in a column spec string.
    Handles arbitrary nesting in both the >{...} prefix and p{...} width."""

    result = []
    i = 0
    while i < len(colspec):
        # Look for the pattern: > then {
        if colspec[i:i+1] == '>':
            j = i + 1
            if j < len(colspec) and colspec[j] == '{':
                # Found >{ — skip to matching }
                try:
                    end_prefix = _find_matching_brace(colspec, j)
                except ValueError:
                    result.append(colspec[i])
                    i += 1
                    continue
                # Now expect p{
                k = end_prefix + 1
                if k < len(colspec) and colspec[k:k+2] == 'p{':
                    try:
                        end_p = _find_matching_brace(colspec, k + 1)
                    except ValueError:
                        result.append(colspec[i])
                        i += 1
                        continue
                    # Replace entire >{...}p{...} with X
                    result.append('X')
                    i = end_p + 1
                    continue
                else:
                    result.append(colspec[i])
                    i += 1
                    continue
            else:
                result.append(colspec[i])
                i += 1
        else:
            result.append(colspec[i])
            i += 1

    return ''.join(result)


def _inside_verbatim(tex_content: str, pos: int) -> bool:
    """Check if position is inside a verbatim or Shaded environment."""
    # Search backwards for the nearest verbatim/Shaded open/close
    before = tex_content[:pos]
    for env in ('verbatim', 'Shaded', 'Highlighting'):
        last_open = before.rfind(f'\\begin{{{env}}}')
        last_close = before.rfind(f'\\end{{{env}}}')
        if last_open > last_close:
            return True
    return False


def fix_tabularx_endhead(tex_content: str) -> str:
    """Convert pandoc-generated tabularx+\\endhead → xltabular.

    Pandoc's RM2 template emits \\begin{tabularx} with \\endhead/\\endlastfoot
    for multi-page tables. tabularx alone does not page-break; xltabular does.
    Only converts tables that contain \\endhead (single-page tables are left as
    tabularx since they don't need page-breaking).
    """
    result = []
    i = 0

    while i < len(tex_content):
        m = re.search(r'\\begin\{tabularx\}', tex_content[i:])
        if not m:
            result.append(tex_content[i:])
            break

        start = i + m.start()
        end_cmd = i + m.end()

        if _inside_verbatim(tex_content, start):
            result.append(tex_content[i:end_cmd])
            i = end_cmd
            continue

        result.append(tex_content[i:start])

        # Parse \begin{tabularx}{width}{colspec}
        pos = end_cmd

        # Width arg: {\\linewidth} or similar
        if pos < len(tex_content) and tex_content[pos] == '{':
            try:
                width_end = _find_matching_brace(tex_content, pos)
            except ValueError:
                result.append(tex_content[start:end_cmd])
                i = end_cmd
                continue
            width_arg = tex_content[pos:width_end + 1]
            pos = width_end + 1
        else:
            result.append(tex_content[start:end_cmd])
            i = end_cmd
            continue

        # Colspec arg
        if pos < len(tex_content) and tex_content[pos] == '{':
            try:
                colspec_end = _find_matching_brace(tex_content, pos)
            except ValueError:
                result.append(tex_content[start:end_cmd])
                i = end_cmd
                continue
            colspec_arg = tex_content[pos:colspec_end + 1]
            pos = colspec_end + 1
        else:
            result.append(tex_content[start:end_cmd])
            i = end_cmd
            continue

        # Find matching \end{tabularx}
        end_match = re.search(r'\\end\{tabularx\}', tex_content[pos:])
        if not end_match:
            result.append(tex_content[start:pos])
            i = pos
            continue

        body_end = pos + end_match.start()
        body = tex_content[pos:body_end]
        after_end = pos + end_match.end()

        # Only promote to xltabular if \endhead is present (multi-page table)
        if r'\endhead' in body:
            body = re.sub(r'\\endfirsthead\s*', '', body)
            result.append(f'\\begin{{xltabular}}{width_arg}{colspec_arg}\n')
            result.append(body)
            result.append('\\end{xltabular}')
        else:
            # Single-page table — leave as tabularx
            result.append(tex_content[start:body_end])
            result.append('\\end{tabularx}')

        i = after_end

    return ''.join(result)


def fix_tables(tex_content: str) -> str:
    """Convert all longtable environments to xltabular with X columns."""

    result = []
    i = 0

    while i < len(tex_content):
        # Find next \begin{longtable}
        m = re.search(r'\\begin\{longtable\}', tex_content[i:])
        if not m:
            # No more longtables — append rest
            result.append(tex_content[i:])
            break

        start = i + m.start()
        end_cmd = i + m.end()

        # Skip longtable inside verbatim/Shaded/Highlighting blocks
        if _inside_verbatim(tex_content, start):
            result.append(tex_content[i:end_cmd])
            i = end_cmd
            continue

        # Append everything before this match
        result.append(tex_content[i:start])

        # Parse: \begin{longtable}[opt]{colspec}
        pos = end_cmd

        # Skip optional argument [...]
        if pos < len(tex_content) and tex_content[pos] == '[':
            try:
                pos = _find_matching(tex_content, pos, '[', ']') + 1
            except ValueError:
                pos = end_cmd  # fallback

        # Colspec in braces
        if pos < len(tex_content) and tex_content[pos] == '{':
            try:
                colspec_end = _find_matching_brace(tex_content, pos)
            except ValueError:
                result.append(tex_content[start:end_cmd])
                i = end_cmd
                continue
            colspec_interior = tex_content[pos + 1:colspec_end]
            new_interior = _replace_p_columns(colspec_interior)
            new_colspec = '{' + new_interior + '}'
        else:
            result.append(tex_content[start:end_cmd])
            i = end_cmd
            continue

        # Find matching \end{longtable}
        end_match = re.search(r'\\end\{longtable\}', tex_content[colspec_end:])
        if not end_match:
            result.append(tex_content[start:colspec_end + 1])
            i = colspec_end + 1
            continue

        body_end = colspec_end + end_match.start()
        body = tex_content[colspec_end + 1:body_end]

        if 'X' in new_interior:
            body = re.sub(r'\\endfirsthead\s*', '', body)
            result.append(f'\\begin{{xltabular}}{{\\linewidth}}{new_colspec}\n')
            result.append(body)
            result.append('\\end{xltabular}')
        else:
            # No X columns — keep as longtable
            result.append(tex_content[start:colspec_end + 1])
            result.append(body)
            result.append('\\end{longtable}')

        i = body_end + len(r'\end{longtable}')

    return ''.join(result)


def main():
    if len(sys.argv) < 2:
        print("Usage: fix_tables.py file.tex [file2.tex ...]", file=sys.stderr)
        sys.exit(1)

    for path in sys.argv[1:]:
        with open(path, 'r') as f:
            content = f.read()

        fixed = fix_tables(content)
        fixed = fix_tabularx_endhead(fixed)

        with open(path, 'w') as f:
            f.write(fixed)

        print(f"  Fixed tables in {path}")


if __name__ == '__main__':
    main()